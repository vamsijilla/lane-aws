"""Copies of the database: a scratch copy for replay and what-if scoring, and the one-time move
of a SQLite database into Postgres.

A scratch copy never touches the real results. On SQLite it is a backup into a temporary file;
on Postgres it is a temporary database filled row by row through the models, so no external
tools (pg_dump) are needed and the copy works while the service is connected.
"""

import sqlite3
import tempfile
import uuid
from collections.abc import Generator
from contextlib import closing, contextmanager
from pathlib import Path

from sqlalchemy import Connection, Engine, Integer, insert, select, text

from lane.db import models  # noqa: F401  # pyright: ignore[reportUnusedImport]  (registers every table)
from lane.db.base import Base, engine_for, sqlite_engine

BATCH = 500


def copy_rows(src: Engine, dst: Engine, *, tables: list[str] | None = None) -> dict[str, int]:
    """Copy every row of every model table from src into dst (whose tables exist and are empty),
    parents before children. Returns rows copied per table."""
    counts: dict[str, int] = {}
    with src.connect() as read, dst.begin() as write:
        if dst.dialect.name == "postgresql":
            # Rows of a self-referencing table (an attachment inside an archive, a superseded
            # document) may arrive before the row they point to; the copy is checked as a whole.
            write.execute(text("SET session_replication_role = replica"))
        for table in Base.metadata.sorted_tables:
            if tables is not None and table.name not in tables:
                continue
            n = 0
            result = read.execution_options(stream_results=True).execute(select(table))
            for chunk in result.mappings().partitions(BATCH):
                write.execute(insert(table), [dict(r) for r in chunk])
                n += len(chunk)
            counts[table.name] = n
        if dst.dialect.name == "postgresql":
            write.execute(text("SET session_replication_role = DEFAULT"))
            reset_sequences(write)
    return counts


def reset_sequences(conn: Connection) -> None:
    """After rows are copied with their ids, serial columns continue from the highest id."""
    for table in Base.metadata.sorted_tables:
        for col in table.primary_key.columns:
            if isinstance(col.type, Integer) and col.autoincrement in (True, "auto"):
                # Table and column names come from the models, never from input
                sql = (
                    f"SELECT setval(pg_get_serial_sequence('{table.name}', '{col.name}'), "  # noqa: S608
                    f"COALESCE((SELECT MAX({col.name}) FROM {table.name}), 0) + 1, false)"
                )
                conn.execute(text(sql))


def alembic_version(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


@contextmanager
def scratch_copy(source: Engine, *, workdir: Path | None = None) -> Generator[Engine]:
    """A throwaway copy of the database behind `source`, removed on exit."""
    url = source.url
    if url.get_backend_name() == "sqlite":
        if not url.database:
            raise RuntimeError("An in-memory database cannot be copied")
        with tempfile.TemporaryDirectory(dir=workdir) as tmp:
            target = Path(tmp) / "scratch.db"
            # closing(): sqlite3's own context manager commits but never closes the connection
            with (
                closing(sqlite3.connect(url.database)) as src,
                closing(sqlite3.connect(target)) as dst,
            ):
                src.backup(dst)
            engine = sqlite_engine(f"sqlite:///{target}")
            try:
                yield engine
            finally:
                engine.dispose()
        return

    name = f"{url.database}_scratch_{uuid.uuid4().hex[:8]}"
    admin = engine_for(url.render_as_string(hide_password=False))
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    engine = engine_for(url.set(database=name).render_as_string(hide_password=False))
    try:
        Base.metadata.create_all(engine)
        version = alembic_version(source)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
            if version:
                conn.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": version})
        copy_rows(source, engine)
        yield engine
    finally:
        engine.dispose()
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
