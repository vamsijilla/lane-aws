"""The decision brief: GET /submissions/{id}."""

import hashlib
from dataclasses import asdict
from datetime import datetime
from typing import Any, cast

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lane.analyze.service import review_is_stale
from lane.api.analysis import analysis_message
from lane.api.auth import ContentViewer
from lane.api.deps import BlobStoreDep, SessionDep, SettingsDep
from lane.api.models import FootnoteOut, SentenceOut, footnotes, sentences
from lane.api.privacy import pii_texts, scrub
from lane.api.submissions import SubmissionSummary, summary_of
from lane.db.models import (
    AnalysisRecord,
    Attachment,
    AttachmentStatus,
    AuditEvent,
    Blob,
    DecisionRecord,
    Document,
    EmailMessage,
    RuleConfirmation,
    RuleOutcomeRecord,
    Run,
    RunStatus,
    Step,
    Submission,
)
from lane.intake.eml import parse_eml
from lane.overview.service import (
    action_counts,
    anchor_of,
    blockers,
    checks_at_quote,
    conflicted_fields,
    key_numbers,
    known_values,
    run_cost,
)
from lane.rules.service import outcome_counts, ruleset
from lane.security import injection

router = APIRouter(prefix="/api/v1", tags=["overview"])


# Rule outcomes in the status words the screens use
_OUTCOME_WORDS = {
    "unknown": "can't check yet",
    "potential_decline": "potential decline",
    "flag": "note",
    "n/a": "not applicable",
}


class RouteOut(BaseModel):
    outcome: str  # decline | refer | ready
    headline: str
    team: str | None
    team_label: str | None
    reason: str
    stable: bool
    potential_decline: bool
    anchor: str
    anchor_date: str
    ruleset_version: str
    counts: dict[str, int]
    checks_at_quote: list[str]  # external checks still open (A07), e.g. "SAFER record"
    # Rules the agent's documents moved away from decline, awaiting an underwriter
    pending: list[str] = []


class SummaryOut(BaseModel):
    status: str  # ok | degraded | not_run
    message: str | None
    sentences: list[SentenceOut]
    footnotes: list[FootnoteOut]
    # Written before the agent's latest reply: shown as an earlier review, never as current
    stale: bool = False


class BlockerOut(BaseModel):
    kind: str
    title: str
    detail: str
    rule_id: str | None
    conflict_id: str | None
    field_id: str | None
    headline: str | None


class KeyNumberOut(BaseModel):
    key: str
    label: str
    value: str
    note: str | None
    conflict: bool
    field_id: str | None
    entity_ids: list[str]


class ActionsPreview(BaseModel):
    items_by_group: dict[str, int]
    group_labels: dict[str, str]
    draft_current: bool | None  # None when no draft exists yet


class ApplicantOut(BaseModel):
    legal_name: str | None
    dot_number: str | None
    mc_number: str | None
    mailing_city: str | None
    mailing_state: str | None
    effective_date: str | None
    agency: str | None
    agency_contact: str | None
    conflicted: list[str]  # "agency.name": shown with a conflict marker, first value only


class SecurityNoteOut(BaseModel):
    filename: str
    page: int
    text: str
    at: str


class EmailOut(BaseModel):
    """One email that went into this submission: the original, then each attached reply."""

    id: str
    subject: str
    sender: str
    received_at: str
    role: str  # original | reply
    how: str | None  # reply matched by: reference | thread | identity | person
    reason: str | None
    run_id: str | None  # the run it started


class RuleDiffOut(BaseModel):
    rule_id: str
    title: str
    before: str | None
    after: str
    was_potential_decline: bool
    potential_decline: bool
    # Moved away from decline or potential decline on the agent's document: an underwriter
    # confirms it (pending | confirmed | void), or None when no confirmation is needed
    confirmation_id: str | None = None
    confirmation: str | None = None
    confirmed_by: str | None = None


class PointLines(BaseModel):
    state: str  # answered | open
    lines: list[str]


class ItemPointsOut(BaseModel):
    item: str
    answered: int
    total: int
    points: list[PointLines]


class GroupPointsOut(BaseModel):
    group: str
    label: str
    answered: int
    requested: int
    items: list[ItemPointsOut]


class LinesOut(BaseModel):
    item: str
    lines: list[str]


class RouteLine(BaseModel):
    headline: str | None
    potential_decline: bool


class AmendmentOut(BaseModel):
    """What the latest reply from the agent changed, measured question by question against
    what the agent was asked: the request marked sent, or the last draft when none was."""

    email_subject: str | None
    run_id: str
    # The reply's run has not finished: nothing below is measured yet, so none of it is shown
    in_progress: bool
    rule_changes: list[RuleDiffOut]
    route_before: RouteLine | None
    route_after: RouteLine
    route_pending: bool  # the route change rests on a rule change awaiting confirmation
    basis: str  # sent | draft
    basis_by: str | None
    basis_at: datetime | None
    basis_version: int | None
    answered: int
    requested: int
    groups: list[GroupPointsOut]
    answered_not_requested: list[LinesOut]  # switched off in the request, settled anyway
    new: list[LinesOut]


class OverviewOut(BaseModel):
    submission: SubmissionSummary
    emails: list[EmailOut]
    amendment: AmendmentOut | None
    applicant: ApplicantOut
    route: RouteOut | None
    summary: SummaryOut
    blockers: list[BlockerOut]
    key_numbers: list[KeyNumberOut]
    actions: ActionsPreview
    cost_usd: float
    security_notes: list[SecurityNoteOut]  # instructions found inside documents, treated as data


@router.get("/submissions/{submission_id}", operation_id="get_overview")
def get_overview(submission_id: str, session: SessionDep, settings: SettingsDep) -> OverviewOut:
    sub = session.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    d = session.get(DecisionRecord, submission_id)
    a = session.get(AnalysisRecord, submission_id)
    counts, draft_current = action_counts(session, submission_id)
    known = known_values(session, submission_id)
    return OverviewOut(
        submission=summary_of(session, sub, sub.email),
        emails=emails_of(session, sub),
        amendment=amendment_of(session, sub, pii_texts(session, settings)),
        applicant=ApplicantOut(
            legal_name=known.get(("applicant", "legal_name")),
            dot_number=known.get(("applicant", "dot_number")),
            mc_number=known.get(("applicant", "mc_number")),
            mailing_city=known.get(("applicant", "mailing_city")),
            mailing_state=known.get(("applicant", "mailing_state")),
            effective_date=known.get(("applicant", "effective_date")),
            agency=known.get(("agency", "name")),
            agency_contact=known.get(("agency", "contact")),
            conflicted=conflicted_fields(session, submission_id),
        ),
        route=RouteOut(
            outcome=d.outcome,
            headline=d.headline,
            team=d.team,
            team_label=d.team_label,
            reason=d.route_reason,
            stable=d.route_stable,
            potential_decline=d.potential_decline,
            anchor=d.anchor,
            anchor_date=d.anchor_date,
            ruleset_version=d.ruleset_version,
            counts=outcome_counts(
                list(
                    session.scalars(
                        select(RuleOutcomeRecord).where(
                            RuleOutcomeRecord.submission_id == d.submission_id
                        )
                    )
                )
            ),
            checks_at_quote=checks_at_quote(session, submission_id),
            pending=[
                f"{c.rule_id} {c.title}: {_OUTCOME_WORDS.get(c.before, c.before)} to "
                f"{_OUTCOME_WORDS.get(c.after, c.after)}"
                for c in session.scalars(
                    select(RuleConfirmation).where(
                        RuleConfirmation.submission_id == submission_id,
                        RuleConfirmation.status == "pending",
                    )
                )
            ],
        )
        if d
        else None,
        summary=SummaryOut(
            status=a.status if a else "not_run",
            message=analysis_message(a) if a else None,
            sentences=sentences(a.summary) if a else [],
            footnotes=footnotes(a.footnotes) if a else [],
            stale=review_is_stale(session, submission_id),
        ),
        blockers=[BlockerOut(**asdict(b)) for b in blockers(session, submission_id)],
        key_numbers=[
            KeyNumberOut(**asdict(k))
            for k in key_numbers(session, submission_id, anchor_of(session, submission_id))
        ],
        actions=ActionsPreview(
            items_by_group=counts,
            group_labels=ruleset().request_groups,
            draft_current=draft_current,
        ),
        cost_usd=run_cost(session, submission_id),
        security_notes=[
            SecurityNoteOut(
                filename=str(e.payload.get("filename", "")),
                page=int(e.payload.get("page", 0)),
                text=(
                    f"Hidden text removed ({e.payload.get('reason')}): {e.payload.get('text', '')}"
                    if e.action == injection.HIDDEN_ACTION
                    else str(e.payload.get("text", ""))
                ),
                at=e.at.isoformat(),
            )
            for e in session.scalars(
                select(AuditEvent)
                .where(AuditEvent.action.in_((injection.ACTION, injection.HIDDEN_ACTION)))
                .order_by(AuditEvent.seq)
            )
            if e.payload.get("submission_id") == submission_id
        ],
    )


def emails_of(session: Session, sub: Submission) -> list[EmailOut]:
    replies = session.scalars(
        select(Submission)
        .where(Submission.related_to_id == sub.id, Submission.relation == "follow_up")
        .order_by(Submission.relation_at)
    ).all()
    runs = {
        r.email_id: r.id
        for r in session.scalars(
            select(Run).where(Run.submission_id == sub.id).order_by(Run.started_at)
        )
        if r.email_id
    }
    out = [
        EmailOut(
            id=sub.email.id,
            subject=sub.email.subject,
            sender=sub.email.from_addr,
            received_at=(sub.email.sent_at or sub.email.received_at).isoformat(),
            role="original",
            how=None,
            reason=None,
            run_id=runs.get(sub.email.id),
        )
    ]
    out += [
        EmailOut(
            id=r.email.id,
            subject=r.email.subject,
            sender=r.email.from_addr,
            received_at=(r.email.sent_at or r.email.received_at).isoformat(),
            role="reply",
            how=r.relation_how,
            reason=r.relation_reason,
            run_id=runs.get(r.email.id),
        )
        for r in replies
    ]
    return out


def amendment_of(
    session: Session, sub: Submission, masked: dict[str, str] | None = None
) -> AmendmentOut | None:
    """The rules diff and the request items answered by the latest follow-up run."""
    run = session.scalars(
        select(Run)
        .where(Run.submission_id == sub.id, Run.trigger == "follow_up")
        .order_by(Run.started_at.desc())
        .limit(1)
    ).first()
    if run is None:
        return None
    steps = {s.name: s.summary or {} for s in run.steps}
    rules = steps.get("rules", {})
    pts = cast(dict[str, Any], steps.get("actions", {}).get("points") or {})
    m = masked or {}

    def lines(ls: list[str]) -> list[str]:
        # Request lines can name driver identifiers: masked here as on the request itself
        return [scrub(x, m) for x in ls]

    def listed(key: str) -> list[LinesOut]:
        return [
            LinesOut(item=scrub(str(p["item"]), m), lines=lines(list(p["lines"])))
            for p in cast(list[dict[str, Any]], pts.get(key, []))
        ]

    labels = ruleset().request_groups
    groups = [
        GroupPointsOut(
            group=str(g["group"]),
            label=labels.get(str(g["group"]), str(g["group"])),
            answered=int(g["answered"]),
            requested=int(g["requested"]),
            items=[
                ItemPointsOut(
                    item=scrub(str(it["item"]), m),
                    answered=int(it["answered"]),
                    total=len(it["points"]),
                    points=[
                        PointLines(state=str(p["state"]), lines=lines(list(p["lines"])))
                        for p in it["points"]
                    ],
                )
                for it in cast(list[dict[str, Any]], g["items"])
            ],
        )
        for g in cast(list[dict[str, Any]], pts.get("groups", []))
    ]
    confirmations = {
        c.rule_id: c
        for c in session.scalars(
            select(RuleConfirmation)
            .where(RuleConfirmation.run_id == run.id)
            .order_by(RuleConfirmation.created_at)
        )
    }

    def change(c: dict[str, Any]) -> RuleDiffOut:
        conf = confirmations.get(str(c["rule_id"]))
        return RuleDiffOut(
            **c,
            confirmation_id=conf.id if conf else None,
            confirmation=conf.status if conf else None,
            confirmed_by=conf.decided_by.removeprefix("user:")
            if conf and conf.decided_by
            else None,
        )

    before = session.scalars(
        select(Step)
        .join(Run, Run.id == Step.run_id)
        .where(
            Run.submission_id == sub.id,
            Run.started_at < run.started_at,
            Run.trigger != "replay",
            Step.name == "rules",
        )
        .order_by(Step.started_at.desc())
        .limit(1)
    ).first()
    was = before.summary or {} if before else None
    email = session.get(EmailMessage, run.email_id) if run.email_id else None
    at = pts.get("basis_at")
    return AmendmentOut(
        email_subject=email.subject if email else None,
        run_id=run.id,
        in_progress=run.status == RunStatus.RUNNING,
        rule_changes=[change(c) for c in rules.get("changes", [])],
        route_before=RouteLine(
            headline=was.get("headline"), potential_decline=bool(was.get("potential_decline"))
        )
        if was
        else None,
        route_after=RouteLine(
            headline=rules.get("headline"), potential_decline=bool(rules.get("potential_decline"))
        ),
        route_pending=any(c.status == "pending" for c in confirmations.values()),
        basis=str(pts.get("basis", "draft")),
        basis_by=pts.get("basis_by"),
        basis_at=datetime.fromisoformat(at) if at else None,
        basis_version=pts.get("basis_version"),
        answered=int(pts.get("answered", 0)),
        requested=int(pts.get("requested", 0)),
        groups=groups,
        answered_not_requested=listed("answered_not_requested"),
        new=listed("new"),
    )


class EmailAttachmentOut(BaseModel):
    """A file as the email carried it, and what Lane made of it."""

    filename: str
    attachment_id: str | None  # the file on the Documents tab, when Lane kept it
    documents: int  # documents read from it (a file can hold several)
    duplicate_of: str | None  # the earlier copy it repeats, by file name
    note: str | None  # "Archive of 15 files", "Not read: ..."


class EmailDetailOut(BaseModel):
    id: str
    role: str  # original | reply
    sender: str
    to: list[str]
    sent_at: str
    subject: str
    body: str
    how: str | None  # a reply's match: reference | thread | identity | name | person
    run_id: str | None
    answered: int | None  # for a reply: points of the agent request it answered
    requested: int | None
    attachments: list[EmailAttachmentOut]


@router.get("/submissions/{submission_id}/emails/{email_id}", operation_id="get_submission_email")
def get_email(
    submission_id: str,
    email_id: str,
    session: SessionDep,
    settings: SettingsDep,
    store: BlobStoreDep,
    viewer: ContentViewer,
) -> EmailDetailOut:
    """One email of the submission as it arrived, read from the stored message itself."""
    sub = session.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    listed = next((e for e in emails_of(session, sub) if e.id == email_id), None)
    email = session.get(EmailMessage, email_id)
    blob = session.get(Blob, email.raw_sha256) if email else None
    if listed is None or email is None or blob is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Email not found in this submission")
    parsed = parse_eml(store.get(blob.storage_key))

    files = list(
        session.scalars(select(Attachment).where(Attachment.submission_id == submission_id))
    )
    # Copies of each file, in the order they arrived. The original's parts take them from the
    # first; a reply's from the last (a file it resends is the later, duplicate copy)
    by_hash: dict[str, list[Attachment]] = {}
    for a in sorted(files, key=lambda f: f.ordinal):
        if a.blob_sha256:
            by_hash.setdefault(a.blob_sha256, []).append(a)
    from_end = listed.role == "reply"
    names = {a.id: a.filename for a in files}
    docs = {
        aid: n
        for aid, n in session.execute(
            select(Document.attachment_id, func.count())
            .where(Document.submission_id == submission_id)
            .group_by(Document.attachment_id)
        )
    }

    def described(filename: str, data: bytes) -> EmailAttachmentOut:
        same = by_hash.get(hashlib.sha256(data).hexdigest(), [])
        a = (same.pop() if from_end else same.pop(0)) if same else None
        if a is None:
            return EmailAttachmentOut(
                filename=filename, attachment_id=None, documents=0, duplicate_of=None, note=None
            )
        members = [m for m in files if m.parent_id == a.id]
        repeats = sum(m.status is AttachmentStatus.DUPLICATE for m in members)
        note = (
            f"Archive of {len(members)} files"
            + (f", {repeats} of them duplicates of files already attached" if repeats else "")
            if a.status is AttachmentStatus.EXPANDED
            else f"Not read: {a.reason}"
            if a.status is AttachmentStatus.REJECTED and a.reason
            else None
        )
        copy_of = a.duplicate_of_id if a.status is AttachmentStatus.DUPLICATE else None
        return EmailAttachmentOut(
            filename=filename,
            attachment_id=copy_of or a.id,
            documents=docs.get(copy_of or a.id, 0),
            duplicate_of=names.get(copy_of) if copy_of else None,
            note=note,
        )

    answered = requested = None
    if listed.run_id and listed.role == "reply":
        step = session.scalars(
            select(Step).where(Step.run_id == listed.run_id, Step.name == "actions")
        ).first()
        pts = cast(dict[str, Any], (step.summary or {}).get("points") or {}) if step else {}
        if pts:
            answered, requested = int(pts.get("answered", 0)), int(pts.get("requested", 0))

    # Driver identifiers the agent wrote out are masked, as on the draft and the request
    texts = pii_texts(session, settings)
    return EmailDetailOut(
        id=email.id,
        role=listed.role,
        sender=email.from_addr,
        to=list(email.to_addrs),
        sent_at=listed.received_at,
        subject=scrub(email.subject, texts),
        body=scrub(parsed.body_text, texts),
        how=listed.how,
        run_id=listed.run_id,
        answered=answered,
        requested=requested,
        attachments=[described(p.filename, p.data) for p in parsed.parts],
    )
