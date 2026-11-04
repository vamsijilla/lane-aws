"""Run extraction for a submission's documents and persist the results.

Cheap model by default. Documents that fail their checks are marked as needing escalation with
the reasons and an estimated cost. Nothing is re-read with the stronger model until a person
approves (EscalationApproval); approved documents then use the stronger model on every run.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from lane.audit import chain
from lane.config import Settings
from lane.db.models import (
    Blob,
    Document,
    DocumentExtraction,
    EscalationApproval,
    ExtractionStatus,
    Fact,
    Page,
    PageKind,
    QualityBand,
)
from lane.documents.layout import load_layout
from lane.extract import engine_v2 as v2
from lane.extract.engine import (
    EXTRACTOR_VERSION,
    DocInput,
    DocPage,
    ExtractionOutcome,
    build_request,
    escalation_cost,
    extractable,
    finish,
    page_text_for_extraction,
)
from lane.llm.gateway import LlmGateway, LlmRequest, LlmResult, LlmUnavailable
from lane.storage.blobs import BlobStore

ACTOR = "system:extract"


@dataclass
class ExtractSummary:
    statuses: Counter[str] = field(default_factory=lambda: Counter[str]())
    facts: int = 0
    text_facts: int = 0
    grounded_text_facts: int = 0
    vision_facts: int = 0
    ocr_confirmed: int = 0
    pending_escalations: list[dict[str, Any]] = field(
        default_factory=lambda: list[dict[str, Any]]()
    )
    needs_review: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    checks_passed: int = 0
    checks_failed: int = 0
    llm: dict[str, Any] = field(default_factory=lambda: dict[str, Any]())
    unavailable: str | None = None
    # Extraction v2
    decision_facts: int = 0
    decision_verified: int = 0
    auto_escalated: list[str] = field(default_factory=lambda: list[str]())
    strong_first: list[str] = field(default_factory=lambda: list[str]())  # stronger model first
    second_read: list[str] = field(default_factory=lambda: list[str]())  # then the cheaper one

    def as_dict(self) -> dict[str, Any]:
        rate = self.grounded_text_facts / self.text_facts if self.text_facts else None
        return {
            "statuses": dict(self.statuses),
            "facts": self.facts,
            "text_grounding_rate": None if rate is None else round(rate, 3),
            "ocr_confirmation_rate": round(self.ocr_confirmed / self.vision_facts, 3)
            if self.vision_facts
            else None,
            "checks": {"passed": self.checks_passed, "failed": self.checks_failed},
            "pending_escalations": self.pending_escalations,
            "escalation_cost_usd": round(
                sum(p["estimated_cost_usd"] for p in self.pending_escalations), 4
            ),
            "needs_review": self.needs_review,
            **(
                {
                    "decision_facts": self.decision_facts,
                    "decision_verified": self.decision_verified,
                    "auto_escalated": self.auto_escalated,
                    "strong_first": self.strong_first,
                    "second_read": self.second_read,
                }
                if self.decision_facts
                else {}
            ),
            "llm": self.llm,
            **({"unavailable": self.unavailable} if self.unavailable else {}),
        }


def load_input(session: Session, store: BlobStore, doc: Document) -> DocInput:
    pages = session.scalars(
        select(Page).where(Page.document_id == doc.id).order_by(Page.page_no)
    ).all()

    def blob(sha: str | None) -> bytes | None:
        if sha is None:
            return None
        b = session.get(Blob, sha)
        return store.get(b.storage_key) if b else None

    out: list[DocPage] = []
    for page in pages:
        raw_layout = blob(page.layout_sha256)
        layout = load_layout(raw_layout) if raw_layout else None
        email_text = None
        if page.kind is PageKind.EMAIL_BODY and page.attachment.blob is not None:
            email_text = store.get(page.attachment.blob.storage_key).decode("utf-8", "replace")
        image = (
            None if page.kind in (PageKind.SHEET, PageKind.EMAIL_BODY) else blob(page.render_sha256)
        )
        out.append(DocPage(page, layout, image, page_text_for_extraction(page, layout, email_text)))
    return DocInput(doc, out)


def extract_submission(
    session: Session,
    store: BlobStore,
    settings: Settings,
    submission_id: str,
    *,
    gateway_factory: Any,
    run_id: str | None,
) -> ExtractSummary:
    summary = ExtractSummary()
    docs = session.scalars(
        select(Document).where(Document.submission_id == submission_id).order_by(Document.ordinal)
    ).all()
    approved = set(
        session.scalars(
            select(EscalationApproval.document_id).where(
                EscalationApproval.document_id.in_([d.id for d in docs])
            )
        )
    )

    if settings.extract_v2:
        return _extract_v2(
            session, store, settings, list(docs), approved, summary, gateway_factory, run_id
        )

    work: list[DocInput] = []
    for doc in docs:
        if doc.superseded_by_id or not extractable(doc):
            reason = (
                "Superseded by a newer copy" if doc.superseded_by_id else "Not an extracted type"
            )
            _save(session, doc, _skipped(reason), model=None)
            summary.statuses["skipped"] += 1
            continue
        work.append(load_input(session, store, doc))

    try:
        gateway: LlmGateway = gateway_factory(settings)
    except LlmUnavailable as exc:
        summary.unavailable = str(exc)
        for item in work:
            _save(session, item.doc, _failed(f"AI extraction unavailable: {exc}"), model=None)
            summary.statuses["failed"] += 1
        session.commit()
        return summary

    requests: list[LlmRequest[BaseModel]] = []
    for item in work:
        strong = item.doc.id in approved
        requests.append(
            build_request(
                item,
                model=settings.llm_escalation_model if strong else settings.llm_extract_model,
                effort=settings.llm_escalation_effort if strong else settings.llm_extract_effort,
            )
        )
    results = gateway.run_many(session, requests, run_id=run_id)

    for item, result in zip(work, results, strict=True):
        cheap = (
            item.doc.id not in approved
            and settings.llm_extract_model != settings.llm_escalation_model
        )
        outcome = finish(item, result, cheap=cheap)
        record = _save(session, item.doc, outcome, model=result.request.model, result=result)
        if outcome.status is ExtractionStatus.NEEDS_ESCALATION:
            record.escalation_cost_usd = escalation_cost(result, settings)
            summary.pending_escalations.append(
                {
                    "document_id": item.doc.id,
                    "title": item.doc.title,
                    "reasons": outcome.escalation_reasons,
                    "estimated_cost_usd": record.escalation_cost_usd,
                }
            )
        elif outcome.status is ExtractionStatus.NEEDS_REVIEW:
            summary.needs_review.append(
                {
                    "document_id": item.doc.id,
                    "title": item.doc.title,
                    "reasons": outcome.escalation_reasons,
                }
            )
        summary.statuses[outcome.status.value] += 1
        summary.facts += len(outcome.facts)
        text = [f for f in outcome.facts if f.method == "text"]
        summary.text_facts += len(text)
        summary.grounded_text_facts += sum(1 for f in text if f.grounding in ("exact", "fuzzy"))
        vision = [f for f in outcome.facts if f.method == "vision"]
        summary.vision_facts += len(vision)
        summary.ocr_confirmed += sum(1 for f in vision if f.grounding.startswith("ocr_"))
        summary.checks_passed += sum(1 for c in outcome.checks if c.passed)
        summary.checks_failed += sum(1 for c in outcome.checks if not c.passed)
        chain.record(
            session,
            actor=ACTOR,
            action="document.extracted",
            subject_type="document",
            subject_id=item.doc.id,
            payload={
                "title": item.doc.title,  # the timeline names the document
                "status": outcome.status.value,
                "model": result.request.model,
                "facts": len(outcome.facts),
                "checks_failed": [c.name for c in outcome.checks if not c.passed],
                "llm_call_id": result.call.id,
            },
        )
    summary.llm = {
        "calls": gateway.stats.calls,
        "cache_hits": gateway.stats.cache_hits,
        "failures": gateway.stats.failures,
        "cost_usd": gateway.stats.cost_usd,
    }
    session.commit()
    return summary


def reads_strong_first(item: DocInput, settings: Settings) -> bool:
    """Hard reads go to the stronger model first: a page graded poor, or an image of a type that
    is usually a phone photo (a license card). The cheaper model then gives the second read."""
    if settings.llm_extract_model == settings.llm_escalation_model:
        return False
    poor = settings.extract_strong_first_poor and any(
        p.page.quality is QualityBand.POOR for p in item.pages
    )
    return poor or (item.is_image and item.doc.doc_type in settings.extract_strong_first_types)


def _extract_v2(
    session: Session,
    store: BlobStore,
    settings: Settings,
    docs: list[Document],
    approved: set[str],
    summary: ExtractSummary,
    gateway_factory: Any,
    run_id: str | None,
) -> ExtractSummary:
    """Extraction v2: catalog fields only, two reads for decision fields, and a stronger re-read
    without asking when a decision field does not verify (lane/extract/engine_v2.py)."""
    work: list[DocInput] = []
    for doc in docs:
        if doc.superseded_by_id:
            _save(session, doc, _skipped("Superseded by a newer copy"), model=None)
            summary.statuses["skipped"] += 1
            continue
        item = load_input(session, store, doc)
        if not v2.extractable(doc.doc_type, item.is_sheet):
            _save(session, doc, _skipped("Not an extracted type"), model=None)
            summary.statuses["skipped"] += 1
            continue
        work.append(item)
    try:
        gateway: LlmGateway = gateway_factory(settings)
    except LlmUnavailable as exc:
        summary.unavailable = str(exc)
        for item in work:
            _save(session, item.doc, _failed(f"AI extraction unavailable: {exc}"), model=None)
            summary.statuses["failed"] += 1
        session.commit()
        return summary

    cheap_model, strong_model = settings.llm_extract_model, settings.llm_escalation_model
    today = date.today()

    def request(item: DocInput, strong: bool) -> LlmRequest[BaseModel]:
        return v2.build_request(
            item,
            model=strong_model if strong else cheap_model,
            effort=settings.llm_escalation_effort if strong else settings.llm_extract_effort,
        )

    routed = {
        i.doc.id for i in work if reads_strong_first(i, settings) and i.doc.id not in approved
    }
    first = gateway.run_many(
        session,
        [request(i, i.doc.id in approved or i.doc.id in routed) for i in work],
        run_id=run_id,
    )
    outcomes = [
        v2.finish(i, r, strong=i.doc.id in approved or cheap_model == strong_model, today=today)
        for i, r in zip(work, first, strict=True)
    ]
    summary.strong_first = sorted(i.doc.title for i in work if i.doc.id in routed)
    # Decision fields that did not verify, recall or single-read validator failures: a second,
    # independent read now, no approval needed. Usually the stronger model; for a document it
    # already read first, the cheaper model, and the stronger model's values stay the facts.
    again = [n for n, o in enumerate(outcomes) if o.auto_escalate]
    if again:
        second = gateway.run_many(
            session,
            [request(work[n], work[n].doc.id not in routed) for n in again],
            run_id=run_id,
        )
        for n, result in zip(again, second, strict=True):
            if work[n].doc.id in routed:
                strong_read = v2.finish(work[n], first[n], strong=True, today=today)
                cheap_read = v2.finish(work[n], result, strong=True, today=today)
                v2.keep_both_reads(
                    work[n],
                    cheap_read,
                    strong_read,
                    first_model=cheap_model,
                    second_model=strong_model,
                )
                outcomes[n] = strong_read
                summary.second_read.append(work[n].doc.title)
                continue
            outcome = v2.finish(work[n], result, strong=True, today=today)
            v2.keep_both_reads(
                work[n], outcomes[n], outcome, first_model=cheap_model, second_model=strong_model
            )
            summary.auto_escalated.append(work[n].doc.title)
            outcomes[n] = outcome
            first[n] = result
    for n, (item, result, outcome) in enumerate(zip(work, first, outcomes, strict=True)):
        record = _save(session, item.doc, outcome, model=result.request.model, result=result)
        record.auto_escalated = n in again
        record.coverage = outcome.coverage
        record.cost_usd = float(result.call.cost_usd or 0)
        record.latency_ms = result.call.latency_ms
        if outcome.status is ExtractionStatus.NEEDS_ESCALATION:
            record.escalation_cost_usd = escalation_cost(result, settings)
            summary.pending_escalations.append(
                {
                    "document_id": item.doc.id,
                    "title": item.doc.title,
                    "reasons": outcome.escalation_reasons,
                    "estimated_cost_usd": record.escalation_cost_usd,
                }
            )
        elif outcome.status is ExtractionStatus.NEEDS_REVIEW:
            summary.needs_review.append(
                {
                    "document_id": item.doc.id,
                    "title": item.doc.title,
                    "reasons": outcome.escalation_reasons,
                }
            )
        summary.statuses[outcome.status.value] += 1
        summary.facts += len(outcome.facts)
        summary.decision_facts += sum(1 for f in outcome.facts if f.tier == "decision")
        summary.decision_verified += sum(
            1 for f in outcome.facts if f.tier == "decision" and f.verified
        )
        text = [f for f in outcome.facts if f.method == "text"]
        summary.text_facts += len(text)
        summary.grounded_text_facts += sum(
            1 for f in text if f.grounding not in ("none", "mismatch")
        )
        vision = [f for f in outcome.facts if f.method == "vision"]
        summary.vision_facts += len(vision)
        summary.ocr_confirmed += sum(
            1 for f in vision if f.grounding.startswith("ocr_") and f.verified
        )
        summary.checks_passed += sum(1 for c in outcome.checks if c.passed)
        summary.checks_failed += sum(1 for c in outcome.checks if not c.passed)
        chain.record(
            session,
            actor=ACTOR,
            action="document.extracted",
            subject_type="document",
            subject_id=item.doc.id,
            payload={
                "title": item.doc.title,  # the timeline names the document
                "status": outcome.status.value,
                "model": result.request.model,
                "extractor": f"v{v2.EXTRACTOR_VERSION}",
                "auto_escalated": n in again,
                "facts": len(outcome.facts),
                "checks_failed": [c.name for c in outcome.checks if not c.passed],
                "llm_call_id": result.call.id,
            },
        )
    summary.llm = {
        "calls": gateway.stats.calls,
        "cache_hits": gateway.stats.cache_hits,
        "failures": gateway.stats.failures,
        "cost_usd": gateway.stats.cost_usd,
    }
    session.commit()
    return summary


def _skipped(reason: str) -> ExtractionOutcome:
    return ExtractionOutcome(ExtractionStatus.SKIPPED, "none", None, [], [], [reason])


def _failed(reason: str) -> ExtractionOutcome:
    return ExtractionOutcome(ExtractionStatus.FAILED, "none", None, [], [], [reason], reason)


def _save(
    session: Session,
    doc: Document,
    outcome: ExtractionOutcome,
    *,
    model: str | None,
    result: LlmResult[BaseModel] | None = None,
) -> DocumentExtraction:
    session.execute(delete(Fact).where(Fact.document_id == doc.id))
    record = session.get(DocumentExtraction, doc.id) or DocumentExtraction(document_id=doc.id)
    record.submission_id = doc.submission_id
    record.doc_type = doc.doc_type
    record.status = outcome.status
    record.method = outcome.method
    record.model = model
    record.extractor_version = "2" if outcome.coverage is not None else EXTRACTOR_VERSION
    record.data = outcome.data
    record.checks = [c.as_dict() for c in outcome.checks]
    record.escalation_reasons = outcome.escalation_reasons
    record.escalation_cost_usd = None
    record.llm_call_id = result.call.id if result else None
    record.error = outcome.error
    session.add(record)
    for f in outcome.facts:
        session.add(
            Fact(
                submission_id=doc.submission_id,
                document_id=doc.id,
                path=f.path,
                value_raw=f.raw,
                value_norm=f.norm,
                page_id=f.page_id,
                page_no=f.page_no,
                bbox=list(f.bbox) if f.bbox else None,
                cell_ref=f.cell_ref,
                quote=f.quote,
                method=f.method,
                grounding=f.grounding,
                confidence=f.confidence,
                confidence_reasons=f.reasons,
                tier=f.tier,
                verified=f.verified,
                verification=f.verification,
                second_read=f.second_read,
                reads=f.reads,
                model=None if f.method == "sheet" and model is None else model,
            )
        )
    session.flush()
    return record


def pending_escalations(session: Session, submission_id: str) -> list[DocumentExtraction]:
    return list(
        session.scalars(
            select(DocumentExtraction).where(
                DocumentExtraction.submission_id == submission_id,
                DocumentExtraction.status == ExtractionStatus.NEEDS_ESCALATION,
            )
        )
    )


def approve_escalations(session: Session, submission_id: str, *, actor: str) -> list[str]:
    """Record approval for every document waiting on escalation. The caller reruns the pipeline;
    approved documents are then read with the stronger model."""
    pending = pending_escalations(session, submission_id)
    for record in pending:
        session.merge(
            EscalationApproval(
                document_id=record.document_id,
                approved_by=actor,
                estimated_cost_usd=record.escalation_cost_usd,
            )
        )
    if pending:
        chain.record(
            session,
            actor=actor,
            action="escalation.approved",
            subject_type="submission",
            subject_id=submission_id,
            payload={
                "documents": [r.document_id for r in pending],
                "estimated_cost_usd": round(sum(r.escalation_cost_usd or 0 for r in pending), 4),
            },
        )
    session.commit()
    return [r.document_id for r in pending]
