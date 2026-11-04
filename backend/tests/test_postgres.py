"""Postgres behaviour: migrations, the move from SQLite, scratch copies.

Skipped unless LANE_TEST_PG_URL names a Postgres server we may create databases on
(`make check-pg` sets it). Each test gets its own temporary database, dropped afterwards.
"""

import os
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from lane.audit import chain
from lane.config import get_settings
from lane.db.base import Base, engine_for, get_engine, get_sessionmaker, sqlite_engine
from lane.db.models import AuditEvent
from lane.db.scratch import alembic_version, copy_rows, scratch_copy
from lane.events import notify
from lane.events.bus import BUS
from lane.pipeline import queue
from lane.storage.blobs import BlobStore
from tests.factories import PDF, make_email
from tests.test_intake import ingest

BACKEND = Path(__file__).resolve().parents[1]
PG_URL = os.environ.get("LANE_TEST_PG_URL", "")

pytestmark = pytest.mark.skipif(not PG_URL, reason="set LANE_TEST_PG_URL (make check-pg)")


def _admin(sql: str) -> None:
    admin = engine_for(PG_URL)
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(sql))
    admin.dispose()


@pytest.fixture
def pg(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """A fresh, migrated Postgres database that the app's settings point at."""
    name = f"lane_t_{uuid.uuid4().hex[:10]}"
    _admin(f'CREATE DATABASE "{name}"')
    url = make_url(PG_URL).set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("LANE_DATABASE_URL", url)
    for cached in (get_settings, get_engine, get_sessionmaker):
        cached.cache_clear()
    command.upgrade(Config(str(BACKEND / "alembic.ini")), "head")
    try:
        yield get_engine()
    finally:
        get_engine().dispose()
        for cached in (get_settings, get_engine, get_sessionmaker):
            cached.cache_clear()
        _admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _record(session: Session, n: int) -> None:
    for i in range(n):
        chain.record(
            session,
            actor="test",
            action="test.event",
            subject_type="test",
            subject_id=str(i),
            payload={"i": i},
        )
    session.commit()


def test_migrations_reach_head_on_postgres(pg: Engine) -> None:
    head = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini"))).get_current_head()
    assert alembic_version(pg) == head


def test_a_sqlite_database_moves_with_its_audit_chain_intact(pg: Engine, tmp_path: Path) -> None:
    src = sqlite_engine(f"sqlite:///{tmp_path / 'old.db'}")
    Base.metadata.create_all(src)
    with Session(src) as session:
        _record(session, 3)

    counts = copy_rows(src, pg)
    src.dispose()

    assert counts["audit_events"] == 3
    with Session(pg) as session:
        assert chain.verify(session).ok
        _record(session, 1)  # the serial column continues after the copied ids
        report = chain.verify(session)
    assert report.ok and report.events == 4


def test_a_scratch_copy_is_separate_and_removed_afterwards(pg: Engine) -> None:
    with Session(pg) as session:
        _record(session, 2)

    with scratch_copy(pg) as copy:
        name = copy.url.database
        with Session(copy) as scratch:
            assert chain.verify(scratch).events == 2
            _record(scratch, 1)
            assert scratch.scalar(select(func.count()).select_from(AuditEvent)) == 3

    with Session(pg) as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 2
        gone = session.scalar(
            text("SELECT count(*) FROM pg_database WHERE datname = :n"), {"n": name}
        )
    assert gone == 0


def test_two_workers_claiming_at_once_get_different_jobs(pg: Engine, tmp_path: Path) -> None:
    store = BlobStore(tmp_path / "blobs")
    with Session(pg) as session:
        for n in range(2):
            raw = make_email([("a.pdf", PDF)], message_id=f"<m{n}@agency.example>")
            sid = ingest(session, store, raw).submission_id
            queue.enqueue(session, sid, kind="process", trigger="intake")

    with Session(pg) as a, Session(pg) as b:
        first = queue.claim(a, "w1", lease_s=30, max_attempts=3)
        # w1's transaction is committed; a second, overlapping claim must skip its row
        second = queue.claim(b, "w2", lease_s=30, max_attempts=3)
        assert first is not None and second is not None
        assert first.job.id != second.job.id
        assert queue.claim(b, "w3", lease_s=30, max_attempts=3) is None


def test_a_workers_events_reach_the_api_over_notify(
    pg: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANE_RUN_MODE", "queue")
    get_settings.cache_clear()
    settings = get_settings()
    assert notify.available(settings)
    key = f"sub-{uuid.uuid4().hex[:6]}"
    stop = notify.start_listening(settings)
    try:
        time.sleep(0.5)  # the listener connects
        sender = notify.Publisher(settings)
        event = BUS.publish(f"{key}-worker", "step.started", {"step": "triage"}, forward=False)
        sender.send(key, event)
        sender.close()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not BUS.since(key, 0)[0]:
            time.sleep(0.05)
        events, _ = BUS.since(key, 0)
        assert [(e.type, e.data) for e in events] == [("step.started", {"step": "triage"})]
    finally:
        stop.set()
        time.sleep(1.2)  # the listener notices and closes its connection


def test_writers_in_parallel_append_to_one_unbroken_audit_chain(pg: Engine) -> None:
    errors: list[BaseException] = []

    def write(n: int) -> None:
        try:
            with Session(pg) as session:
                for i in range(20):
                    chain.record(
                        session,
                        actor=f"writer{n}",
                        action="test.event",
                        subject_type="test",
                        subject_id=str(i),
                    )
                    session.commit()
        except BaseException as exc:  # collected and asserted below
            errors.append(exc)

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(write, (1, 2)))
    assert errors == []
    with Session(pg) as session:
        report = chain.verify(session)
    assert report.ok and report.events == 40
