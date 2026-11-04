"""Queue mode: jobs, leases, a worker taking over a dead worker's job, events between processes."""

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from lane import worker
from lane.api.main import create_app
from lane.config import get_settings
from lane.db.base import utcnow
from lane.db.models import Job, JobStatus, Run, RunStatus, Step, StepStatus
from lane.events import notify
from lane.events.bus import Bus
from lane.pipeline import queue
from lane.storage.blobs import BlobStore
from tests.factories import PDF, make_email
from tests.test_intake import ingest

LEASE = 30.0


@pytest.fixture
def sid(db: Session, store: BlobStore) -> str:
    return ingest(db, store, make_email([("a.pdf", PDF)])).submission_id


def test_a_queued_job_is_claimed_once(db: Session, sid: str) -> None:
    job = queue.enqueue(db, sid, kind="process", trigger="intake")

    first = queue.claim(db, "w1", lease_s=LEASE, max_attempts=3)
    assert first is not None and first.job.id == job.id and first.reclaimed_from is None
    assert first.job.status is JobStatus.RUNNING and first.job.attempts == 1
    assert queue.claim(db, "w2", lease_s=LEASE, max_attempts=3) is None


def test_a_job_whose_lease_ran_out_is_taken_over(db: Session, sid: str) -> None:
    queue.enqueue(db, sid, kind="process", trigger="intake")
    held = queue.claim(db, "w1", lease_s=LEASE, max_attempts=3)
    assert held is not None
    held.job.lease_until = utcnow() - timedelta(seconds=1)  # w1 stopped renewing
    db.commit()

    taken = queue.claim(db, "w2", lease_s=LEASE, max_attempts=3)
    assert taken is not None and taken.reclaimed_from == "w1"
    assert taken.job.lease_owner == "w2" and taken.job.attempts == 2
    assert not queue.renew(db, taken.job.id, "w1", lease_s=LEASE)  # w1 cannot take it back
    assert queue.renew(db, taken.job.id, "w2", lease_s=LEASE)


def test_a_job_that_keeps_losing_its_worker_stops(db: Session, sid: str) -> None:
    job = queue.enqueue(db, sid, kind="process", trigger="intake")
    job.status, job.attempts, job.lease_owner = JobStatus.RUNNING, 3, "w1"
    job.lease_until = utcnow() - timedelta(seconds=1)
    db.commit()

    assert queue.claim(db, "w2", lease_s=LEASE, max_attempts=3) is None
    db.refresh(job)
    assert job.status is JobStatus.FAILED and job.error == queue.GAVE_UP.format(n=3)


def test_taking_over_marks_the_cut_off_run_and_runs_again(
    db: Session, sid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cut_off = Run(submission_id=sid, trigger="intake")
    db.add(cut_off)
    db.flush()
    db.add(Step(run_id=cut_off.id, name="extract", version="1"))
    job = queue.enqueue(db, sid, kind="process", trigger="intake")
    job.run_id = cut_off.id
    job.status, job.attempts, job.lease_owner = JobStatus.RUNNING, 1, "w1"
    job.lease_until = utcnow() - timedelta(seconds=1)
    db.commit()
    ran: list[tuple[Any, ...]] = []
    monkeypatch.setattr("lane.pipeline.background.process", lambda *a, **k: ran.append(a))

    claimed = queue.claim(db, "w2", lease_s=LEASE, max_attempts=3)
    assert claimed is not None
    worker.run_job(claimed, "w2", get_settings())

    db.expire_all()
    assert ran == [(sid, "intake")]
    run = db.get(Run, cut_off.id)
    assert run is not None and run.status is RunStatus.FAILED
    assert run.steps[0].status is StepStatus.FAILED and run.steps[0].error == queue.WORKER_STOPPED
    final = db.get(Job, job.id)
    assert final is not None and final.status is JobStatus.DONE


def test_a_failing_job_is_recorded_and_the_worker_goes_on(
    db: Session, sid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue.enqueue(db, sid, kind="process", trigger="intake")

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr("lane.pipeline.background.process", boom)
    claimed = queue.claim(db, "w1", lease_s=LEASE, max_attempts=3)
    assert claimed is not None
    worker.run_job(claimed, "w1", get_settings())

    db.expire_all()
    job = db.scalars(select(Job)).one()
    assert job.status is JobStatus.FAILED and job.error == "RuntimeError: disk full"


@pytest.fixture
def queued_client(db: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("LANE_RUN_MODE", "queue")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c


def test_in_queue_mode_the_api_queues_work_instead_of_running_it(
    queued_client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[Any] = []
    monkeypatch.setattr("lane.pipeline.background.process", lambda *a, **k: ran.append(a))
    raw = make_email([("a.pdf", PDF)])
    r = queued_client.post(
        "/api/v1/intake/eml", content=raw, headers={"Content-Type": "message/rfc822"}
    )
    assert r.status_code == 201

    jobs = list(db.scalars(select(Job)))
    assert [(j.kind, j.trigger, j.status) for j in jobs] == [
        ("process", "intake", JobStatus.QUEUED)
    ]
    assert jobs[0].submission_id == r.json()["submission_id"]
    assert ran == []


def test_events_can_be_forwarded_and_a_large_one_is_cut_down() -> None:
    sent: list[tuple[str, str]] = []
    bus = Bus()
    bus.forward = lambda key, event: sent.append((key, event.type))
    bus.publish("s1", "step.started", {"step": "triage"})
    bus.publish("s1", "copied", {}, forward=False)  # republished from another process
    assert sent == [("s1", "step.started")]

    big = bus.publish("s1", "facts", {"run_id": "r1", "rows": "x" * 20_000}, forward=False)
    encoded = notify.encode("s1", big)
    assert len(encoded.encode()) <= notify.LIMIT
    assert '"truncated":true' in encoded and '"run_id":"r1"' in encoded
