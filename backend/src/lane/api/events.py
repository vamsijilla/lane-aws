"""Live progress for one submission as Server-Sent Events.

The stream opens with a snapshot of what already exists (the email, attachment tiles, the
latest run, the decision), then sends each pipeline event as it happens. Clients resume with
Last-Event-ID after a disconnect. The stream closes a few seconds after the run finishes.
Polling GET /submissions/{id}/runs remains the fallback.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.api.deps import SessionDep, SettingsDep
from lane.db.models import Attachment, DecisionRecord, Run, Submission
from lane.events.bus import BUS

router = APIRouter(prefix="/api/v1", tags=["events"])


def _sse(event: str, data: dict[str, Any], seq: int | None = None) -> str:
    head = f"id: {seq}\n" if seq is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def snapshot(session: Session, sub: Submission) -> dict[str, Any]:
    email = sub.email
    attachments = session.scalars(
        select(Attachment).where(Attachment.submission_id == sub.id).order_by(Attachment.ordinal)
    ).all()
    run = session.scalars(
        select(Run).where(Run.submission_id == sub.id).order_by(Run.started_at.desc())
    ).first()
    decision = session.get(DecisionRecord, sub.id)
    return {
        "submission_id": sub.id,
        "status": sub.status.value,
        "email": {
            "subject": email.subject,
            "from": email.from_addr,
            "received_at": (email.sent_at or email.received_at).isoformat(),
        },
        "attachments": [
            {
                "id": a.id,
                "filename": a.filename,
                "role": a.role.value,
                "status": a.status.value,
                "size": a.blob.size if a.blob else None,
                "kind": a.blob.kind if a.blob else None,
                "parent_id": a.parent_id,
                "duplicate_of_id": a.duplicate_of_id,
                "reason": a.reason,
            }
            for a in attachments
        ],
        "run": {
            "id": run.id,
            "status": run.status.value,
            "steps": [{"name": s.name, "status": s.status.value} for s in run.steps],
        }
        if run
        else None,
        "decision": {"outcome": decision.outcome, "headline": decision.headline}
        if decision
        else None,
    }


@router.get(
    "/submissions/{submission_id}/events",
    operation_id="stream_events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "Event stream"}},
)
async def stream_events(
    submission_id: str,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    sub = session.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    resume = int(last_event_id) if last_event_id and last_event_id.isdigit() else None
    start = resume if resume is not None else BUS.last_seq(submission_id)
    first = None if resume is not None else snapshot(session, sub)

    async def events() -> AsyncIterator[str]:
        after = start
        if first is not None:
            yield _sse("snapshot", first, after)
        quiet = 0.0
        since_beat = 0.0
        while not await request.is_disconnected():
            batch, finished = BUS.since(submission_id, after)
            for e in batch:
                yield _sse(e.type, {**e.data, "at": e.at}, e.seq)
                after = e.seq
            quiet = 0.0 if batch or not finished else quiet + settings.events_poll_s
            if quiet >= settings.events_idle_close_s:
                yield _sse("end", {"reason": "run finished"}, after)
                return
            since_beat += settings.events_poll_s
            if since_beat >= settings.events_heartbeat_s:
                since_beat = 0.0
                yield ": keep-alive\n\n"
            await asyncio.sleep(settings.events_poll_s)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
