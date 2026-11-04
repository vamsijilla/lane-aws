"""Typed shapes for JSON the pipeline stores, so the generated client covers every payload the
UI reads. Each model mirrors one stored structure; validation fails loudly if they drift."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from lane.api.display import dates_in


class Out(BaseModel):
    """A response model: fields with defaults are still always sent, so the schema says so."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class SourceOut(Out):
    document_id: str
    doc_type: str
    title: str
    fact_ids: list[str] = Field(default_factory=lambda: list[str]())
    as_of: str | None = None
    note: str | None = None
    verified: bool = True  # False: not found on the page it was read from, so it decides nothing
    locations: list[str] = Field(default_factory=lambda: list[str]())  # "page 2", "cell E50"
    # The weakest band among the facts it was read from, and why; none for a derived value
    confidence: str | None = None
    confidence_reasons: list[str] = Field(default_factory=lambda: list[str]())

    @field_validator("title")
    @classmethod
    def _readable(cls, v: str) -> str:
        return dates_in(v) or v


class CandidateOut(Out):
    """One value a field could take, with every document that states it."""

    display: str | None
    value: Any
    sources: list[SourceOut] = Field(default_factory=lambda: list[SourceOut]())
    superseded: str | None = None  # why a newer record replaces this value


class RefOut(Out):
    """What a citation points at, for the evidence lens."""

    kind: str  # field | entity | conflict | missing | rule | figure
    label: str
    target: str
    fact_ids: list[str] = Field(default_factory=lambda: list[str]())
    document_ids: list[str] = Field(default_factory=lambda: list[str]())
    cites: list[str] = Field(default_factory=lambda: list[str]())
    value: str | None = None  # a figure's result and how it was worked out

    @field_validator("label")
    @classmethod
    def _readable(cls, v: str) -> str:
        return dates_in(v) or v

    @field_validator("value")
    @classmethod
    def _readable_value(cls, v: str | None) -> str | None:
        return (dates_in(v) or v) if v else v


class FootnoteOut(RefOut):
    n: int
    ref: str


class SentenceOut(Out):
    text: str
    footnotes: list[int]


class RejectedOut(Out):
    """A finding the checks threw out, and why."""

    title: str
    reason: str


class SensitivityOut(Out):
    """The rule's outcome if a conflicted field took this candidate value."""

    field_id: str
    field: str
    value: str | None
    outcome: str
    result: str | None = None  # what the value leads to, when not an outcome ("Small Fleet")
    # Whose value it is, when the field belongs to someone or something specific (a driver,
    # a policy term); none for the one applicant, fleet or operations
    subject: str | None = None
    subject_kind: str | None = None


class ReadingOut(Out):
    """Another way to read the guideline line, and where it leads."""

    id: str
    label: str
    assumption: str | None
    plausible: bool = True  # an implausible reading only flags (A03)
    question: str | None = None
    outcome: str
    reason: str
    items: list[str] = Field(default_factory=lambda: list[str]())


class CheckOut(Out):
    """An arithmetic or consistency check on one extraction."""

    name: str
    passed: bool
    detail: str
    paths: list[str] = Field(default_factory=lambda: list[str]())


_BANDS = ("low", "medium", "high")


def candidates(
    raw: list[dict[str, Any]],
    locations: dict[str, str] | None = None,
    confidence: dict[str, tuple[str, list[str]]] | None = None,
) -> list[CandidateOut]:
    """With `locations` (fact id -> "page 2"), each source lists where in its document it was
    read, once per place. With `confidence` (fact id -> (band, reasons)), each source carries
    its weakest fact's band and the reasons for it."""
    out = [CandidateOut.model_validate(c) for c in raw]
    for c in out:
        for s in c.sources:
            if locations:
                s.locations = list(
                    dict.fromkeys(locations[f] for f in s.fact_ids if f in locations)
                )
            known = [confidence[f] for f in s.fact_ids if confidence and f in confidence]
            if known:
                band = min((b for b, _ in known), key=_BANDS.index)
                s.confidence = band
                s.confidence_reasons = list(
                    dict.fromkeys(r for b, rs in known if b == band for r in rs)
                )
    return out


def fact_locations(rows: list[tuple[str, int | None, str | None]]) -> dict[str, str]:
    """(fact id, page, cell) -> 'cell App!E50' or 'page 2'."""
    return {
        fid: f"cell {cell}" if cell else f"page {page}"
        for fid, page, cell in rows
        if cell or page is not None
    }


class AnalystNoteOut(Out):
    """Text the analyst saw that tried to give it instructions, and where."""

    text: str
    where: str


def sentences(raw: list[dict[str, Any]]) -> list[SentenceOut]:
    return [SentenceOut.model_validate(s) for s in raw]


def footnotes(raw: list[dict[str, Any]]) -> list[FootnoteOut]:
    return [FootnoteOut.model_validate(f) for f in raw]


class ResolutionOut(Out):
    """Who settled a conflict, with which value and why."""

    display: str | None
    reason: str
    actor: str
    at: str
    # Settled by a document the agent sent (an exclusion letter), until a person confirms it
    pending: bool = False


class EvalFiguresOut(Out):
    pages_classified: int = 0
    pages_by_layout: int = 0  # typed by a known layout, with no model call
    pages_by_tier1: int = 0  # typed by deterministic tier 1 signals
    layout_audited: int = 0  # of those, also read by the model for audit
    layout_audit_agreed: int = 0
    layout_memory: bool = True  # known layouts typed pages in this run
    facts: int
    grounding_rate: float | None
    findings: int
    findings_by_kind: dict[str, int]
    findings_cited: bool
    cost_usd_by_purpose: dict[str, float]
