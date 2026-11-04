"""Logical documents of a submission: what each file really contains."""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select

from lane.api.auth import Reviewer
from lane.api.deps import BlobStoreDep, SessionDep, SettingsDep
from lane.api.display import dates_in
from lane.audit import chain
from lane.db.models import (
    Attachment,
    Document,
    DocumentLabel,
    LearnedLayout,
    Page,
    PageClassification,
    Submission,
)
from lane.documents import layouts, templates
from lane.documents.formats import format_note
from lane.documents.types import DOC_TYPE_LABELS, DocType
from lane.pipeline import background as work

router = APIRouter(prefix="/api/v1", tags=["documents"])


class LabelOut(BaseModel):
    """A person's label: the type they set, what the classifier said, who and why."""

    doc_type: str
    was: str
    reason: str
    actor: str
    at: str


class LandmarkOut(BaseModel):
    page_id: str
    text: str
    box: list[float] | None  # in the page's units, for drawing in the lens


class RecognitionOut(BaseModel):
    """How the document's type was decided."""

    method: Literal["layout", "ai", "keywords", "person", "intake"]
    template_id: str | None
    template_name: str | None
    hits: int | None
    of: int | None
    landmarks: list[LandmarkOut]


class DocumentOut(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    id: str
    ordinal: int
    attachment_id: str
    filename: str
    doc_type: str
    doc_type_label: str
    title: str
    page_from: int
    page_to: int
    page_ids: list[str]
    subject_name: str | None
    period: str | None
    document_date: str | None
    confidence: str
    confidence_reasons: list[str]
    filename_hint: str | None
    filename_mismatch: bool  # the name claims a different kind of document
    format_note: str | None = None  # "Different format: letter, not a loss run"
    superseded_by_id: str | None
    needs_review: bool
    review_reasons: list[str]
    label: LabelOut | None
    recognition: RecognitionOut

    @field_validator("title")
    @classmethod
    def _readable(cls, v: str) -> str:
        return dates_in(v) or v


@router.get("/submissions/{submission_id}/documents")
def list_documents(
    submission_id: str, session: SessionDep, settings: SettingsDep
) -> list[DocumentOut]:
    if session.get(Submission, submission_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    docs = session.execute(
        select(Document, Attachment.filename)
        .join(Attachment, Document.attachment_id == Attachment.id)
        .where(Document.submission_id == submission_id)
        .order_by(Document.ordinal)
    ).all()
    labels = {
        lb.document_id: lb
        for lb in session.scalars(
            select(DocumentLabel).where(DocumentLabel.submission_id == submission_id)
        )
    }
    signals = {
        pid: pc.signals
        for pid, pc in session.execute(
            select(Page.id, PageClassification)
            .join(PageClassification, PageClassification.page_id == Page.id)
            .where(Page.submission_id == submission_id)
        )
    }
    pages: dict[str, list[str]] = {}
    for page_id, doc_id in session.execute(
        select(Page.id, Page.document_id)
        .where(Page.submission_id == submission_id, Page.document_id.is_not(None))
        .order_by(Page.page_no)
    ):
        if doc_id:
            pages.setdefault(doc_id, []).append(page_id)
    return [
        DocumentOut(
            id=d.id,
            ordinal=d.ordinal,
            attachment_id=d.attachment_id,
            filename=filename,
            doc_type=d.doc_type,
            doc_type_label=DOC_TYPE_LABELS[DocType(d.doc_type)],
            title=d.title,
            page_from=d.page_from,
            page_to=d.page_to,
            page_ids=pages.get(d.id, []),
            # Identifiers (license and policy numbers) are not exposed here; see PII masking (P8).
            subject_name=d.subject_name,
            period=d.period,
            document_date=d.document_date,
            confidence=d.confidence.value,
            confidence_reasons=d.confidence_reasons,
            filename_hint=d.filename_hint,
            filename_mismatch=d.filename_mismatch,
            format_note=format_note(settings, d.filename_hint, d.doc_type),
            superseded_by_id=d.superseded_by_id,
            needs_review=d.needs_review,
            review_reasons=d.review_reasons,
            label=LabelOut(
                doc_type=lb.doc_type,
                was=lb.was,
                reason=lb.reason,
                actor=lb.actor,
                at=lb.created_at.isoformat(),
            )
            if (lb := labels.get(d.id))
            else None,
            recognition=_recognition(d, pages.get(d.id, []), signals, d.id in labels),
        )
        for d, filename in docs
    ]


class DocTypeOut(BaseModel):
    value: str
    label: str


@router.get("/document-types", operation_id="list_document_types")
def list_document_types() -> list[DocTypeOut]:
    """Types a person can give a document (the email body is set by intake, never by hand)."""
    return [
        DocTypeOut(value=t.value, label=label)
        for t, label in DOC_TYPE_LABELS.items()
        if t is not DocType.EMAIL_BODY
    ]


class LabelIn(BaseModel):
    doc_type: str
    reason: str


@router.post(
    "/documents/{document_id}/label",
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="label_document",
)
def label_document(
    document_id: str,
    body: LabelIn,
    session: SessionDep,
    background: BackgroundTasks,
    viewer: Reviewer,
) -> dict[str, str]:
    """Say what a document is, with a reason. The submission is processed again so the document
    is read as that type; answers already in the cache cost nothing."""
    doc = _document(session, document_id)
    try:
        DocType(body.doc_type)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown document type") from exc
    if body.doc_type == DocType.EMAIL_BODY or doc.doc_type == DocType.EMAIL_BODY:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "The email body is not relabelled"
        )
    if not body.reason.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Labelling needs a reason")
    row = session.get(DocumentLabel, doc.id) or DocumentLabel(
        document_id=doc.id, submission_id=doc.submission_id, was=doc.doc_type
    )
    row.doc_type = body.doc_type
    row.reason = body.reason.strip()
    row.actor = viewer.actor
    session.add(row)
    chain.record(
        session,
        actor=viewer.actor,
        action="document.relabeled",
        subject_type="document",
        subject_id=doc.id,
        payload={
            "title": doc.title,
            "from": DOC_TYPE_LABELS[DocType(row.was)],
            "to": DOC_TYPE_LABELS[DocType(body.doc_type)],
            "reason": row.reason,
        },
    )
    session.commit()
    work.start(background, session, "process", doc.submission_id, trigger="relabel")
    return {"status": "accepted"}


@router.delete(
    "/documents/{document_id}/label",
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="clear_document_label",
)
def clear_label(
    document_id: str,
    session: SessionDep,
    background: BackgroundTasks,
    viewer: Reviewer,
) -> dict[str, str]:
    """Remove a person's label; the classifier's reading is back after reprocessing."""
    doc = _document(session, document_id)
    row = session.get(DocumentLabel, doc.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This document has no label")
    session.delete(row)
    chain.record(
        session,
        actor=viewer.actor,
        action="document.label_cleared",
        subject_type="document",
        subject_id=doc.id,
        payload={"title": doc.title},
    )
    session.commit()
    work.start(background, session, "process", doc.submission_id, trigger="relabel")
    return {"status": "accepted"}


def _document(session: SessionDep, document_id: str) -> Document:
    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return doc


def _recognition(
    d: Document, page_ids: list[str], signals: dict[str, dict[str, Any]], labelled: bool
) -> RecognitionOut:
    """Layout when every page was typed by a known layout; otherwise how most pages were."""
    sig = [signals[p] for p in page_ids if p in signals]
    if labelled:
        return RecognitionOut(
            method="person", template_id=None, template_name=None, hits=None, of=None, landmarks=[]
        )
    if d.doc_type == DocType.EMAIL_BODY or not sig:
        return RecognitionOut(
            method="intake", template_id=None, template_name=None, hits=None, of=None, landmarks=[]
        )
    if all(s.get("method") == "layout" for s in sig):
        first = sig[0]
        return RecognitionOut(
            method="layout",
            template_id=first.get("template"),
            template_name=first.get("template_name"),
            hits=first.get("hits"),
            of=first.get("of"),
            landmarks=[
                LandmarkOut(page_id=pid, text=str(lm["text"]), box=lm.get("box"))
                for pid in page_ids
                for lm in signals.get(pid, {}).get("landmarks", [])
            ],
        )
    methods = [str(s.get("method", "ai")) for s in sig]
    method = "keywords" if methods.count("keywords") > len(methods) / 2 else "ai"
    return RecognitionOut(
        method=method, template_id=None, template_name=None, hits=None, of=None, landmarks=[]
    )


class KnownLayoutOut(BaseModel):
    id: str
    name: str
    kind: str
    doc_type: str
    doc_type_label: str
    status: str  # active | disabled
    source: str | None
    landmarks: int
    pages_matched: int  # stored pages typed by this layout
    audited: int  # pages the model re-checked
    audit_agreed: int
    # Provenance: learned only from a document a person confirmed
    source_document_id: str
    source_submission_id: str
    source_page_no: int
    confirmed_by: str
    confirmed_at: datetime
    disabled_reason: str | None


@router.get("/known-layouts", operation_id="list_known_layouts")
def list_known_layouts(session: SessionDep) -> list[KnownLayoutOut]:
    """Every learned layout, where it came from, how often it typed a page, and how the model
    audit went. None ship with Lane: each was learned from a confirmed document."""
    used = templates.layout_audits(
        [sig for (sig,) in session.execute(select(PageClassification.signals))]
    )
    out: list[KnownLayoutOut] = []
    for row in session.scalars(select(LearnedLayout).order_by(LearnedLayout.confirmed_at)):
        t = templates.Template.model_validate(row.definition)
        out.append(
            KnownLayoutOut(
                id=row.id,
                name=row.name,
                kind=t.kind,
                doc_type=row.doc_type,
                doc_type_label=DOC_TYPE_LABELS[t.doc_type],
                status=row.status,
                source=t.source,
                landmarks=len(t.anchors) or len(t.markers),
                pages_matched=used.get(row.id, [0, 0, 0])[0],
                audited=row.audits,
                audit_agreed=row.agreements,
                source_document_id=row.source_document_id,
                source_submission_id=row.source_submission_id,
                source_page_no=row.source_page_no,
                confirmed_by=row.confirmed_by.replace("user:", ""),
                confirmed_at=row.confirmed_at,
                disabled_reason=row.disabled_reason,
            )
        )
    return out


class ConfirmOut(BaseModel):
    document_id: str
    doc_type: str
    layouts: list[str]  # learned or refreshed from this document


@router.post("/documents/{document_id}/confirm", operation_id="confirm_document")
def confirm_document(
    document_id: str, session: SessionDep, store: BlobStoreDep, viewer: Reviewer
) -> ConfirmOut:
    """A person confirms what a document is. Lane learns its layout from it, with where it came
    from, so the next document laid out the same way is typed with no model call. This is the
    only way a known layout is made."""
    doc = _document(session, document_id)
    learned = layouts.learn(session, store, doc, actor=viewer.actor)
    chain.record(
        session,
        actor=viewer.actor,
        action="document.confirmed",
        subject_type="document",
        subject_id=doc.id,
        payload={"doc_type": doc.doc_type, "layouts": [row.id for row in learned]},
    )
    session.commit()
    return ConfirmOut(document_id=doc.id, doc_type=doc.doc_type, layouts=[r.id for r in learned])
