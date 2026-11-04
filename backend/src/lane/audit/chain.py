"""Hash-chained audit log.

Every event's hash covers the previous event's hash plus this event's content, so editing or
deleting any past event breaks verification from that point on.

On Postgres several processes write (the API, workers); each append first takes a transaction
lock, so appends take turns and none links to a tip another writer has already moved past.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from lane.db.base import utcnow
from lane.db.models import AuditEvent

GENESIS = "0" * 64
# Postgres advisory lock key for appending to the chain (any fixed number; "LANE" in ASCII)
APPEND_LOCK = 0x4C414E45


def _canonical_time(at: datetime) -> str:
    # SQLite returns naive datetimes; treat naive as UTC so hashes are stable across backends.
    aware = at if at.tzinfo else at.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat(timespec="microseconds")


def compute_hash(
    *,
    prev_hash: str,
    at: datetime,
    actor: str,
    action: str,
    subject_type: str,
    subject_id: str,
    payload: dict[str, Any],
) -> str:
    body = json.dumps(
        {
            "prev": prev_hash,
            "at": _canonical_time(at),
            "actor": actor,
            "action": action,
            "subject": [subject_type, subject_id],
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def record(
    session: Session,
    *,
    actor: str,
    action: str,
    subject_type: str,
    subject_id: str,
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    """Append an event. Flushes so the next append in this session sees the new tip."""
    if session.get_bind().dialect.name == "postgresql":
        # Held until this transaction ends: other writers wait, then see this event as the tip
        session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": APPEND_LOCK})
    tip = session.scalars(select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(1)).first()
    prev_hash = tip.hash if tip else GENESIS
    at = utcnow()
    data = json.loads(json.dumps(payload or {}, default=str))
    event = AuditEvent(
        at=at,
        actor=actor,
        action=action,
        subject_type=subject_type,
        subject_id=subject_id,
        payload=data,
        prev_hash=prev_hash,
        hash=compute_hash(
            prev_hash=prev_hash,
            at=at,
            actor=actor,
            action=action,
            subject_type=subject_type,
            subject_id=subject_id,
            payload=data,
        ),
    )
    session.add(event)
    session.flush()
    return event


@dataclass(frozen=True)
class ChainVerification:
    ok: bool
    events: int
    first_bad_seq: int | None = None
    reason: str | None = None


def verify(session: Session) -> ChainVerification:
    prev_hash = GENESIS
    count = 0
    for event in session.scalars(select(AuditEvent).order_by(AuditEvent.seq)):
        count += 1
        if event.prev_hash != prev_hash:
            return ChainVerification(False, count, event.seq, "Link to previous event is broken")
        expected = compute_hash(
            prev_hash=event.prev_hash,
            at=event.at,
            actor=event.actor,
            action=event.action,
            subject_type=event.subject_type,
            subject_id=event.subject_id,
            payload=event.payload,
        )
        if expected != event.hash:
            return ChainVerification(False, count, event.seq, "Event content was changed")
        prev_hash = event.hash
    return ChainVerification(True, count)
