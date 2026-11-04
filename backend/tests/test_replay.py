"""Replay reruns every step from the stored files with model answers only from the cache, and
proves the output is identical, without changing what is stored or calling a model."""

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from lane.config import get_settings
from lane.db.models import AuditEvent, LlmCacheEntry, LlmCall, RuleOutcomeRecord, Run
from lane.llm.gateway import LlmRequest
from lane.pipeline import fingerprint
from lane.pipeline.registry import PIPELINE
from lane.pipeline.runner import process_submission
from lane.review import replay
from lane.storage.blobs import BlobStore
from tests.docs_factory import text_pdf
from tests.factories import make_email
from tests.llm_fakes import FakeTransport, factory, verdict
from tests.test_intake import ingest

# Steps whose model calls the fake can answer validly: classification
STEPS = [s for s in PIPELINE if s.name in {"triage", "classify", "resolve", "rules"}]


def scripted(request: LlmRequest[Any], text: str) -> dict[str, Any]:
    return verdict("loss_run", subject_name="Acme Hauling", period="2025-2026")


def processed(db: Session, store: BlobStore) -> tuple[str, FakeTransport]:
    sid = ingest(db, store, make_email([("losses.pdf", text_pdf(pages=2))])).submission_id
    transport = FakeTransport(scripted)
    process_submission(
        db, store, get_settings(), sid, steps=STEPS, trigger="test", llm_factory=factory(transport)
    )
    return sid, transport


def test_runs_record_their_fingerprints(db: Session, store: BlobStore) -> None:
    sid, _ = processed(db, store)
    run = db.scalars(select(Run).where(Run.submission_id == sid)).one()
    assert run.input_hash and run.output_hash == fingerprint.output_hash(db, sid)
    assert set(run.versions["steps"]) == {s.name for s in STEPS}
    assert run.versions["prompts"] and "sections" in run.versions


def test_the_output_hash_ignores_ids_and_times_but_not_outcomes(
    db: Session, store: BlobStore
) -> None:
    sid, transport = processed(db, store)
    first = fingerprint.output_hash(db, sid)
    process_submission(  # a second run rebuilds every row with new ids and timestamps
        db, store, get_settings(), sid, steps=STEPS, trigger="test", llm_factory=factory(transport)
    )
    assert fingerprint.output_hash(db, sid) == first
    rule = db.scalars(select(RuleOutcomeRecord)).first()
    assert rule is not None
    rule.reason += " (edited)"
    assert fingerprint.output_hash(db, sid) != first


def test_a_replay_from_the_cache_is_identical_and_changes_nothing(
    db: Session, store: BlobStore
) -> None:
    sid, transport = processed(db, store)
    stored = fingerprint.output_hash(db, sid)
    sent = len(transport.calls)
    run = replay.start(db, sid)
    v = replay.replay(
        db, store, get_settings(), run, gateway_factory=factory(transport), steps=STEPS
    )
    assert v.identical and v.not_cached == 0 and v.after_hash == stored and not v.differences
    assert len(transport.calls) == sent  # every answer came from the cache
    assert fingerprint.output_hash(db, sid) == stored  # the real results are untouched
    db.refresh(run)
    assert run.trigger == "replay" and run.output_hash == stored
    assert [s.name for s in run.steps] == [s.name for s in STEPS]
    assert run.versions["replay"]["identical"] is True
    calls = db.scalars(select(LlmCall).where(LlmCall.run_id == run.id)).all()
    assert calls and all(c.cache_hit for c in calls)  # the replay's calls, carried over
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "run.replayed")).one()
    assert event.payload["identical"] is True


def test_a_replay_never_calls_a_model_for_a_missing_answer(db: Session, store: BlobStore) -> None:
    sid, transport = processed(db, store)
    stored = fingerprint.output_hash(db, sid)
    db.execute(delete(LlmCacheEntry))
    db.commit()
    sent = len(transport.calls)
    run = replay.start(db, sid)
    v = replay.replay(
        db, store, get_settings(), run, gateway_factory=factory(transport), steps=STEPS
    )
    assert not v.identical and v.not_cached > 0
    assert len(transport.calls) == sent  # nothing was sent
    assert fingerprint.output_hash(db, sid) == stored  # and nothing stored changed


def test_version_changes_are_named() -> None:
    assert replay._changes(  # pyright: ignore[reportPrivateUsage]
        "Step", {"triage": "2", "rules": "1"}, {"triage": "3", "rules": "1"}
    ) == ["Step triage: 2 to 3"]
