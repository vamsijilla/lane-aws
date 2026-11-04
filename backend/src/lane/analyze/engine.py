"""The analyst: expert findings and a cited summary over the brief, validated in code.

The model may only cite refs that exist in the brief. Findings without a valid citation are
rejected; the model is asked once more with the problems listed, and anything still invalid is
dropped and recorded. AI output here explains and suggests; it never changes a rule outcome.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lane.analyze.context import Brief, Ref
from lane.config import Settings
from lane.domain.order import natural_key
from lane.llm.gateway import GatewayFactory, LlmRequest, LlmResult, LlmUnavailable, TextPart
from lane.llm.prompts import Prompt, load_prompt

PROMPT = "analyze_submission"
DEGRADED_MESSAGE = "AI analysis unavailable for this run. Rules and extracted data are complete."

Band = Literal["high", "medium", "low"]


class Sentence(BaseModel):
    text: str
    citations: list[str]


class FindingOut(BaseModel):
    title: str
    body: str
    kind: Literal["observation", "hypothesis", "recommendation"]
    severity: Band
    confidence: Band
    basis: str
    citations: list[str]


class SecurityNote(BaseModel):
    text: str = Field(description="The instruction-like text, quoted briefly")
    where: str = Field(description="Which ref or part of the brief it appeared in")


class AnalystOutput(BaseModel):
    summary: list[Sentence]
    findings: list[FindingOut]
    security_notes: list[SecurityNote]


@dataclass
class Finding:
    title: str
    body: str
    kind: str
    severity: str
    confidence: str
    basis: str
    citations: list[str]
    dropped_citations: list[str] = field(default_factory=lambda: list[str]())


@dataclass
class Analysis:
    status: str  # ok | degraded
    summary: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    footnotes: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    findings: list[Finding] = field(default_factory=lambda: list[Finding]())
    rejected: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    security_notes: list[dict[str, str]] = field(default_factory=lambda: list[dict[str, str]]())
    attempts: int = 0
    model: str | None = None
    prompt: Prompt | None = None
    cost_usd: float = 0.0
    call_ids: list[str] = field(default_factory=lambda: list[str]())
    error: str | None = None


_REF = re.compile(r"^(?:[FECMX]\d+|R:[A-Z]+-\d+)$")
# The analyst may suggest; it may not decide. Checked in code, not only in the prompt.
_DECIDING = re.compile(
    r"\b(approve|bind|accept|decline|reject)\s+(the|this)\s+(risk|account|submission|policy)\b",
    re.IGNORECASE,
)
_SEVERITY = {"high": 0, "medium": 1, "low": 2}


def normalize_ref(raw: str) -> str:
    ref = raw.strip().strip("[]()").upper().replace(" ", "")
    if re.fullmatch(r"[A-Z]+-\d+", ref):
        ref = f"R:{ref}"
    return ref


@dataclass
class Check:
    findings: list[Finding]
    rejected: list[dict[str, Any]]
    summary: list[tuple[str, list[str]]]
    problems: list[str]


def check(output: AnalystOutput, refs: dict[str, Ref], settings: Settings) -> Check:
    """Validate every citation and claim against the brief. Returns what passed and why the
    rest did not, worded so the model can fix it."""
    findings: list[Finding] = []
    rejected: list[dict[str, Any]] = []
    problems: list[str] = []
    for i, f in enumerate(output.findings, start=1):
        cited = [normalize_ref(c) for c in f.citations]
        valid = list(dict.fromkeys(c for c in cited if _REF.match(c) and c in refs))
        dropped = [c for c in cited if c not in valid]
        reason: str | None = None
        if not valid:
            reason = "no valid citation" + (
                f" ({', '.join(dropped)} not in the brief)" if dropped else ""
            )
        elif _DECIDING.search(f"{f.title} {f.body}"):
            reason = "tells the underwriter to approve, bind or decline"
        elif f.kind == "hypothesis" and not f.basis.strip():
            reason = "hypothesis without a basis"
        if reason:
            rejected.append({"title": f.title, "reason": reason})
            problems.append(f'Finding {i} "{f.title}": {reason}.')
            continue
        if dropped:
            problems.append(
                f'Finding {i} "{f.title}": refs {", ".join(dropped)} are not in the brief.'
            )
        findings.append(
            Finding(
                f.title.strip(),
                f.body.strip(),
                f.kind,
                f.severity,
                f.confidence,
                f.basis.strip(),
                valid,
                dropped,
            )
        )

    summary: list[tuple[str, list[str]]] = []
    for i, s in enumerate(output.summary, start=1):
        valid = list(dict.fromkeys(c for c in map(normalize_ref, s.citations) if c in refs))
        if not valid:
            problems.append(f"Summary sentence {i} has no valid citation.")
            continue
        summary.append((s.text.strip(), valid))
    low, high = settings.analyze_summary_sentences
    if not low <= len(output.summary) <= high:
        problems.append(f"The summary has {len(output.summary)} sentences; write {low} to {high}.")
    findings.sort(
        key=lambda f: (
            _SEVERITY.get(f.severity, 3),
            f.kind == "recommendation",  # what is wrong before what to do about it
            natural_key(f.title),
        )
    )
    return Check(findings, rejected, summary, problems)


def footnoted(
    summary: list[tuple[str, list[str]]], refs: dict[str, Ref]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Number each distinct citation once, in reading order."""
    numbers: dict[str, int] = {}
    notes: list[dict[str, Any]] = []
    sentences: list[dict[str, Any]] = []
    for text, cites in summary:
        ns: list[int] = []
        for c in cites:
            if c not in numbers:
                numbers[c] = len(numbers) + 1
                r = refs[c]
                notes.append(
                    {
                        "n": numbers[c],
                        "ref": c,
                        "kind": r.kind,
                        "label": r.label,
                        "target": r.target,
                        "fact_ids": r.fact_ids[:20],
                        "document_ids": r.document_ids[:10],
                        "cites": r.cites,
                        "value": r.value,
                    }
                )
            ns.append(numbers[c])
        sentences.append({"text": text, "footnotes": ns})
    return sentences, notes


def build_request(
    settings: Settings, prompt: Prompt, parts: list[TextPart], subject: str
) -> LlmRequest[AnalystOutput]:
    return LlmRequest(
        purpose="analyze",
        subject_id=subject,
        model=settings.llm_analyze_model,
        effort=settings.llm_analyze_effort,
        prompt=prompt,
        parts=parts,
        output_type=AnalystOutput,
        max_tokens=settings.llm_analyze_max_tokens,
    )


def estimate_usd(brief: Brief, settings: Settings) -> float:
    """A rough cost for one analyst call: about 3.5 characters per token in, a typical answer out."""
    prompt = load_prompt(PROMPT)
    price_in, price_out = settings.llm_prices.get(settings.llm_analyze_model, (0.0, 0.0))
    tokens_in = (len(brief.text()) + len(prompt.text)) / 3.5
    return round(
        (tokens_in * price_in + settings.analyze_expected_output_tokens * price_out) / 1_000_000, 2
    )


def first_request(
    submission_id: str, brief: Brief, settings: Settings
) -> LlmRequest[AnalystOutput]:
    return build_request(
        settings,
        load_prompt(PROMPT),
        [TextPart(brief.text(), label="submission brief")],
        submission_id,
    )


def analyze(
    session: Session,
    submission_id: str,
    brief: Brief,
    settings: Settings,
    gateway_factory: GatewayFactory,
    *,
    run_id: str | None,
) -> Analysis:
    prompt = load_prompt(PROMPT)
    result = Analysis(status="degraded", model=settings.llm_analyze_model, prompt=prompt)
    try:
        gateway = gateway_factory(settings)
    except LlmUnavailable as exc:
        result.error = f"{DEGRADED_MESSAGE} ({exc})"
        return result

    parts = [TextPart(brief.text(), label="submission brief")]
    checked: Check | None = None
    last: LlmResult[AnalystOutput] | None = None
    for attempt in range(settings.analyze_retries + 1):
        [last] = gateway.run_many(
            session, [build_request(settings, prompt, parts, submission_id)], run_id=run_id
        )
        result.attempts = attempt + 1
        result.cost_usd = round(result.cost_usd + last.call.cost_usd, 6)
        result.call_ids.append(last.call.id)
        if last.output is None:
            break
        checked = check(last.output, brief.refs, settings)
        if not checked.problems:
            break
        parts = [
            parts[0],
            TextPart(
                "Your previous answer:\n"
                + json.dumps(last.output.model_dump(mode="json"), ensure_ascii=False)
                + "\n\nIt had these problems:\n- "
                + "\n- ".join(checked.problems)
                + "\n\nReturn the complete corrected answer. Cite only refs that appear in the brief."
            ),
        ]

    if last is None or last.output is None or checked is None:
        result.error = f"{DEGRADED_MESSAGE} ({last.error if last else 'no call made'})"
        return result

    result.status = "ok"
    result.findings = checked.findings
    result.rejected = checked.rejected
    result.summary, result.footnotes = footnoted(checked.summary, brief.refs)
    result.security_notes = [{"text": n.text, "where": n.where} for n in last.output.security_notes]
    if checked.problems:
        result.error = f"{len(checked.problems)} problems remained after {result.attempts} attempts; invalid items were dropped"
    return result
