"""Submission list for the inbox."""

from datetime import UTC, datetime
from email.utils import parseaddr
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lane.api.auth import Deleter, Reviewer
from lane.api.deps import BlobStoreDep, SessionDep, SettingsDep
from lane.audit import chain
from lane.config import get_settings
from lane.db.base import utcnow
from lane.db.models import (
    Attachment,
    AttachmentRole,
    AttachmentStatus,
    AuditEvent,
    Conflict,
    DecisionRecord,
    Document,
    EmailMessage,
    MissingInfo,
    RuleConfirmation,
    Run,
    Submission,
    UnderwriterDecision,
)
from lane.documents.formats import format_note
from lane.intake import link
from lane.intake.remove import delete_and_reingest, delete_submission
from lane.intake.service import IntakeLimits
from lane.overview.service import known_values
from lane.pipeline import background as work

router = APIRouter(prefix="/api/v1", tags=["submissions"])


class LatestRun(BaseModel):
    status: str
    current_step: str | None  # the step running now, if any
    steps_done: int


class SeniorReviewOut(BaseModel):
    action: str  # quote | confirm: what needs senior authority
    by: str
    at: datetime
    note: str | None = None


class SubmissionSummary(BaseModel):
    id: str
    received_at: datetime
    subject: str
    sender_name: str | None
    sender_email: str
    status: str
    files: int
    duplicates: int  # copies of a file already received (a zip that repeats the attachments)
    archives: int  # zip files opened
    documents: int
    needs_review: int
    filename_mismatches: int
    superseded: int
    # Files that are the record their name promises, in another format ("MTC 23-24.pdf: letter,
    # not a loss run"); not misnamed
    different_formats: list[str] = Field(default_factory=lambda: list[str]())
    latest_run: LatestRun | None
    recommendation: str | None  # decline | refer | ready
    recommendation_headline: str | None
    potential_decline: bool
    # Who the submission is for and from, as resolved from the documents (None until read)
    applicant: str | None = None
    agency: str | None = None
    # How this email relates to an earlier submission (same_files | possible_follow_up |
    # kept_separate), the submission, its applicant, and why
    relation: str | None = None
    related_to: str | None = None
    related_applicant: str | None = None
    relation_reason: str | None = None
    # The latest run came from the agent's reply: points answered of points asked, and new ones
    updated_by_reply: bool = False
    # Rule changes away from decline on the agent's documents, awaiting an underwriter
    pending_confirmations: int = 0
    # Conflicts the documents still disagree on, and those a person (or a document) settled
    # What the underwriter decided (decline | refer_back | quote), who and when; None until then
    decision: str | None = None
    decision_reason: str | None = None
    decision_conditions: list[str] = Field(default_factory=lambda: list[str]())
    decided_by: str | None = None
    decided_at: datetime | None = None
    # An underwriter asked a senior underwriter to decide; cleared by the next decision
    senior_review: SeniorReviewOut | None = None
    conflicts_open: int = 0
    conflicts_resolved: int = 0
    missing: int = 0
    points_answered: int = 0
    points_requested: int = 0
    points_new: int = 0


@router.get("/submissions")
def list_submissions(session: SessionDep) -> list[SubmissionSummary]:
    """One row per submission. A reply whose files were attached to a submission is part of
    that submission's row, not a row of its own."""
    subs = session.execute(
        select(Submission, EmailMessage)
        .join(EmailMessage, Submission.email_id == EmailMessage.id)
        .where((Submission.relation.is_(None)) | (Submission.relation != "follow_up"))
        .order_by(func.coalesce(EmailMessage.sent_at, EmailMessage.received_at).desc())
    ).all()
    return [summary_of(session, sub, email) for sub, email in subs]


def _utc(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _count(session: Session, stmt: object) -> int:
    return int(session.scalar(stmt) or 0)  # type: ignore[arg-type]


def _different_formats(session: Session, submission_id: str) -> list[str]:
    settings = get_settings()
    out: list[str] = []
    for filename, hint, doc_type in session.execute(
        select(Attachment.filename, Document.filename_hint, Document.doc_type)
        .join(Attachment, Attachment.id == Document.attachment_id)
        .where(Document.submission_id == submission_id, Document.filename_mismatch.is_(False))
        .order_by(Document.ordinal)
    ):
        note = format_note(settings, hint, doc_type)
        if note:
            out.append(f"{filename}: {note.removeprefix('Different format: ')}")
    return list(dict.fromkeys(out))


def summary_of(session: Session, sub: Submission, email: EmailMessage) -> SubmissionSummary:
    docs = select(func.count()).select_from(Document).where(Document.submission_id == sub.id)
    run = session.scalars(
        select(Run)
        .where(Run.submission_id == sub.id, Run.trigger != "replay")  # a replay changes nothing
        .order_by(Run.started_at.desc())
        .limit(1)
    ).first()
    latest = None
    if run is not None:
        running = next((s.name for s in run.steps if s.status.value == "running"), None)
        done = sum(1 for s in run.steps if s.status.value != "running")
        latest = LatestRun(status=run.status.value, current_step=running, steps_done=done)
    name, _addr = parseaddr(email.headers.get("From", ""))
    decision = session.get(DecisionRecord, sub.id)
    known = known_values(session, sub.id)
    pts: dict[str, Any] = {}
    if run is not None and run.trigger == "follow_up":
        actions = next((s for s in run.steps if s.name == "actions"), None)
        pts = ((actions.summary or {}).get("points") or {}) if actions else {}
    pending = _count(
        session,
        select(func.count())
        .select_from(RuleConfirmation)
        .where(RuleConfirmation.submission_id == sub.id, RuleConfirmation.status == "pending"),
    )
    decided = session.scalars(
        select(UnderwriterDecision)
        .where(UnderwriterDecision.submission_id == sub.id)
        .order_by(UnderwriterDecision.decided_at.desc())
        .limit(1)
    ).first()
    resolutions = list(
        session.execute(
            select(Conflict.resolution, Conflict.type).where(Conflict.submission_id == sub.id)
        )
    )
    # Settled by a person, a document, or code (a VIN's own decode), as DATA-01 counts them
    settled = sum(1 for r, kind in resolutions if r or kind == "reference")
    related_applicant = (
        known_values(session, sub.related_to_id).get(("applicant", "legal_name"))
        if sub.related_to_id
        else None
    )
    return SubmissionSummary(
        applicant=known.get(("applicant", "legal_name")),
        agency=known.get(("agency", "name")),
        relation=sub.relation,
        related_to=sub.related_to_id,
        related_applicant=related_applicant,
        relation_reason=sub.relation_reason,
        updated_by_reply=run is not None and run.trigger == "follow_up",
        points_answered=int(pts.get("answered", 0)),
        points_requested=int(pts.get("requested", 0)),
        points_new=len(pts.get("new", [])),
        id=sub.id,
        # When the agent sent it (the Date header); when Lane ingested it stays in the audit.
        # SQLite drops tz info; stored values are UTC.
        received_at=_utc(email.sent_at or email.received_at),
        subject=email.subject,
        sender_name=name or None,
        sender_email=email.from_addr,
        status=sub.status.value,
        files=_count(
            session,
            select(func.count())
            .select_from(Attachment)
            .where(
                Attachment.submission_id == sub.id,
                Attachment.status == AttachmentStatus.ACCEPTED,
                Attachment.role != AttachmentRole.EMAIL_BODY,  # the agent's files, not the message
            ),
        ),
        duplicates=_count(
            session,
            select(func.count())
            .select_from(Attachment)
            .where(
                Attachment.submission_id == sub.id,
                Attachment.status == AttachmentStatus.DUPLICATE,
            ),
        ),
        archives=_count(
            session,
            select(func.count())
            .select_from(Attachment)
            .where(
                Attachment.submission_id == sub.id,
                Attachment.status == AttachmentStatus.EXPANDED,
            ),
        ),
        documents=_count(session, docs),
        needs_review=_count(session, docs.where(Document.needs_review.is_(True))),
        filename_mismatches=_count(  # files, not documents: one misnamed PDF can hold many
            session,
            select(func.count(func.distinct(Document.attachment_id))).where(
                Document.submission_id == sub.id, Document.filename_mismatch.is_(True)
            ),
        ),
        superseded=_count(session, docs.where(Document.superseded_by_id.is_not(None))),
        different_formats=_different_formats(session, sub.id),
        latest_run=latest,
        recommendation=decision.outcome if decision else None,
        recommendation_headline=decision.headline if decision else None,
        # Still a potential decline until an underwriter confirms what cleared it
        potential_decline=bool(decision and decision.potential_decline) or pending > 0,
        pending_confirmations=pending,
        decision=decided.decision if decided else None,
        decision_reason=decided.reason if decided else None,
        decision_conditions=list(decided.conditions) if decided else [],
        decided_by=decided.decided_by.removeprefix("user:") if decided else None,
        decided_at=decided.decided_at if decided else None,
        senior_review=_senior_review(session, sub.id),
        conflicts_open=len(resolutions) - settled,
        conflicts_resolved=settled,
        missing=_count(
            session,
            select(func.count())
            .select_from(MissingInfo)
            .where(MissingInfo.submission_id == sub.id),
        ),
    )


@router.delete(
    "/submissions/{submission_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_submission",
)
def remove_submission(submission_id: str, session: SessionDep, viewer: Deleter) -> Response:
    """Delete a submission and everything derived from it. The audit log keeps a record."""
    if delete_submission(session, submission_id, actor=viewer.actor) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class RestartedOut(BaseModel):
    submission_id: str


@router.post(
    "/submissions/{submission_id}/restart",
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="restart_submission",
)
def restart_submission(
    submission_id: str,
    session: SessionDep,
    store: BlobStoreDep,
    settings: SettingsDep,
    background: BackgroundTasks,
    viewer: Deleter,
) -> RestartedOut:
    """Delete the submission, ingest its original email again with every file, and process it."""
    result = delete_and_reingest(
        session,
        store,
        submission_id,
        actor=viewer.actor,
        limits=IntakeLimits.from_settings(settings),
    )
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    if result.process_id:
        work.start(background, session, "process", result.process_id, trigger=result.trigger)
    return RestartedOut(submission_id=result.submission_id)


class LinkedOut(BaseModel):
    submission_id: str  # the submission to open now
    relation: str


@router.post("/submissions/{submission_id}/attach", operation_id="attach_follow_up")
def attach_follow_up(
    submission_id: str, session: SessionDep, background: BackgroundTasks, viewer: Reviewer
) -> LinkedOut:
    """A person confirms a suggested follow-up: its files join the earlier submission, which
    runs again."""
    sub = session.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    target = session.get(Submission, sub.related_to_id) if sub.related_to_id else None
    if sub.relation != "possible_follow_up" or target is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This email is not a suggested follow-up")
    placed = link.attach(session, sub, target, actor=viewer.actor)
    session.commit()
    assert placed.process_id is not None
    work.start(background, session, "process", placed.process_id, trigger=placed.trigger)
    return LinkedOut(submission_id=target.id, relation="follow_up")


@router.post("/submissions/{submission_id}/keep-separate", operation_id="keep_separate")
def keep_separate(
    submission_id: str, session: SessionDep, background: BackgroundTasks, viewer: Reviewer
) -> LinkedOut:
    """A person says a suggested follow-up is a submission of its own: it is processed as one."""
    sub = session.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    if sub.relation != "possible_follow_up":
        raise HTTPException(status.HTTP_409_CONFLICT, "This email is not a suggested follow-up")
    placed = link.keep_separate(session, sub, actor=viewer.actor)
    session.commit()
    work.start(background, session, "process", sub.id, trigger=placed.trigger)
    return LinkedOut(submission_id=sub.id, relation="kept_separate")


def _senior_review(session: Session, submission_id: str) -> SeniorReviewOut | None:
    """The latest open request for senior review: none once a decision or confirmation follows."""
    latest = session.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.subject_id == submission_id,
            AuditEvent.action.in_(
                ("decision.senior_review_requested", "decision.recorded", "rule_change.confirmed")
            ),
        )
        .order_by(AuditEvent.seq.desc())
        .limit(1)
    ).first()
    if latest is None or latest.action != "decision.senior_review_requested":
        return None
    return SeniorReviewOut(
        action=str(latest.payload.get("action", "quote")),
        by=latest.actor.removeprefix("user:"),
        at=latest.at,
        note=latest.payload.get("note") or None,
    )


def at_risk(session: Session, submission_id: str) -> bool:
    """Lane's route is a decline or potential decline, or a cleared decline awaits confirmation:
    proceeding to quote then needs senior authority (settings.decision_authority)."""
    rec = session.get(DecisionRecord, submission_id)
    pending = session.scalar(
        select(func.count())
        .select_from(RuleConfirmation)
        .where(
            RuleConfirmation.submission_id == submission_id,
            RuleConfirmation.status == "pending",
        )
    )
    return bool(rec and (rec.potential_decline or rec.outcome == "decline")) or bool(pending)


NEEDS_SENIOR = "Needs senior underwriter authority"


class SeniorReviewIn(BaseModel):
    action: str = "quote"  # quote | confirm
    note: str | None = None


@router.post("/submissions/{submission_id}/senior-review", operation_id="request_senior_review")
def request_senior_review(
    submission_id: str, body: SeniorReviewIn, session: SessionDep, viewer: Reviewer
) -> SubmissionSummary:
    """An underwriter without the authority asks a senior underwriter to decide."""
    sub = session.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    if body.action not in ("quote", "confirm"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown action")
    chain.record(
        session,
        actor=viewer.actor,
        action="decision.senior_review_requested",
        subject_type="submission",
        subject_id=submission_id,
        payload={"action": body.action, "note": (body.note or "").strip() or None},
    )
    session.commit()
    return summary_of(session, sub, sub.email)


class ConfirmedOut(BaseModel):
    rule_id: str
    status: str


@router.post("/rule-confirmations/{confirmation_id}/confirm", operation_id="confirm_rule_change")
def confirm_rule_change(
    confirmation_id: str, session: SessionDep, settings: SettingsDep, viewer: Reviewer
) -> ConfirmedOut:
    """A senior underwriter accepts a rule outcome the agent's documents moved away from decline
    (four-eyes on clearing a decline)."""
    c = session.get(RuleConfirmation, confirmation_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nothing to confirm")
    if not viewer.may_decide("confirm_cleared_decline", settings):
        raise HTTPException(status.HTTP_403_FORBIDDEN, NEEDS_SENIOR)
    if c.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This change is {c.status}")
    c.status = "confirmed"
    c.decided_by = viewer.actor
    c.decided_at = utcnow()
    chain.record(
        session,
        actor=viewer.actor,
        action="rule_change.confirmed",
        subject_type="submission",
        subject_id=c.submission_id,
        payload={"rule_id": c.rule_id, "from": c.before, "to": c.after, "run_id": c.run_id},
    )
    session.commit()
    return ConfirmedOut(rule_id=c.rule_id, status=c.status)


DECISIONS = {"decline": "Decline", "refer_back": "Refer back to agent", "quote": "Proceed to quote"}


class DecisionIn(BaseModel):
    decision: str  # decline | refer_back | quote
    reason: str
    conditions: list[str] = Field(default_factory=lambda: list[str]())


@router.post("/submissions/{submission_id}/decision", operation_id="record_decision")
def record_decision(
    submission_id: str,
    body: DecisionIn,
    session: SessionDep,
    settings: SettingsDep,
    viewer: Reviewer,
) -> SubmissionSummary:
    """The underwriter decides. Lane's recommendation stays beside it, unchanged."""
    sub = session.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    if body.decision not in DECISIONS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown decision")
    if not body.reason.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A reason is required")
    needed = (
        "quote_at_risk"
        if body.decision == "quote" and at_risk(session, submission_id)
        else body.decision
    )
    if not viewer.may_decide(needed, settings):
        raise HTTPException(status.HTTP_403_FORBIDDEN, NEEDS_SENIOR)
    rec = session.get(DecisionRecord, submission_id)
    d = UnderwriterDecision(
        submission_id=submission_id,
        decision=body.decision,
        reason=body.reason.strip(),
        conditions=[c.strip() for c in body.conditions if c.strip()],
        recommendation=rec.headline if rec else None,
        decided_by=viewer.actor,
    )
    session.add(d)
    session.flush()
    chain.record(
        session,
        actor=viewer.actor,
        action="decision.recorded",
        subject_type="submission",
        subject_id=submission_id,
        payload={
            "decision": d.decision,
            "reason": d.reason,
            "conditions": d.conditions,
            "lane_recommendation": d.recommendation,
        },
    )
    session.commit()
    return summary_of(session, sub, sub.email)
