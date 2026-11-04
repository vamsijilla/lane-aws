"""The agent request and its draft email. There is no send endpoint, by design."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from lane.actions import points
from lane.actions.service import build_actions, included_sha
from lane.analyze.service import review_is_stale
from lane.api.auth import ContentViewer, Reviewer, Viewer
from lane.api.deps import SessionDep, SettingsDep
from lane.api.privacy import pii_texts, scrub
from lane.audit import chain
from lane.config import Settings
from lane.db.models import (
    AgentRequestItem,
    EmailDraft,
    RequestSnapshot,
    Submission,
)
from lane.intake.link import tag_subject
from lane.llm.gateway import LlmGateway
from lane.rules.service import ruleset

router = APIRouter(prefix="/api/v1", tags=["actions"])


class ItemOut(BaseModel):
    id: str
    ordinal: int
    group: str
    group_label: str
    text: str
    details: list[str]
    source_kind: str
    source_refs: list[str]
    included: bool
    # A recommendation from an expert review written before the agent's latest reply
    stale: bool = False


class DraftOut(BaseModel):
    version: int
    to: str | None
    subject: str
    body: str
    method: str
    model: str | None
    problems: list[str]
    created_at: datetime
    current: bool  # false when the included items changed since this draft
    sent: bool = False  # always false: the system never sends


class SentOut(BaseModel):
    """A person marked the request as sent (Lane itself never sends)."""

    id: str
    draft_version: int | None
    sent_by: str
    sent_at: datetime
    items: int  # included items in the snapshot
    current: bool  # the request has not changed since


class ActionsOut(BaseModel):
    items: list[ItemOut]
    draft: DraftOut | None
    last_sent: SentOut | None = None


class IncludeIn(BaseModel):
    included: bool


def _actions(session: SessionDep, submission_id: str) -> ActionsOut:
    groups = ruleset().request_groups
    review_stale = review_is_stale(session, submission_id)
    items = [
        ItemOut(
            id=i.id,
            ordinal=i.ordinal,
            group=i.group,
            group_label=groups.get(i.group, i.group),
            text=i.text,
            details=i.details,
            source_kind=i.source_kind,
            source_refs=i.source_refs,
            included=i.included,
            stale=i.source_kind == "finding" and review_stale,
        )
        for i in session.scalars(
            select(AgentRequestItem)
            .where(AgentRequestItem.submission_id == submission_id)
            .order_by(AgentRequestItem.ordinal)
        )
    ]
    d = session.scalars(
        select(EmailDraft)
        .where(EmailDraft.submission_id == submission_id)
        .order_by(EmailDraft.version.desc())
    ).first()
    draft = (
        DraftOut(
            version=d.version,
            to=d.to,
            subject=tag_subject(d.subject, submission_id),  # drafts stored before the reference
            body=d.body,
            method=d.method,
            model=d.model,
            problems=d.problems,
            created_at=d.created_at,
            current=d.items_sha256 == included_sha(session, submission_id),
        )
        if d
        else None
    )
    snap = session.scalars(
        select(RequestSnapshot)
        .where(RequestSnapshot.submission_id == submission_id)
        .order_by(RequestSnapshot.sent_at.desc())
        .limit(1)
    ).first()
    last_sent = (
        SentOut(
            id=snap.id,
            draft_version=snap.draft_version,
            sent_by=snap.sent_by.removeprefix("user:"),
            sent_at=snap.sent_at,
            items=sum(1 for i in snap.items if i.get("included")),
            current=snap.items == points.item_rows(session, submission_id),
        )
        if snap
        else None
    )
    return ActionsOut(items=items, draft=draft, last_sent=last_sent)


def _submission(session: SessionDep, submission_id: str) -> Submission:
    sub = session.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    return sub


@router.get("/submissions/{submission_id}/actions")
def get_actions(
    submission_id: str, session: SessionDep, settings: SettingsDep, viewer: ContentViewer
) -> ActionsOut:
    _submission(session, submission_id)
    return _presented(_actions(session, submission_id), session, settings, viewer)


def _presented(
    out: ActionsOut, session: SessionDep, settings: Settings, viewer: Viewer
) -> ActionsOut:
    # Driver identifiers are masked everywhere, the email included: a date of birth the agent is
    # asked to confirm is named by month and day only (lane/actions/build.py), so nothing is lost
    texts = pii_texts(session, settings)

    def masked(text: str) -> str:
        return scrub(text, texts)

    for item in out.items:
        item.text = masked(item.text)
        item.details = [masked(d) for d in item.details]
    if out.draft:
        out.draft.body = masked(out.draft.body)
    return out


@router.patch("/request-items/{item_id}", operation_id="set_request_item_included")
def patch_item(
    item_id: str, body: IncludeIn, session: SessionDep, settings: SettingsDep, viewer: Reviewer
) -> ItemOut:
    item = session.get(AgentRequestItem, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request item not found")
    item.included = body.included
    chain.record(
        session,
        actor=viewer.actor,
        action="agent_request.item_included" if body.included else "agent_request.item_excluded",
        subject_type="agent_request_item",
        subject_id=item.id,
        payload={"text": item.text},
    )
    session.commit()
    out = _presented(_actions(session, item.submission_id), session, settings, viewer)
    return next(i for i in out.items if i.id == item_id)


@router.post("/submissions/{submission_id}/actions/mark-sent", operation_id="mark_request_sent")
def mark_sent(
    submission_id: str, session: SessionDep, settings: SettingsDep, viewer: Reviewer
) -> ActionsOut:
    """A person says they sent the request (from their own mail). Lane records what was asked,
    who and when, so the agent's reply is measured against it. Lane sends nothing."""
    _submission(session, submission_id)
    d = session.scalars(
        select(EmailDraft)
        .where(EmailDraft.submission_id == submission_id)
        .order_by(EmailDraft.version.desc())
    ).first()
    rows = points.item_rows(session, submission_id)
    snap = RequestSnapshot(
        submission_id=submission_id,
        draft_version=d.version if d else None,
        subject=tag_subject(d.subject, submission_id) if d else None,
        items=rows,
        sent_by=viewer.actor,
    )
    session.add(snap)
    session.flush()
    chain.record(
        session,
        actor=viewer.actor,
        action="agent_request.marked_sent",
        subject_type="submission",
        subject_id=submission_id,
        payload={
            "snapshot_id": snap.id,
            "draft_version": snap.draft_version,
            "items": sum(1 for r in rows if r["included"]),
            "note": "Marked as sent by a person; Lane does not send email",
        },
    )
    session.commit()
    return _presented(_actions(session, submission_id), session, settings, viewer)


@router.post("/submissions/{submission_id}/actions/redraft", operation_id="redraft_email")
def redraft(
    submission_id: str,
    session: SessionDep,
    settings: SettingsDep,
    viewer: Reviewer,
) -> ActionsOut:
    sub = _submission(session, submission_id)
    build_actions(session, sub, settings, LlmGateway, run_id=None)
    return _presented(_actions(session, submission_id), session, settings, viewer)
