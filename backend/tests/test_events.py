"""Live progress: the bus, and the event stream a real pipeline run produces."""

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from lane.api.main import create_app
from lane.config import get_settings
from lane.events.bus import Bus
from lane.pipeline.registry import PIPELINE
from lane.pipeline.runner import process_submission
from lane.storage.blobs import BlobStore
from tests.docs_factory import text_pdf
from tests.factories import make_email
from tests.test_intake import ingest


def test_bus_orders_bounds_and_tracks_runs() -> None:
    bus = Bus(capacity=3)
    assert bus.since("s", 0) == ([], True)
    bus.publish("s", "run.started")
    for i in range(3):
        bus.publish("s", "page.ready", {"i": i})
    events, finished = bus.since("s", 0)
    assert [e.seq for e in events] == [2, 3, 4] and not finished  # oldest dropped
    assert [e.seq for e in bus.since("s", 3)[0]] == [4]
    bus.publish("s", "run.finished")
    assert bus.since("s", 5) == ([], True) and bus.last_seq("s") == 5
    assert bus.since("other", 0) == ([], True)


@pytest.fixture
def client(db: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("LANE_EVENTS_POLL_S", "0.01")
    monkeypatch.setenv("LANE_EVENTS_IDLE_CLOSE_S", "0.05")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c


def read(
    client: TestClient, path: str, **headers: str
) -> list[tuple[str, int | None, dict[str, object]]]:
    events: list[tuple[str, int | None, dict[str, object]]] = []
    text = client.get(path, headers=headers).text
    for block in text.strip().split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if not line.startswith(":")
        )
        if "event" in fields:
            seq = int(fields["id"]) if "id" in fields else None
            events.append((fields["event"], seq, json.loads(fields["data"])))
    return events


def test_a_run_streams_its_progress_in_order(
    client: TestClient, db: Session, store: BlobStore
) -> None:
    sid = ingest(db, store, make_email([("application.pdf", text_pdf(pages=2))])).submission_id
    process_submission(db, store, get_settings(), sid, steps=PIPELINE, trigger="test")

    replay = read(client, f"/api/v1/submissions/{sid}/events", **{"Last-Event-ID": "0"})
    types = [t for t, _, _ in replay]
    assert types[0] == "run.started" and types[-1] == "end"
    assert types[-2] == "run.finished"
    steps = [d["step"] for t, _, d in replay if t == "step.started"]
    assert steps == [s.name for s in PIPELINE]
    assert types.count("page.ready") == 3  # two PDF pages and the email body
    assert "document.found" in types and "decision.made" in types
    assert types.index("page.ready") < types.index("document.found") < types.index("decision.made")
    seqs = [s for _, s, _ in replay if s is not None]
    assert seqs[:-1] == sorted(seqs[:-1])

    # Resuming after an event sends only what came later.
    middle = seqs[len(seqs) // 2]
    resumed = read(client, f"/api/v1/submissions/{sid}/events", **{"Last-Event-ID": str(middle)})
    assert resumed[0][1] == middle + 1

    # A fresh connection starts from a snapshot of what exists now.
    fresh = read(client, f"/api/v1/submissions/{sid}/events")
    kind, _, snap = fresh[0]
    assert kind == "snapshot" and snap["submission_id"] == sid
    assert [a["filename"] for a in snap["attachments"]][-1] == "application.pdf"  # type: ignore[index]
    assert snap["run"] and snap["decision"]
    assert client.get("/api/v1/submissions/nope/events").status_code == 404
