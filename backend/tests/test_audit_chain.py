import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lane.audit import chain
from lane.db.models import AuditEvent


def _append(session: Session, n: int) -> None:
    for i in range(n):
        chain.record(
            session,
            actor="test",
            action="thing.done",
            subject_type="t",
            subject_id=str(i),
            payload={"i": i, "note": "café"},
        )
    session.commit()


def test_empty_chain_verifies(db: Session) -> None:
    assert chain.verify(db) == chain.ChainVerification(ok=True, events=0)


def test_chain_links_each_event_to_the_previous(db: Session) -> None:
    _append(db, 3)
    events = db.query(AuditEvent).order_by(AuditEvent.seq).all()
    assert events[0].prev_hash == chain.GENESIS
    assert [e.prev_hash for e in events[1:]] == [e.hash for e in events[:-1]]
    assert chain.verify(db).ok


def test_editing_a_past_event_is_detected(db: Session) -> None:
    _append(db, 3)
    db.execute(update(AuditEvent).where(AuditEvent.seq == 2).values(payload={"i": 99}))
    db.commit()
    result = chain.verify(db)
    assert not result.ok
    assert result.first_bad_seq == 2
    assert result.reason == "Event content was changed"


def test_deleting_a_past_event_is_detected(db: Session) -> None:
    _append(db, 3)
    db.query(AuditEvent).filter(AuditEvent.seq == 2).delete()
    db.commit()
    result = chain.verify(db)
    assert not result.ok
    assert result.first_bad_seq == 3


def test_a_forked_chain_cannot_be_written(db: Session) -> None:
    _append(db, 1)
    tip = db.query(AuditEvent).one()
    db.add(
        AuditEvent(
            actor="x",
            action="fork",
            subject_type="t",
            subject_id="f",
            payload={},
            prev_hash=tip.prev_hash,
            hash="f" * 64,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
