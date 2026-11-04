"""The audit trail for one submission, with verification of the whole hash chain."""

from datetime import datetime
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from lane.api.deps import SessionDep, SettingsDep
from lane.api.privacy import masker_for
from lane.audit import chain, describe
from lane.db.models import (
    AgentRequestItem,
    Attachment,
    AuditEvent,
    Document,
    FindingRecord,
    ResolvedField,
    Run,
    Step,
    Submission,
)

router = APIRouter(prefix="/api/v1", tags=["audit"])

# Payload keys that carry a field's value; masked when the field holds personal data
VALUE_KEYS = {"chosen", "before", "after", "was"}


class AuditEventOut(BaseModel):
    seq: int
    at: datetime
    actor: str
    action: str
    kind: Literal["human", "security", "model", "system"]
    summary: str  # one plain sentence, personal data masked
    subject_type: str
    subject_id: str
    payload: dict[str, Any]
    hash: str


class ChainOut(BaseModel):
    verified: bool
    events: int
    first_bad_seq: int | None
    reason: str | None


class AuditOut(BaseModel):
    chain: ChainOut
    events: list[AuditEventOut]
    next_before: int | None  # pass as ?before= for the next page


def subject_ids(session: SessionDep, sid: str) -> set[str]:
    """Everything an event about this submission can be about."""
    ids = {sid}
    run_ids = set(session.scalars(select(Run.id).where(Run.submission_id == sid)))
    ids |= run_ids
    if run_ids:
        ids |= set(session.scalars(select(Step.id).where(Step.run_id.in_(run_ids))))
    for model in (Attachment, Document, FindingRecord, AgentRequestItem, ResolvedField):
        ids |= set(session.scalars(select(model.id).where(model.submission_id == sid)))
    return ids


@router.get("/submissions/{submission_id}/audit", operation_id="get_audit")
def get_audit(
    submission_id: str,
    session: SessionDep,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    before: int | None = None,
) -> AuditOut:
    if session.get(Submission, submission_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    query = (
        select(AuditEvent)
        .where(AuditEvent.subject_id.in_(subject_ids(session, submission_id)))
        .order_by(AuditEvent.seq.desc())
        .limit(limit + 1)
    )
    if before is not None:
        query = query.where(AuditEvent.seq < before)
    rows = list(session.scalars(query))
    page, more = rows[:limit], len(rows) > limit
    mask = masker_for(session, settings, submission_id)

    def masked(attribute: str | None, value: Any) -> str:
        pii = mask.attribute(attribute) if attribute else None
        return str(mask.value(pii, value))

    def payload(p: dict[str, Any]) -> dict[str, Any]:
        """Mask a field's value carried under a neutral key (chosen, before, after)."""
        pii = mask.attribute(str(p["attribute"])) if p.get("attribute") else None
        out = {k: mask.value(pii, v) if pii and k in VALUE_KEYS else v for k, v in p.items()}
        return cast(dict[str, Any], mask.tree(out))

    v = chain.verify(session)
    return AuditOut(
        chain=ChainOut(
            verified=v.ok, events=v.events, first_bad_seq=v.first_bad_seq, reason=v.reason
        ),
        events=[
            AuditEventOut(
                seq=e.seq,
                at=e.at,
                actor=e.actor,
                action=e.action,
                kind=describe.kind(e.action, e.actor, e.payload),
                summary=describe.summary(e.action, e.payload, masked),
                subject_type=e.subject_type,
                subject_id=e.subject_id,
                payload=payload(e.payload),
                hash=e.hash,
            )
            for e in page
        ],
        next_before=page[-1].seq if more and page else None,
    )
