"""Operations' live view: workers by heartbeat, jobs and their leases, a takeover, services."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from lane.api.main import create_app
from lane.db.base import utcnow
from lane.db.models import JobStatus, WorkerHeartbeat
from lane.pipeline import queue
from lane.storage.blobs import BlobStore
from tests.factories import PDF, make_email
from tests.test_intake import ingest


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def ops_get(client: TestClient, path: str, role: str = "ops"):
    token = client.post("/api/v1/auth/demo-login", json={"role": role}).json()["token"]
    return client.get(path, headers={"Authorization": f"Bearer {token}"})


def test_the_queue_shows_workers_jobs_and_a_takeover(
    client: TestClient, db: Session, store: BlobStore
) -> None:
    sid = ingest(db, store, make_email([("a.pdf", PDF)])).submission_id
    queue.enqueue(db, sid, kind="process", trigger="intake")
    held = queue.claim(db, "host:1", lease_s=30, max_attempts=3)
    assert held is not None
    held.job.lease_until = utcnow() - timedelta(seconds=1)  # host:1 stopped renewing
    db.commit()
    taken = queue.claim(db, "host:2", lease_s=30, max_attempts=3)
    assert taken is not None
    now = utcnow()
    db.add_all(
        [
            WorkerHeartbeat(owner="host:2", seen_at=now, job_id=taken.job.id, jobs_done=3),
            WorkerHeartbeat(owner="host:1", seen_at=now - timedelta(seconds=40)),
            WorkerHeartbeat(owner="host:0", seen_at=now - timedelta(hours=2)),  # long gone
        ]
    )
    db.commit()

    body = ops_get(client, "/api/v1/ops/queue").json()

    assert {w["owner"]: w["state"] for w in body["workers"]} == {
        "host:1": "quiet",
        "host:2": "working",
    }
    assert body["jobs_by_status"] == {JobStatus.RUNNING.value: 1}
    job = body["jobs"][0]
    assert job["lease_owner"] == "host:2" and job["taken_over"] and job["attempts"] == 2
    assert 0 < job["lease_seconds_left"] <= 30
    assert [s["name"] for s in body["services"]] == ["Database", "File storage", "Email intake"]
    assert all(s["ok"] for s in body["services"][:2])


def test_the_queue_is_for_operations_roles(client: TestClient) -> None:
    assert ops_get(client, "/api/v1/ops/queue", role="viewer").status_code == 403
