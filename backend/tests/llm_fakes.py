"""A scripted transport so tests exercise the gateway and pipeline without network calls."""

from collections.abc import Callable
from typing import Any

from lane.config import Settings
from lane.llm.gateway import LlmGateway, LlmRequest, TransportResponse

Responder = Callable[[LlmRequest[Any], str], dict[str, Any] | TransportResponse | Exception]


class FakeTransport:
    """Calls `respond(request, all_text_in_content)`; returns its dict as the parsed output."""

    def __init__(self, respond: Responder) -> None:
        self.respond = respond
        self.calls: list[tuple[LlmRequest[Any], list[dict[str, Any]], bool]] = []

    def __call__(
        self, request: LlmRequest[Any], content: list[dict[str, Any]], *, fallbacks: bool
    ) -> TransportResponse:
        self.calls.append((request, content, fallbacks))
        text = "\n".join(b["text"] for b in content if b["type"] == "text")
        out = self.respond(request, text)
        if isinstance(out, Exception):
            raise out
        if isinstance(out, TransportResponse):
            return out
        return TransportResponse(
            parsed=out, stop_reason="end_turn", input_tokens=1000, output_tokens=100
        )


def factory(transport: FakeTransport) -> Callable[[Settings], LlmGateway]:
    return lambda settings: LlmGateway(settings, transport=transport)


def verdict(doc_type: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "doc_type": doc_type,
        "starts_new_document": True,
        "subject_name": None,
        "subject_id": None,
        "period": None,
        "document_date": None,
        "title": doc_type.replace("_", " ").capitalize(),
        "confidence": "high",
        "evidence": [],
    }
    base.update(overrides)
    return base
