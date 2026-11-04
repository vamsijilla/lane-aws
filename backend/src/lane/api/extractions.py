"""Extraction results, facts with evidence, and the escalation approval."""

import re
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from lane.api.auth import ContentViewer, SpendApprover, ViewerDep
from lane.api.deps import SessionDep, SettingsDep
from lane.api.models import CheckOut
from lane.api.privacy import Masker, masker_for
from lane.db.models import Document, DocumentExtraction, Fact, Submission
from lane.domain import normalize as nz
from lane.extract.service import approve_escalations, pending_escalations
from lane.pipeline import background as work

router = APIRouter(prefix="/api/v1", tags=["extraction"])


class ReadOut(BaseModel):
    by: str  # a model, or "code" for the second read
    value: str | None


class CoverageOut(BaseModel):
    """The catalog's expected fields for a document: how many were found and verified."""

    expected: int
    found: int
    verified: int
    missing: list[str]
    decision_expected: int
    decision_unverified: list[str]


class ExtractionOut(BaseModel):
    document_id: str
    doc_type: str
    title: str
    status: str
    method: str
    model: str | None
    facts: int
    checks: list[CheckOut]
    escalation_reasons: list[str]
    escalation_cost_usd: float | None
    data: dict[str, Any] | None
    # Extraction v2
    extractor: str  # "1" or "2"
    auto_escalated: bool  # re-read with the stronger model without asking
    coverage: CoverageOut | None  # expected fields from the catalog: found, verified, missing
    cost_usd: float | None
    latency_ms: int | None


class FactOut(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    id: str
    path: str
    value: str | None
    value_norm: Any
    page_id: str | None
    page_no: int | None
    bbox: list[float] | None
    cell_ref: str | None
    quote: str | None
    method: str
    grounding: str
    confidence: str
    confidence_reasons: list[str]
    # Extraction v2: how the value was verified, in words; what code and each model read
    tier: str | None = None
    verified: bool = False
    verification: str | None = None
    second_read: str | None = None
    reads: list[ReadOut] = Field(default_factory=lambda: list[ReadOut]())


class EscalationItem(BaseModel):
    document_id: str
    title: str
    reasons: list[str]
    estimated_cost_usd: float | None


class Escalations(BaseModel):
    model: str
    pending: list[EscalationItem]
    estimated_cost_usd: float


def _submission(session: SessionDep, submission_id: str) -> None:
    if session.get(Submission, submission_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")


@router.get("/submissions/{submission_id}/extractions")
def list_extractions(
    submission_id: str, session: SessionDep, settings: SettingsDep, viewer: ViewerDep
) -> list[ExtractionOut]:
    _submission(session, submission_id)
    mask = masker_for(session, settings, submission_id)
    counts = dict(
        session.execute(
            select(Fact.document_id, func.count())
            .where(Fact.submission_id == submission_id)
            .group_by(Fact.document_id)
        ).all()
    )
    rows = session.execute(
        select(DocumentExtraction, Document)
        .join(Document, Document.id == DocumentExtraction.document_id)
        .where(DocumentExtraction.submission_id == submission_id)
        .order_by(Document.ordinal)
    ).all()
    return [
        ExtractionOut(
            document_id=x.document_id,
            doc_type=x.doc_type,
            title=d.title,
            status=x.status.value,
            method=x.method,
            model=x.model,
            facts=counts.get(x.document_id, 0),
            checks=[CheckOut.model_validate(c) for c in x.checks],
            escalation_reasons=x.escalation_reasons,
            escalation_cost_usd=x.escalation_cost_usd,
            # Extracted content is for roles that may see document content, and masked even then.
            data=mask.tree(x.data) if viewer.can("view_content") else None,
            extractor=x.extractor_version,
            auto_escalated=x.auto_escalated,
            coverage=CoverageOut.model_validate(x.coverage) if x.coverage else None,
            cost_usd=x.cost_usd,
            latency_ms=x.latency_ms,
        )
        for x, d in rows
    ]


_SHEET_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[ T]00:00:00(?:\.0+)?)?$")


def as_printed(text: Any) -> Any:
    """A worksheet date cell as a person sees it ('Mar 1, 2021'), never a stored timestamp."""
    if isinstance(text, str) and (m := _SHEET_DATE.match(text.strip())):
        return nz.long_date(m.group(1))
    return text


def fact_out(f: Fact, mask: Masker) -> FactOut:
    pii = mask.attribute(f.path)

    def shown(text: Any) -> Any:
        """Masked by attribute when the fact is personal data, then scrubbed of any identifier
        a row snippet carries; dates in worksheet form shown as dates."""
        scrubbed = mask.text(text)
        if pii is not None and scrubbed == text:
            return mask.value(pii, text)
        return as_printed(scrubbed)

    return FactOut(
        id=f.id,
        path=f.path,
        value=shown(f.value_raw),
        value_norm=mask.value(pii, f.value_norm) if pii else f.value_norm,
        page_id=f.page_id,
        page_no=f.page_no,
        bbox=f.bbox,
        cell_ref=f.cell_ref,
        quote=shown(f.quote),
        method=f.method,
        grounding=f.grounding,
        confidence=f.confidence.value,
        confidence_reasons=[str(mask.text(r)) for r in f.confidence_reasons],
        tier=f.tier,
        verified=bool(f.verified),
        verification=mask.text(f.verification),
        second_read=shown(f.second_read),
        reads=[ReadOut(by=str(r.get("by")), value=shown(r.get("value"))) for r in f.reads or []],
    )


@router.get("/documents/{document_id}/facts")
def list_facts(
    document_id: str,
    session: SessionDep,
    settings: SettingsDep,
    _viewer: ContentViewer,
) -> list[FactOut]:
    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    mask = masker_for(session, settings, doc.submission_id)
    facts = session.scalars(
        select(Fact).where(Fact.document_id == document_id).order_by(Fact.page_no, Fact.path)
    ).all()
    return [fact_out(f, mask) for f in facts]


@router.get("/submissions/{submission_id}/escalations")
def get_escalations(submission_id: str, session: SessionDep, settings: SettingsDep) -> Escalations:
    _submission(session, submission_id)
    pending = pending_escalations(session, submission_id)
    titles = {
        d.id: d.title
        for d in session.scalars(
            select(Document).where(Document.id.in_([p.document_id for p in pending]))
        )
    }
    items = [
        EscalationItem(
            document_id=p.document_id,
            title=titles.get(p.document_id, ""),
            reasons=p.escalation_reasons,
            estimated_cost_usd=p.escalation_cost_usd,
        )
        for p in pending
    ]
    return Escalations(
        model=settings.llm_escalation_model,
        pending=items,
        estimated_cost_usd=round(sum(i.estimated_cost_usd or 0 for i in items), 4),
    )


class EscalationApproved(BaseModel):
    approved: list[str]


@router.post(
    "/submissions/{submission_id}/escalations/approve",
    status_code=202,
    operation_id="approve_escalations",
)
def approve(
    submission_id: str,
    session: SessionDep,
    background: BackgroundTasks,
    viewer: SpendApprover,
) -> EscalationApproved:
    """A person approves re-reading every pending document with the stronger model."""
    _submission(session, submission_id)
    approved = approve_escalations(session, submission_id, actor=viewer.actor)
    if approved:
        work.start(background, session, "process", submission_id, trigger="escalation")
    return EscalationApproved(approved=approved)
