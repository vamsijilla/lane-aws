import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from lane.config import Settings, get_settings
from lane.db.base import engine_for, get_engine, get_sessionmaker
from lane.storage.blobs import BlobStore

BACKEND = Path(__file__).resolve().parents[1]
# Tests never read a developer's .env (queue mode, a testing model profile, a database URL):
# every setting a test depends on is its default or set by the test itself.
Settings.model_config["env_file"] = None
# `make test-pg`: run every test on Postgres instead of SQLite, each on its own database
# cloned from one migrated template.
ALL_ON_PG = os.environ.get("LANE_TEST_ALL_ON_PG", "")


def _pg_admin(sql: str) -> None:
    admin = engine_for(ALL_ON_PG)
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(sql))
    admin.dispose()


def _pg_url(name: str) -> str:
    return make_url(ALL_ON_PG).set(database=name).render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def pg_template() -> Iterator[str | None]:
    if not ALL_ON_PG:
        yield None
        return
    name = f"lane_tpl_{uuid.uuid4().hex[:8]}"
    _pg_admin(f'CREATE DATABASE "{name}"')
    before = os.environ.get("LANE_DATABASE_URL")
    os.environ["LANE_DATABASE_URL"] = _pg_url(name)
    for cached in (get_settings, get_engine, get_sessionmaker):
        cached.cache_clear()
    command.upgrade(Config(str(BACKEND / "alembic.ini")), "head")
    get_engine().dispose()
    if before is None:
        os.environ.pop("LANE_DATABASE_URL")
    else:
        os.environ["LANE_DATABASE_URL"] = before
    yield name
    _pg_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(autouse=True)
def isolated_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pg_template: str | None
) -> Iterator[None]:
    """Every test gets its own data dir and database."""
    monkeypatch.setenv("LANE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LANE_DATABASE_URL", "")
    pg_db = None
    if pg_template is not None:
        pg_db = f"lane_t_{uuid.uuid4().hex[:10]}"
        _pg_admin(f'CREATE DATABASE "{pg_db}" TEMPLATE "{pg_template}"')
        monkeypatch.setenv("LANE_DATABASE_URL", _pg_url(pg_db))
    # Tests never reach a real model: an empty key overrides any key in .env, so the default
    # gateway is unavailable. Tests that need model output inject a FakeTransport.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    # Real OCR takes seconds per page; tests in test_ocr.py switch it on explicitly.
    monkeypatch.setenv("LANE_TRIAGE_OCR", "false")
    # The shipped default for requests without a sign-in is the read-only viewer
    # (test_privacy_auth checks that); most API tests act as the demo underwriter.
    monkeypatch.setenv("LANE_DEMO_DEFAULT_ROLE", "underwriter")
    for cached in (get_settings, get_engine, get_sessionmaker):
        cached.cache_clear()
    yield
    get_engine().dispose()
    for cached in (get_settings, get_engine, get_sessionmaker):
        cached.cache_clear()
    if pg_db is not None:
        _pg_admin(f'DROP DATABASE IF EXISTS "{pg_db}" WITH (FORCE)')


@pytest.fixture
def db() -> Iterator[Session]:
    """A migrated database (via Alembic, same as production) and a session on it."""
    command.upgrade(Config(str(BACKEND / "alembic.ini")), "head")
    with get_sessionmaker()() as session:
        yield session


@pytest.fixture
def store() -> BlobStore:
    return BlobStore(get_settings().blob_dir)
