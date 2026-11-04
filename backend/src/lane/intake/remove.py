"""Delete a submission, or throw it away and ingest its original email again.

Everything the pipeline derived from the email goes: attachments, pages, documents, facts, the
board, rule results, analysis, drafts, runs. Three things stay on purpose:
- the audit log, which is append-only; a `submission.deleted` event is added to it,
- blobs, which are content-addressed and may be shared; the original email is what a rerun reads,
- model calls and the response cache, so running the same files again reuses the answers,
- the page and OCR caches, kept by file hash, so the same files are not read again.
"""

from dataclasses import dataclass

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from lane.audit import chain
from lane.db.models import (
    AgentRequestItem,
    AnalysisRecord,
    Attachment,
    Blob,
    Conflict,
    DecisionRecord,
    Document,
    DocumentExtraction,
    DocumentLabel,
    EmailDraft,
    EmailMessage,
    EscalationApproval,
    Fact,
    FieldResolution,
    FindingRecord,
    Job,
    LlmCall,
    MissingInfo,
    Page,
    PageClassification,
    RequestSnapshot,
    ResolvedEntity,
    ResolvedField,
    RuleConfirmation,
    RuleOutcomeRecord,
    Run,
    Step,
    Submission,
    UnderwriterDecision,
)
from lane.intake.service import IngestResult, IntakeLimits, ingest_email
from lane.storage.blobs import BlobStore

BY_SUBMISSION = (
    Fact,
    DocumentExtraction,
    Conflict,
    MissingInfo,
    ResolvedField,
    ResolvedEntity,
    RuleOutcomeRecord,
    DecisionRecord,
    AnalysisRecord,
    FindingRecord,
    AgentRequestItem,
    EmailDraft,
    FieldResolution,
    DocumentLabel,
    Job,
    RequestSnapshot,
    RuleConfirmation,
    UnderwriterDecision,
)


@dataclass(frozen=True)
class Removed:
    subject: str
    files: int
    documents: int


def delete_submission(session: Session, submission_id: str, *, actor: str) -> Removed | None:
    """Remove a submission and what was derived from it. None if there is no such submission."""
    sub = session.get(Submission, submission_id)
    if sub is None:
        return None
    # Emails linked to this one (replies attached to it, resends, suggestions) go with it
    for child_id in list(
        session.scalars(select(Submission.id).where(Submission.related_to_id == sub.id))
    ):
        delete_submission(session, child_id, actor=actor)
    email = session.get(EmailMessage, sub.email_id)
    page_ids = list(session.scalars(select(Page.id).where(Page.submission_id == sub.id)))
    doc_ids = list(session.scalars(select(Document.id).where(Document.submission_id == sub.id)))
    run_ids = list(session.scalars(select(Run.id).where(Run.submission_id == sub.id)))
    files = len(
        list(session.scalars(select(Attachment.id).where(Attachment.submission_id == sub.id)))
    )
    removed = Removed(email.subject if email else "", files, len(doc_ids))

    if run_ids:  # keep the calls (and so the cache); only let go of the run
        session.execute(update(LlmCall).where(LlmCall.run_id.in_(run_ids)).values(run_id=None))
    if page_ids:
        session.execute(delete(PageClassification).where(PageClassification.page_id.in_(page_ids)))
    if doc_ids:
        session.execute(
            delete(EscalationApproval).where(EscalationApproval.document_id.in_(doc_ids))
        )
    for model in BY_SUBMISSION:
        session.execute(delete(model).where(model.submission_id == sub.id))  # type: ignore[attr-defined]
    session.execute(delete(Page).where(Page.submission_id == sub.id))
    session.execute(delete(Document).where(Document.submission_id == sub.id))
    if run_ids:
        session.execute(delete(Step).where(Step.run_id.in_(run_ids)))
    session.execute(delete(Run).where(Run.submission_id == sub.id))
    session.execute(delete(Attachment).where(Attachment.submission_id == sub.id))
    session.execute(delete(Submission).where(Submission.id == sub.id))
    if email is not None:
        session.execute(update(Run).where(Run.email_id == email.id).values(email_id=None))
        session.execute(delete(EmailMessage).where(EmailMessage.id == email.id))
    chain.record(
        session,
        actor=actor,
        action="submission.deleted",
        subject_type="submission",
        subject_id=submission_id,
        payload={"subject": removed.subject, "files": files, "documents": len(doc_ids)},
    )
    session.commit()
    return removed


def delete_and_reingest(
    session: Session,
    store: BlobStore,
    submission_id: str,
    *,
    actor: str,
    limits: IntakeLimits,
) -> IngestResult | None:
    """Delete a submission and ingest its original email again, as if it had just arrived."""
    sub = session.get(Submission, submission_id)
    email = session.get(EmailMessage, sub.email_id) if sub else None
    blob = session.get(Blob, email.raw_sha256) if email else None
    if email is None or blob is None:
        return None
    raw = store.get(blob.storage_key)
    source = email.source
    delete_submission(session, submission_id, actor=actor)
    return ingest_email(session, store, raw, source=source, limits=limits)
