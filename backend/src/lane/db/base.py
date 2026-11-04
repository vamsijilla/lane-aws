"""Database engine, session factory and declarative base.

SQLite by default (tests, a quick local run) and Postgres when LANE_DATABASE_URL names one
(postgresql+psycopg://...). Nothing here is backend specific beyond the connection settings.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import DateTime, Engine, MetaData, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from lane.config import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def sqlite_engine(url: str) -> Engine:
    engine = create_engine(url, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", _sqlite_pragmas)
    return engine


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    url = settings.resolved_database_url
    if url.startswith("sqlite"):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        return sqlite_engine(url)
    return server_engine(url)


def server_engine(url: str) -> Engine:
    """Postgres: sessions in UTC, so timestamps read back the same on every machine."""
    # 20 connections plus 20 overflow per process: room for a burst of page loads (a known
    # deadlock remains under extreme concurrency; docs/DECISIONS.md D115)
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=20,
        max_overflow=20,
        connect_args={"options": "-c timezone=UTC"},
    )


def engine_for(url: str) -> Engine:
    return sqlite_engine(url) if url.startswith("sqlite") else server_engine(url)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, committed by the caller."""
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()
