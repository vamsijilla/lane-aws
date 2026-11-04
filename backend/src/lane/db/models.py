"""ORM models, grouped by concern; docs/ARCHITECTURE.md describes every table."""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lane.db.base import Base, TimestampMixin, utcnow


def new_id() -> str:
    return uuid.uuid4().hex


def str_enum[E: enum.StrEnum](cls: type[E]) -> Enum:
    """Store enum values (lowercase), not member names, as plain strings."""
    return Enum(
        cls,
        native_enum=False,
        length=16,
        values_callable=_enum_values,
    )


def _enum_values(members: type[enum.StrEnum]) -> list[str]:
    return [m.value for m in members]


class SubmissionStatus(enum.StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class AttachmentRole(enum.StrEnum):
    EMAIL_BODY = "email_body"
    ATTACHMENT = "attachment"
    ARCHIVE_MEMBER = "archive_member"


class AttachmentStatus(enum.StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    EXPANDED = "expanded"  # an archive whose members were extracted
    REJECTED = "rejected"


class Blob(Base, TimestampMixin):
    """Content-addressed bytes. The same file sent twice is stored once."""

    __tablename__ = "blobs"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    size: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(32))  # sniffed from content, never the filename
    mime: Mapped[str] = mapped_column(String(128))
    storage_key: Mapped[str] = mapped_column(String(256))


class EmailMessage(Base, TimestampMixin):
    __tablename__ = "email_messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(512), unique=True)
    message_id: Mapped[str | None] = mapped_column(String(512), index=True)
    raw_sha256: Mapped[str] = mapped_column(ForeignKey("blobs.sha256"))
    source: Mapped[str] = mapped_column(String(32))  # upload | cli | smtp
    from_addr: Mapped[str] = mapped_column(String(320))
    to_addrs: Mapped[list[str]] = mapped_column(JSON)
    subject: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    headers: Mapped[dict[str, Any]] = mapped_column(JSON)

    submission: Mapped["Submission"] = relationship(back_populates="email")


class Submission(Base, TimestampMixin):
    __tablename__ = "submissions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email_id: Mapped[str] = mapped_column(ForeignKey("email_messages.id"), unique=True)
    status: Mapped[SubmissionStatus] = mapped_column(
        str_enum(SubmissionStatus), default=SubmissionStatus.RECEIVED
    )
    # How this email relates to an earlier submission (lane/intake/link.py): same_files,
    # follow_up (its files were attached there), possible_follow_up (a person decides) or
    # kept_separate. None for a new submission.
    related_to_id: Mapped[str | None] = mapped_column(ForeignKey("submissions.id"), index=True)
    relation: Mapped[str | None] = mapped_column(String(24))
    relation_how: Mapped[str | None] = mapped_column(
        String(16)
    )  # reference | thread | identity | name | files
    relation_reason: Mapped[str | None] = mapped_column(Text)
    relation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    relation_by: Mapped[str | None] = mapped_column(String(64))

    email: Mapped[EmailMessage] = relationship(back_populates="submission")
    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="submission", order_by="Attachment.ordinal"
    )


class Attachment(Base, TimestampMixin):
    """One file as received: a top-level attachment, an archive member, or the email body."""

    __tablename__ = "attachments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    role: Mapped[AttachmentRole] = mapped_column(str_enum(AttachmentRole))
    filename: Mapped[str] = mapped_column(Text)  # as sent; an untrusted hint only
    declared_mime: Mapped[str | None] = mapped_column(String(128))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("attachments.id"))
    archive_path: Mapped[str | None] = mapped_column(Text)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    blob_sha256: Mapped[str | None] = mapped_column(ForeignKey("blobs.sha256"))
    status: Mapped[AttachmentStatus] = mapped_column(str_enum(AttachmentStatus))
    duplicate_of_id: Mapped[str | None] = mapped_column(ForeignKey("attachments.id"))
    reason: Mapped[str | None] = mapped_column(Text)
    # Why triage could not read it (corrupt, encrypted, unsupported); None when it was read
    read_error: Mapped[str | None] = mapped_column(Text)

    submission: Mapped[Submission] = relationship(back_populates="attachments")
    blob: Mapped[Blob | None] = relationship()


class AuditEvent(Base):
    """Append-only, hash-chained. Each hash covers the previous hash and this event."""

    __tablename__ = "audit_events"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64), index=True)
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    # Unique: two writers that read the same tip cannot both append. One fails and retries.
    prev_hash: Mapped[str] = mapped_column(String(64), unique=True)
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class PageKind(enum.StrEnum):
    TEXT = "text"  # digital page with a usable text layer
    SCAN = "scan"  # image of a document, no text layer
    PHOTO = "photo"  # camera photo (card, form on a table), no text layer
    SHEET = "sheet"  # one worksheet of a spreadsheet
    EMAIL_BODY = "email_body"


class QualityBand(enum.StrEnum):
    CLEAN = "clean"
    DEGRADED = "degraded"
    POOR = "poor"


class Page(Base, TimestampMixin):
    """One physical unit of a file: a PDF page, an image, a worksheet or the email body.

    Derived artifacts (render, thumbnail, word boxes, sheet grid) are blobs, so they are
    deduped and immutable like everything else.
    """

    __tablename__ = "pages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    attachment_id: Mapped[str] = mapped_column(ForeignKey("attachments.id"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)  # 1-based within the attachment
    kind: Mapped[PageKind] = mapped_column(str_enum(PageKind))
    triage_version: Mapped[str] = mapped_column(String(16))

    width: Mapped[float | None] = mapped_column()  # PDF points or pixels
    height: Mapped[float | None] = mapped_column()
    rotation: Mapped[int] = mapped_column(Integer, default=0)  # rotation declared by the file
    has_text_layer: Mapped[bool] = mapped_column(default=False)
    text_chars: Mapped[int] = mapped_column(Integer, default=0)
    image_coverage: Mapped[float] = mapped_column(default=0.0)

    sharpness: Mapped[float | None] = mapped_column()
    contrast: Mapped[float | None] = mapped_column()
    colorfulness: Mapped[float | None] = mapped_column()
    skew_deg: Mapped[float | None] = mapped_column()
    quality: Mapped[QualityBand] = mapped_column(str_enum(QualityBand))
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)

    sheet_name: Mapped[str | None] = mapped_column(String(255))
    sheet_hidden: Mapped[bool] = mapped_column(default=False)

    render_sha256: Mapped[str | None] = mapped_column(ForeignKey("blobs.sha256"))
    thumb_sha256: Mapped[str | None] = mapped_column(ForeignKey("blobs.sha256"))
    # The page as it came, when preparation changed it (turned, straightened, flattened)
    original_sha256: Mapped[str | None] = mapped_column(ForeignKey("blobs.sha256"))
    prep: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    layout_sha256: Mapped[str | None] = mapped_column(ForeignKey("blobs.sha256"))
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"), index=True)
    text_source: Mapped[str | None] = mapped_column(String(16))  # layer | ocr
    ocr_confidence: Mapped[float | None] = mapped_column()

    attachment: Mapped[Attachment] = relationship()


class RunStatus(enum.StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"  # finished, but some steps or items failed
    FAILED = "failed"


class StepStatus(enum.StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"
    FAILED = "failed"
    SKIPPED = "skipped"


class Run(Base, TimestampMixin):
    """One pass of the pipeline over a submission."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    trigger: Mapped[str] = mapped_column(String(32))  # intake | follow_up | rerun | replay | cli
    # The email that started it: the original for intake, the agent's reply for a follow-up
    email_id: Mapped[str | None] = mapped_column(ForeignKey("email_messages.id"))
    status: Mapped[RunStatus] = mapped_column(str_enum(RunStatus), default=RunStatus.RUNNING)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Replay: what went in, which versions ran, and a hash of everything that came out
    versions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, server_default="{}")
    input_hash: Mapped[str | None] = mapped_column(String(64))
    output_hash: Mapped[str | None] = mapped_column(String(64))
    replay_of: Mapped[str | None] = mapped_column(String(32))

    steps: Mapped[list["Step"]] = relationship(back_populates="run", order_by="Step.started_at")


class Step(Base):
    __tablename__ = "steps"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(16))
    status: Mapped[StepStatus] = mapped_column(str_enum(StepStatus), default=StepStatus.RUNNING)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)

    run: Mapped[Run] = relationship(back_populates="steps")


class ConfidenceBand(enum.StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class LlmCall(Base, TimestampMixin):
    """Every model call, cached or not: what was asked, of which model, at what cost."""

    __tablename__ = "llm_calls"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(64))  # e.g. classify_page
    subject_id: Mapped[str | None] = mapped_column(String(64))  # e.g. the page id
    model: Mapped[str] = mapped_column(String(64))
    prompt_name: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(16))
    prompt_sha256: Mapped[str] = mapped_column(String(64))
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    cache_hit: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(16))  # ok | refused | invalid | error
    stop_reason: Mapped[str | None] = mapped_column(String(32))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    request_id: Mapped[str | None] = mapped_column(String(128))
    served_by: Mapped[str | None] = mapped_column(String(64))  # differs from model on fallback
    error: Mapped[str | None] = mapped_column(Text)


class LlmCacheEntry(Base, TimestampMixin):
    """Validated model output keyed by (model, prompt hash, input hash). Makes runs replayable."""

    __tablename__ = "llm_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(64))
    output: Mapped[dict[str, Any]] = mapped_column(JSON)
    source_call_id: Mapped[str] = mapped_column(ForeignKey("llm_calls.id"))


class UnderwriterDecision(Base, TimestampMixin):
    """What the underwriter decided, beside Lane's recommendation: decline, refer back to the
    agent, or proceed to quote, with a reason and any conditions. Every decision is kept; the
    latest is current."""

    __tablename__ = "underwriter_decisions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    decision: Mapped[str] = mapped_column(String(16))  # decline | refer_back | quote
    reason: Mapped[str] = mapped_column(Text)
    conditions: Mapped[list[str]] = mapped_column(JSON, default=list)
    recommendation: Mapped[str | None] = mapped_column(Text)  # Lane's, at the time
    decided_by: Mapped[str] = mapped_column(String(128))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RuleConfirmation(Base, TimestampMixin):
    """A rule that moved away from decline or potential decline on a document the agent sent:
    the new outcome stands only once an underwriter confirms it."""

    __tablename__ = "rule_confirmations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    rule_id: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(Text)
    before: Mapped[str] = mapped_column(String(24))  # decline | potential decline
    after: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | confirmed | void
    decided_by: Mapped[str | None] = mapped_column(String(128))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RequestSnapshot(Base, TimestampMixin):
    """The agent request as a person marked it sent: who, when, and every item with its details
    (included or not). Lane never sends; this records that someone did, so a reply is measured
    against what the agent was actually asked."""

    __tablename__ = "request_snapshots"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    draft_version: Mapped[int | None] = mapped_column(Integer)
    subject: Mapped[str | None] = mapped_column(Text)
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    sent_by: Mapped[str] = mapped_column(String(128))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PageCacheEntry(Base, TimestampMixin):
    """A page as triage read it, kept by the file's sha256, the triage version and the page
    number, so the same file is not read again even after its submission is deleted (images
    and layouts are content-addressed blobs, which stay)."""

    __tablename__ = "page_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    triage_version: Mapped[str] = mapped_column(String(16))
    page_no: Mapped[int] = mapped_column(Integer)
    page: Mapped[dict[str, Any]] = mapped_column(JSON)  # the Page columns, as stored
    hidden: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class OcrCacheEntry(Base, TimestampMixin):
    """Words OCR read on one page of one file, keyed by the file's sha256, the page, the OCR
    engine and version, and the settings that shape the image (documents/triage.OcrScope). The
    same file sent again, in any email, is not read again."""

    __tablename__ = "ocr_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    engine: Mapped[str] = mapped_column(String(64))
    words: Mapped[list[list[Any]]] = mapped_column(JSON)


class Document(Base, TimestampMixin):
    """A logical document: one or more consecutive pages of one file with one business type.

    A file can hold several documents (an MVR bundle with license photos); the filename is
    only a hint, recorded and compared, never trusted.
    """

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    attachment_id: Mapped[str] = mapped_column(ForeignKey("attachments.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)  # order within the submission
    doc_type: Mapped[str] = mapped_column(String(32), index=True)
    page_from: Mapped[int] = mapped_column(Integer)
    page_to: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    subject_id: Mapped[str | None] = mapped_column(String(128))
    subject_name: Mapped[str | None] = mapped_column(String(255))
    period: Mapped[str | None] = mapped_column(String(64))
    document_date: Mapped[str | None] = mapped_column(String(16))  # ISO date as printed
    confidence: Mapped[ConfidenceBand] = mapped_column(str_enum(ConfidenceBand))
    confidence_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    filename_hint: Mapped[str | None] = mapped_column(String(32))
    filename_mismatch: Mapped[bool] = mapped_column(default=False)
    superseded_by_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"))
    needs_review: Mapped[bool] = mapped_column(default=False)
    review_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    classifier_version: Mapped[str] = mapped_column(String(16))

    attachment: Mapped[Attachment] = relationship()


class PageClassification(Base, TimestampMixin):
    """What the classifier said about one page, kept for audit and for re-splitting."""

    __tablename__ = "page_classifications"

    page_id: Mapped[str] = mapped_column(ForeignKey("pages.id"), primary_key=True)
    classifier_version: Mapped[str] = mapped_column(String(16))
    model_output: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    signals: Mapped[dict[str, Any]] = mapped_column(JSON)
    llm_call_id: Mapped[str | None] = mapped_column(ForeignKey("llm_calls.id"))
    error: Mapped[str | None] = mapped_column(Text)


class ExtractionStatus(enum.StrEnum):
    OK = "ok"
    NEEDS_ESCALATION = "needs_escalation"  # cheap model failed checks; awaiting approval
    NEEDS_REVIEW = "needs_review"  # checks failed even on the stronger model
    FAILED = "failed"
    SKIPPED = "skipped"  # superseded or not an extractable type


class DocumentExtraction(Base, TimestampMixin):
    """The typed reading of one logical document, its checks, and how it was obtained."""

    __tablename__ = "document_extractions"

    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    doc_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[ExtractionStatus] = mapped_column(str_enum(ExtractionStatus))
    method: Mapped[str] = mapped_column(String(16))  # sheet | text | vision | none
    model: Mapped[str | None] = mapped_column(String(64))
    extractor_version: Mapped[str] = mapped_column(String(16))
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    escalation_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    escalation_cost_usd: Mapped[float | None] = mapped_column()
    # Extraction v2: re-read with the stronger model without asking (decision fields that did
    # not verify, recall or validator failures); expected / found / verified / missing fields;
    # what the reads cost and how long they took
    auto_escalated: Mapped[bool] = mapped_column(default=False, server_default="0")
    coverage: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    cost_usd: Mapped[float | None] = mapped_column()
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    llm_call_id: Mapped[str | None] = mapped_column(ForeignKey("llm_calls.id"))
    error: Mapped[str | None] = mapped_column(Text)


class Fact(Base, TimestampMixin):
    """One extracted value with its evidence: where it is and how much to trust it."""

    __tablename__ = "facts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    path: Mapped[str] = mapped_column(String(128))  # e.g. claims[1].total_paid
    value_raw: Mapped[str | None] = mapped_column(Text)
    value_norm: Mapped[Any] = mapped_column(JSON)
    page_id: Mapped[str | None] = mapped_column(ForeignKey("pages.id"))
    page_no: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[list[float] | None] = mapped_column(JSON)  # x0, y0, x1, y1 in page points
    cell_ref: Mapped[str | None] = mapped_column(String(64))
    quote: Mapped[str | None] = mapped_column(Text)
    method: Mapped[str] = mapped_column(String(16))
    grounding: Mapped[str] = mapped_column(String(16))  # exact | fuzzy | cell | image | none
    confidence: Mapped[ConfidenceBand] = mapped_column(str_enum(ConfidenceBand))
    confidence_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Extraction v2: the catalog tier, whether a second, independent read agreed, how it was
    # verified (in words), what code read at the value's anchor, and every model read
    tier: Mapped[str | None] = mapped_column(String(16))
    verified: Mapped[bool] = mapped_column(default=False, server_default="0")
    verification: Mapped[str | None] = mapped_column(Text)
    second_read: Mapped[str | None] = mapped_column(Text)
    reads: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    model: Mapped[str | None] = mapped_column(String(64))


class EscalationApproval(Base, TimestampMixin):
    """A person approved re-reading this document with the stronger model."""

    __tablename__ = "escalation_approvals"

    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    approved_by: Mapped[str] = mapped_column(String(128))
    estimated_cost_usd: Mapped[float | None] = mapped_column()


class ResolvedEntity(Base, TimestampMixin):
    """One real-world thing the submission is about: the applicant, a driver, a vehicle, a
    coverage term, a claim. Rebuilt on every run from the documents."""

    __tablename__ = "resolved_entities"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # stable hash
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    key: Mapped[str] = mapped_column(String(128))
    label: Mapped[str] = mapped_column(Text)
    found_in: Mapped[list[str]] = mapped_column(JSON, default=list)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ResolvedField(Base, TimestampMixin):
    """An attribute of an entity with every candidate value, its sources, and its state."""

    __tablename__ = "resolved_fields"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # stable hash
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("resolved_entities.id"), index=True)
    attribute: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16))  # known | conflict | invalid | missing
    resolution: Mapped[str | None] = mapped_column(String(16))
    value_display: Mapped[str | None] = mapped_column(Text)
    value: Mapped[Any] = mapped_column(JSON)
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    invalid: Mapped[list[str]] = mapped_column(JSON, default=list)


class Conflict(Base, TimestampMixin):
    __tablename__ = "conflicts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # stable: one per field
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    field_id: Mapped[str] = mapped_column(ForeignKey("resolved_fields.id"))
    entity_id: Mapped[str] = mapped_column(ForeignKey("resolved_entities.id"))
    type: Mapped[str] = mapped_column(String(16))  # identity | value | cardinality | reference
    title: Mapped[str] = mapped_column(Text)
    hint: Mapped[str | None] = mapped_column(Text)
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    blocking_rules: Mapped[list[str]] = mapped_column(JSON, default=list)
    notes: Mapped[list[str]] = mapped_column(JSON, default=list)  # e.g. an inference it implies
    resolution: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # set when a person resolved it


class MissingInfo(Base, TimestampMixin):
    __tablename__ = "missing_info"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # stable: one per code
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str | None] = mapped_column(ForeignKey("resolved_entities.id"))
    document_ids: Mapped[list[str]] = mapped_column(JSON, default=list)


class RuleOutcomeRecord(Base, TimestampMixin):
    """One guideline rule evaluated for a submission, with the facts it read."""

    __tablename__ = "rule_results"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # stable: submission + rule
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    rule_id: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(Text)
    section: Mapped[str] = mapped_column(Text)
    quote: Mapped[str] = mapped_column(Text)
    decline_capable: Mapped[bool] = mapped_column(default=False)
    outcome: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    items: Mapped[list[str]] = mapped_column(JSON, default=list)
    request: Mapped[str | None] = mapped_column(Text)
    inputs: Mapped[list[str]] = mapped_column(JSON, default=list)  # resolved field ids
    potential_decline: Mapped[bool] = mapped_column(default=False)
    blocking_conflicts: Mapped[list[str]] = mapped_column(JSON, default=list)
    sensitivity: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    anchor_note: Mapped[str | None] = mapped_column(Text)
    readings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    assumptions: Mapped[list[str]] = mapped_column(JSON, default=list)
    # The missing fact the rule waits on ({key, label}); shared keys are one question
    waiting_on: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    evidence: Mapped[list[str]] = mapped_column(JSON, default=list)  # facts behind details read
    absent: Mapped[list[str]] = mapped_column(JSON, default=list)  # fields read, not found
    # What the other date basis would ask for, when only it refers
    anchor_items: Mapped[list[str]] = mapped_column(JSON, default=list)
    ruleset_version: Mapped[str] = mapped_column(String(16))


class DecisionRecord(Base, TimestampMixin):
    """The recommended next step for a submission. The underwriter makes the decision."""

    __tablename__ = "decisions"

    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    outcome: Mapped[str] = mapped_column(String(16))  # decline | refer | ready
    headline: Mapped[str] = mapped_column(Text)
    team: Mapped[str | None] = mapped_column(String(32))
    team_label: Mapped[str | None] = mapped_column(Text)
    route_reason: Mapped[str] = mapped_column(Text)
    route_stable: Mapped[bool] = mapped_column(default=False)
    potential_decline: Mapped[bool] = mapped_column(default=False)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    anchor: Mapped[str] = mapped_column(String(32))
    anchor_date: Mapped[str] = mapped_column(String(16))
    ruleset_version: Mapped[str] = mapped_column(String(16))
    ruleset_sha256: Mapped[str] = mapped_column(String(64))
    counts: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)


class AnalysisRecord(Base, TimestampMixin):
    """The analyst's summary for a submission. Findings live in their own table."""

    __tablename__ = "analyses"

    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    status: Mapped[str] = mapped_column(String(32))  # ok | degraded | awaiting_approval
    model: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(16))
    prompt_sha256: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    footnotes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    refs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # citation id -> evidence
    rejected: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    security_notes: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    error: Mapped[str | None] = mapped_column(Text)
    estimate_usd: Mapped[float | None] = mapped_column()  # shown when waiting for approval
    approved_by: Mapped[str | None] = mapped_column(String(128))  # one paid run per approval
    # When the current findings were written (a later agent reply makes them stale)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FindingRecord(Base, TimestampMixin):
    """One analyst finding with its citations and the underwriter's review of it."""

    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # stable: submission + title
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))  # observation | hypothesis | recommendation
    severity: Mapped[str] = mapped_column(String(8))
    confidence: Mapped[str] = mapped_column(String(8))
    basis: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[str]] = mapped_column(JSON, default=list)
    review_state: Mapped[str] = mapped_column(String(16), default="open")  # open|accepted|dismissed
    review_reason: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(128))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentRequestItem(Base, TimestampMixin):
    """One thing to ask or tell the agent, with links back to what raised it."""

    __tablename__ = "agent_request_items"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # stable: submission + key
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    group: Mapped[str] = mapped_column(String(16))  # before_quote | before_binding | inform
    text: Mapped[str] = mapped_column(Text)
    details: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_kind: Mapped[str] = mapped_column(String(16))  # rule | missing | conflict | finding
    source_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    included: Mapped[bool] = mapped_column(default=True)
    # The question each detail belongs to (entity and topic), parallel to details
    detail_keys: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")


class EmailDraft(Base, TimestampMixin):
    """A draft to the agent. Never sent by the system; a person copies or opens it."""

    __tablename__ = "email_drafts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    to: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(String(16))  # model | template
    model: Mapped[str | None] = mapped_column(String(64))
    items_sha256: Mapped[str] = mapped_column(String(64))
    problems: Mapped[list[str]] = mapped_column(JSON, default=list)


class FieldResolution(Base, TimestampMixin):
    """A person's choice between conflicting values, with the reason. Field ids are stable, so
    the choice survives every re-resolve; it is applied while resolving."""

    __tablename__ = "field_resolutions"

    field_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    display: Mapped[str] = mapped_column(Text)
    value: Mapped[Any] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(128))
    # choice: one of the documents' values for a conflict; override: a person's own value
    kind: Mapped[str] = mapped_column(String(16), default="choice", server_default="choice")
    before: Mapped[str | None] = mapped_column(Text)  # what the field showed before an override


class DocumentLabel(Base, TimestampMixin):
    """A person's label for a document the classifier got wrong or could not place. Applied on
    every classification run until cleared; document ids are stable across runs."""

    __tablename__ = "document_labels"

    document_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    doc_type: Mapped[str] = mapped_column(String(32))
    was: Mapped[str] = mapped_column(String(32))  # what the classifier said
    reason: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(128))


class JobStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Job(Base, TimestampMixin):
    """Work for the worker: process a submission, or replay a run. A worker holds a job by its
    lease; a job whose lease runs out (the worker died) is claimed again by another worker."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_lease", "status", "lease_until"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # process | replay
    trigger: Mapped[str] = mapped_column(String(32))
    status: Mapped[JobStatus] = mapped_column(str_enum(JobStatus), default=JobStatus.QUEUED)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_id: Mapped[str | None] = mapped_column(String(32))  # the run this job is working on
    replay_run_id: Mapped[str | None] = mapped_column(String(32))  # for a replay job
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkerHeartbeat(Base):
    """A worker says it is alive every few seconds, so Operations can show idle workers too and
    tell a healthy worker from one that stopped (its jobs are taken over by lease)."""

    __tablename__ = "worker_heartbeats"

    owner: Mapped[str] = mapped_column(String(128), primary_key=True)  # host:pid
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    job_id: Mapped[str | None] = mapped_column(String(32))  # the job it is running now
    jobs_done: Mapped[int] = mapped_column(Integer, default=0)


class LearnedLayout(Base, TimestampMixin):
    """A known layout, learned only from a document a person confirmed. The definition is a
    lane.documents.templates.Template; provenance says which page of which document it came
    from and who confirmed it. An AI audit that disagrees disables it."""

    __tablename__ = "layouts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    doc_type: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | disabled
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    source_document_id: Mapped[str] = mapped_column(String(32))
    source_submission_id: Mapped[str] = mapped_column(String(32))
    source_page_no: Mapped[int] = mapped_column(Integer)
    confirmed_by: Mapped[str] = mapped_column(String(128))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    audits: Mapped[int] = mapped_column(Integer, default=0)
    agreements: Mapped[int] = mapped_column(Integer, default=0)
    disabled_reason: Mapped[str | None] = mapped_column(Text)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
