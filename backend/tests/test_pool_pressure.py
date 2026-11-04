"""A burst of page requests never locks up the API, and personal data stays masked under load.

Every submission response passes the privacy middleware, which looks up the identifiers to mask.
Run on the event loop with its own connection, that lookup could wait for a connection that only
the waiting requests could free: with more requests in flight than connections, the API stopped
answering until it was restarted. Here the pool is cut to two connections, so a dozen concurrent
requests are enough to show it.
"""

import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import lane.api.main as api_main
import lane.db.base as base
from lane.api.main import create_app
from lane.storage.blobs import BlobStore
from tests.seed import seed_submission

RAW_LICENSE = "D1234567"  # the seeded driver's license number


@pytest.fixture
def small_pool(db: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    url = base.get_engine().url
    engine = create_engine(
        url,
        pool_size=2,
        max_overflow=0,
        pool_timeout=3,
        connect_args={"check_same_thread": False} if url.drivername.startswith("sqlite") else {},
    )
    monkeypatch.setattr(base, "get_engine", lambda: engine)
    base.get_sessionmaker.cache_clear()
    yield engine
    engine.dispose()
    base.get_sessionmaker.cache_clear()


@pytest.fixture
def sid(db: Session, store: BlobStore) -> str:
    return seed_submission(db, store)


def _burst(client: TestClient, sid: str, n: int) -> tuple[list[Response], float]:
    paths = [f"/api/v1/submissions/{sid}{p}" for p in ("", "/rules", "/entities", "/conflicts")]
    started = time.monotonic()

    def get(i: int) -> Response:
        return client.get(paths[i % len(paths)])

    with ThreadPoolExecutor(n) as pool:
        responses = list(pool.map(get, range(n)))
    return responses, time.monotonic() - started


def test_a_burst_of_requests_finishes_with_more_requests_than_connections(
    small_pool: Engine, sid: str
) -> None:
    with TestClient(create_app()) as client:
        responses, took = _burst(client, sid, 12)
    assert [r.status_code for r in responses] == [200] * 12
    assert took < 10, f"12 requests on 2 connections took {took:.1f}s"
    assert not [r.url for r in responses if RAW_LICENSE in r.text]


@pytest.mark.xfail(
    reason="Known deadlock (docs/DECISIONS.md): past FastAPI's 40 worker threads, a sync "
    "endpoint's session closes only after its response is validated on another thread, so every "
    "thread can wait for a connection. Mitigated by pool size and the UI's request limit.",
    strict=True,
    raises=SQLAlchemyError,
)
def test_a_burst_larger_than_the_thread_pool_finishes(small_pool: Engine, sid: str) -> None:
    with TestClient(create_app()) as client:
        responses, _took = _burst(client, sid, 60)
    assert [r.status_code for r in responses] == [200] * 60


def test_a_response_is_refused_rather_than_sent_unmasked(
    db: Session, sid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(*_args: object) -> dict[str, str]:
        raise SQLAlchemyError("no connection")

    monkeypatch.setattr(api_main, "pii_texts", unavailable)
    with TestClient(create_app()) as client:
        r = client.get(f"/api/v1/submissions/{sid}/entities")
    assert r.status_code == 503
    assert RAW_LICENSE not in r.text
