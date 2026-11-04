"""The one door to language models.

Every call goes through here: typed request in, schema-validated output out, recorded with
model, prompt version, tokens, cost and latency. Outputs are cached by
(model, effort, prompt hash, schema, input hash), so a rerun with the same inputs and versions
reproduces the same result and costs nothing.

Document content is wrapped in tagged blocks and the system prompt says it is data, not
instructions. Network calls run in a small thread pool; all database work stays on the caller's
thread.
"""

import base64
import hashlib
import io
import json
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

import anthropic
import structlog
from anthropic.types import Message
from anthropic.types.beta import BetaMessage
from PIL import Image
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from lane.config import Settings
from lane.db.models import LlmCacheEntry, LlmCall
from lane.llm.prompts import Prompt

log = structlog.get_logger()

# Models with server-side refusal fallback (docs: claude-api skill, refusal fallbacks).
FALLBACK_MODELS = frozenset(
    {"claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-fable-5-1"}
)
FALLBACK_BETA = "server-side-fallback-2026-07-01"


NOT_CACHED = "Not in the cache: a replay never calls a model"


class LlmUnavailable(RuntimeError):
    """No credentials or model access; callers fall back to deterministic paths."""


@dataclass(frozen=True)
class TextPart:
    text: str
    label: str | None = None  # wraps the text as untrusted document content when set


@dataclass(frozen=True)
class ImagePart:
    png: bytes
    label: str


type Part = TextPart | ImagePart


@dataclass(frozen=True)
class LlmRequest[T: BaseModel]:
    purpose: str
    subject_id: str | None
    model: str
    effort: str
    prompt: Prompt
    parts: Sequence[Part]
    output_type: type[T]
    max_tokens: int = 4000
    # Extra cache key material: v2 extraction keys on the document, the field catalog's version
    # and the extractor's version, so a change to any of them is a new read. Empty keeps the
    # key as it was (v1 entries stay valid).
    cache_salt: str = ""


@dataclass
class TransportResponse:
    parsed: dict[str, Any] | None
    stop_reason: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    request_id: str | None = None
    served_by: str | None = None
    error: str | None = None


class Transport(Protocol):
    def __call__(
        self, request: LlmRequest[Any], content: list[dict[str, Any]], *, fallbacks: bool
    ) -> TransportResponse: ...


@dataclass
class LlmResult[T: BaseModel]:
    request: LlmRequest[T]
    output: T | None
    call: LlmCall
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.output is not None


@dataclass
class GatewayStats:
    calls: int = 0
    cache_hits: int = 0
    failures: int = 0
    cost_usd: float = 0.0
    by_status: dict[str, int] = field(default_factory=lambda: dict[str, int]())


class LlmGateway:
    def __init__(self, settings: Settings, transport: Transport | None = None) -> None:
        self.settings = settings
        if transport is None:
            if not settings.has_llm_key:
                raise LlmUnavailable("ANTHROPIC_API_KEY is not set")
            transport = AnthropicTransport(settings)
        self.transport = transport
        self.stats = GatewayStats()

    def cached(self, session: Session, request: LlmRequest[Any]) -> bool:
        """Whether this exact request has an answer already, so running it costs nothing."""
        return session.get(LlmCacheEntry, cache_key(request, self._content(request))) is not None

    def run_many[T: BaseModel](
        self, session: Session, requests: Sequence[LlmRequest[T]], *, run_id: str | None
    ) -> list[LlmResult[T]]:
        prepared = [(req, self._content(req)) for req in requests]
        keys = [cache_key(req, content) for req, content in prepared]
        results: list[LlmResult[T] | None] = [None] * len(requests)

        misses: list[int] = []
        for i, (req, _content) in enumerate(prepared):
            hit = session.get(LlmCacheEntry, keys[i])
            if hit is None:
                misses.append(i)
                continue
            call = self._call_record(req, keys[i], run_id, cache_hit=True, status="ok")
            # Re-apply the blank rules so cached answers follow the current wire conventions.
            output = req.output_type.model_validate(blank_to_none(req.output_type, hit.output))
            results[i] = LlmResult(req, output, call)

        def send(i: int) -> tuple[TransportResponse, int]:
            req, content = prepared[i]
            started = time.perf_counter()
            try:
                response = self.transport(req, content, fallbacks=self._fallbacks(req.model))
            except Exception as exc:  # one bad call must never sink the batch
                response = TransportResponse(None, None, error=f"{type(exc).__name__}: {exc}")
            return response, round((time.perf_counter() - started) * 1000)

        if self.settings.llm_cache_only:  # replay: a miss is recorded, never sent
            responses = [(TransportResponse(None, None, error=NOT_CACHED), 0) for _ in misses]
        else:
            workers = max(1, min(self.settings.llm_max_concurrency, len(misses)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                responses = list(pool.map(send, misses))

        for i, (response, latency_ms) in zip(misses, responses, strict=True):
            req = prepared[i][0]
            results[i] = self._finish(session, req, keys[i], run_id, response, latency_ms)

        for result in results:
            assert result is not None
            session.add(result.call)
            self._count(result)
            c = result.call
            # One line per call, for the spend alarm (a log metric summing cost_usd)
            log.info(
                "llm.call",
                purpose=c.purpose,
                model=c.model,
                cache_hit=c.cache_hit,
                status=c.status,
                cost_usd=c.cost_usd,
                latency_ms=c.latency_ms,
            )
        session.flush()
        for i, result in enumerate(results):
            assert result is not None
            if result.ok and not result.call.cache_hit:
                session.merge(
                    LlmCacheEntry(
                        cache_key=keys[i],
                        model=result.request.model,
                        output=result.output.model_dump(mode="json"),  # type: ignore[union-attr]
                        source_call_id=result.call.id,
                    )
                )
        session.flush()
        return [r for r in results if r is not None]

    # --- internals ------------------------------------------------------------------------

    def _fallbacks(self, model: str) -> bool:
        return self.settings.llm_refusal_fallbacks and model in FALLBACK_MODELS

    def _content(self, req: LlmRequest[Any]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for part in req.parts:
            if isinstance(part, ImagePart):
                blocks.append({"type": "text", "text": f"Image: {part.label}"})
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.standard_b64encode(
                                fit_image(part.png, self.settings.llm_image_max_px)
                            ).decode(),
                        },
                    }
                )
            elif part.label:
                blocks.append({"type": "text", "text": wrap_untrusted(part.label, part.text)})
            else:
                blocks.append({"type": "text", "text": part.text})
        return blocks

    def _call_record(
        self,
        req: LlmRequest[Any],
        key: str,
        run_id: str | None,
        *,
        cache_hit: bool,
        status: str,
    ) -> LlmCall:
        return LlmCall(
            run_id=run_id,
            purpose=req.purpose,
            subject_id=req.subject_id,
            model=req.model,
            prompt_name=req.prompt.name,
            prompt_version=req.prompt.version,
            prompt_sha256=req.prompt.sha256,
            cache_key=key,
            cache_hit=cache_hit,
            status=status,
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            cost_usd=0.0,
            latency_ms=0,
        )

    def _finish[T: BaseModel](
        self,
        session: Session,
        req: LlmRequest[T],
        key: str,
        run_id: str | None,
        r: TransportResponse,
        latency_ms: int,
    ) -> LlmResult[T]:
        output: T | None = None
        error = r.error
        status = "error" if r.error else "ok"
        if not r.error:
            if r.stop_reason == "refusal":
                status, error = "refused", "The model declined this request"
            elif r.parsed is None:
                status, error = "invalid", f"No structured output (stop reason {r.stop_reason})"
            else:
                try:
                    output = req.output_type.model_validate(
                        blank_to_none(req.output_type, r.parsed)
                    )
                except ValidationError as exc:
                    first = exc.errors()[0]
                    where = ".".join(str(x) for x in first["loc"])
                    status, error = (
                        "invalid",
                        f"Output failed validation ({exc.error_count()} errors; "
                        f"first at {where}: {first['msg']})",
                    )
        call = self._call_record(req, key, run_id, cache_hit=False, status=status)
        call.stop_reason = r.stop_reason
        call.input_tokens = r.input_tokens
        call.output_tokens = r.output_tokens
        call.cache_read_tokens = r.cache_read_tokens
        call.cache_write_tokens = r.cache_write_tokens
        call.cost_usd = self._cost(r.served_by or req.model, r)
        call.latency_ms = latency_ms
        call.request_id = r.request_id
        call.served_by = r.served_by
        call.error = error
        return LlmResult(req, output, call, error)

    def _cost(self, model: str, r: TransportResponse) -> float:
        prices = self.settings.llm_prices.get(model)
        if prices is None:
            return 0.0
        price_in, price_out = prices
        dollars = (
            r.input_tokens * price_in
            + r.cache_read_tokens * price_in * 0.1
            + r.cache_write_tokens * price_in * 1.25
            + r.output_tokens * price_out
        ) / 1_000_000
        return round(dollars, 6)

    def _count(self, result: LlmResult[Any]) -> None:
        s = self.stats
        s.calls += 1
        s.cache_hits += int(result.call.cache_hit)
        s.failures += int(not result.ok)
        s.cost_usd = round(s.cost_usd + result.call.cost_usd, 6)
        s.by_status[result.call.status] = s.by_status.get(result.call.status, 0) + 1


class AnthropicTransport:
    """Calls the Messages API with structured output. SDK retries cover 429, 5xx and network."""

    def __init__(self, settings: Settings) -> None:
        key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
        self.client = anthropic.Anthropic(
            api_key=key, timeout=settings.llm_timeout_s, max_retries=settings.llm_max_retries
        )

    def __call__(
        self, request: LlmRequest[Any], content: list[dict[str, Any]], *, fallbacks: bool
    ) -> TransportResponse:
        schema = wire_schema(request.output_type)
        try:
            message = self._send(request, content, schema, fallbacks=fallbacks, constrained=True)
        except anthropic.BadRequestError as exc:
            if GRAMMAR_TOO_LARGE not in str(exc.message):
                return TransportResponse(None, None, error=f"API error 400: {exc.message}")
            # The schema is too big for constrained decoding. Ask for the same JSON in the prompt
            # instead; strict validation still runs on the answer.
            try:
                message = self._send(
                    request, content, schema, fallbacks=fallbacks, constrained=False
                )
            except anthropic.APIStatusError as inner:
                return _api_error(inner)
            except anthropic.APIConnectionError as inner:
                return TransportResponse(None, None, error=f"Connection error: {inner}")
        except anthropic.APIStatusError as exc:
            return _api_error(exc)
        except anthropic.APIConnectionError as exc:
            return TransportResponse(None, None, error=f"Connection error: {exc}")

        usage = message.usage
        text = next((b.text for b in message.content if b.type == "text"), None)
        parsed: dict[str, Any] | None = None
        error: str | None = None
        if text is not None and message.stop_reason != "refusal":
            try:
                loaded: Any = json.loads(_strip_fences(text))
                parsed = cast(dict[str, Any], loaded) if isinstance(loaded, dict) else None
            except json.JSONDecodeError:
                error = "The model returned malformed JSON"
        return TransportResponse(
            parsed=parsed,
            stop_reason=message.stop_reason,
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cache_read_tokens=usage.cache_read_input_tokens or 0,
            cache_write_tokens=usage.cache_creation_input_tokens or 0,
            request_id=getattr(message, "_request_id", None),
            served_by=message.model if message.model != request.model else None,
            error=error,
        )

    def _send(
        self,
        request: LlmRequest[Any],
        content: list[dict[str, Any]],
        schema: dict[str, Any],
        *,
        fallbacks: bool,
        constrained: bool,
    ) -> Message | BetaMessage:
        messages: Any = [{"role": "user", "content": content}]
        system = request.prompt.text
        output_config: Any = {"effort": request.effort}
        if constrained:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        else:
            system += (
                "\n\nAnswer with one JSON object and nothing else, matching this JSON schema. "
                'Use "" for any string that is not shown.\n'
                + json.dumps(schema, separators=(",", ":"))
            )
        if fallbacks:
            return self.client.beta.messages.create(
                model=request.model,
                max_tokens=request.max_tokens,
                system=system,
                messages=messages,
                output_config=output_config,
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        return self.client.messages.create(
            model=request.model,
            max_tokens=request.max_tokens,
            system=system,
            messages=messages,
            output_config=output_config,
        )


GRAMMAR_TOO_LARGE = "compiled grammar is too large"
# Ways a model writes "not shown" in a string field. Only applied where the field is nullable.
BLANKS = frozenset({"", "null", "none"})


def _api_error(exc: anthropic.APIStatusError) -> TransportResponse:
    return TransportResponse(None, None, error=f"API error {exc.status_code}: {exc.message}")


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return t.strip()


def wire_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The JSON schema sent to the API for constrained decoding.

    Every optional field is costly for the decoder's grammar, and large documents (loss runs,
    applications) exceed its limit. On the wire, every field is required and nullable strings
    become plain strings where "" means "not shown". `blank_to_none` maps them back before the
    Pydantic model validates the answer, so code still sees None.
    """
    return _simplify(model.model_json_schema())


def _simplify(node: Any) -> Any:
    if isinstance(node, list):
        return [_simplify(n) for n in cast(list[Any], node)]
    if not isinstance(node, dict):
        return node
    src = cast(dict[str, Any], node)
    any_of = src.get("anyOf")
    if isinstance(any_of, list):
        options: list[Any] = [
            o for o in cast(list[Any], any_of) if not (_is_dict(o) and o.get("type") == "null")
        ]
        if len(options) == 1:
            merged = {k: v for k, v in src.items() if k != "anyOf"}
            merged.update(cast(dict[str, Any], options[0]))
            return _simplify(merged)
    out: dict[str, Any] = {}
    for key, value in src.items():
        if key in ("properties", "$defs") and isinstance(value, dict):
            # Keys here are field and type names, not schema keywords: never filter them.
            named = cast(dict[str, Any], value)
            out[key] = {name: _simplify(v) for name, v in named.items()}
        elif key not in {"default", "title", "maxItems", "minItems", "maxLength", "minLength"}:
            out[key] = _simplify(value)
    if out.get("type") == "object" and isinstance(out.get("properties"), dict):
        out["required"] = list(cast(dict[str, Any], out["properties"]))
        out["additionalProperties"] = False
    return out


def _is_dict(node: Any) -> bool:
    return isinstance(node, dict)


def blank_to_none(model: type[BaseModel], data: Any) -> Any:
    """Map the wire format back: "" becomes None, but only where the model allows None."""
    schema = model.model_json_schema()
    defs = cast(dict[str, Any], schema.get("$defs", {}))
    return _restore(schema, data, defs)


def _deref(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        return cast(dict[str, Any], defs[ref.removeprefix("#/$defs/")])
    return node


def _restore(schema: dict[str, Any], value: object, defs: dict[str, Any]) -> object:
    data = cast(Any, value)
    schema = _deref(schema, defs)
    options = [_deref(cast(dict[str, Any], o), defs) for o in schema.get("anyOf", [])]
    nullable = any(o.get("type") == "null" for o in options)
    if nullable and isinstance(data, str) and data.strip().lower() in BLANKS:
        return None
    concrete = next((o for o in options if o.get("type") != "null"), schema) if options else schema
    if isinstance(concrete.get("properties"), dict) and (data is None or data == ""):
        data = {}  # an object answered with "" means "nothing here": all fields empty
    if isinstance(data, dict) and isinstance(concrete.get("properties"), dict):
        props = cast(dict[str, Any], concrete["properties"])
        return {
            k: _restore(cast(dict[str, Any], props[k]), v, defs) if k in props else v
            for k, v in cast(dict[str, Any], data).items()
        }
    if isinstance(data, list) and isinstance(concrete.get("items"), dict):
        items = cast(dict[str, Any], concrete["items"])
        return [_restore(items, v, defs) for v in cast(list[Any], data)]
    return value


def cache_key(req: LlmRequest[Any], content: list[dict[str, Any]]) -> str:
    """Stable across runs: images hashed rather than inlined, JSON canonicalized."""

    def stable(block: dict[str, Any]) -> dict[str, Any]:
        if block.get("type") == "image":
            data = block["source"]["data"]
            return {"type": "image", "sha256": hashlib.sha256(data.encode()).hexdigest()}
        return block

    material = {
        "model": req.model,
        "effort": req.effort,
        "max_tokens": req.max_tokens,
        "prompt": req.prompt.sha256,
        "schema": wire_schema(req.output_type),
        "content": [stable(b) for b in content],
    }
    if req.cache_salt:
        material["salt"] = req.cache_salt
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def wrap_untrusted(label: str, text: str) -> str:
    """Tag document content as data. The closing tag carries a nonce derived from the content,
    so text inside the document cannot close the block early."""
    nonce = hashlib.sha256(text.encode()).hexdigest()[:12]
    return (
        f'<document_content label="{label}" id="{nonce}">\n{text}\n</document_content id="{nonce}">'
    )


def fit_image(png: bytes, max_px: int) -> bytes:
    img = Image.open(io.BytesIO(png))
    if max(img.size) <= max_px:
        return png
    img.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


GatewayFactory = Callable[[Settings], LlmGateway]
