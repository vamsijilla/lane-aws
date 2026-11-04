"""Move a SQLite database into Postgres, once.

    cd backend && uv run python ../scripts/sqlite_to_postgres.py ../var/lane.db \
        --to postgresql+psycopg://localhost:5433/lane [--merge-cache other.db ...]

The target must already be migrated (alembic upgrade head) and empty. Every row is copied with
its id, so the audit chain, runs and model cache carry over unchanged. --merge-cache adds the
model answers of another SQLite database that are not in the target yet (for example the copy
the perturbations ran on), so processing them again costs nothing. Their calls are kept as the
cache's source, without the runs they belonged to.
"""

import argparse
import sys

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from lane.audit import chain
from lane.db.base import engine_for, sqlite_engine
from lane.db.models import LlmCacheEntry, LlmCall
from lane.db.scratch import alembic_version, copy_rows


def merge_cache(src_path: str, dst_url: str) -> int:
    src, dst = sqlite_engine(f"sqlite:///{src_path}"), engine_for(dst_url)
    calls, cache = LlmCall.__table__, LlmCacheEntry.__table__
    with src.connect() as read, dst.begin() as write:
        have = set(write.scalars(select(cache.c.cache_key)))
        have_calls = set(write.scalars(select(calls.c.id)))
        added = 0
        for entry in read.execute(select(cache)).mappings():
            if entry["cache_key"] in have:
                continue
            if entry["source_call_id"] not in have_calls:
                call = read.execute(select(calls).where(calls.c.id == entry["source_call_id"]))
                row = dict(call.mappings().one())
                row["run_id"] = None  # the run lives only in the other database
                write.execute(insert(calls), [row])
                have_calls.add(row["id"])
            write.execute(insert(cache), [dict(entry)])
            have.add(entry["cache_key"])
            added += 1
    src.dispose()
    dst.dispose()
    return added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="the SQLite database file")
    parser.add_argument("--to", required=True, help="the Postgres URL (migrated, empty)")
    parser.add_argument("--merge-cache", nargs="*", default=[], help="more SQLite files")
    args = parser.parse_args(argv)

    src, dst = sqlite_engine(f"sqlite:///{args.source}"), engine_for(args.to)
    if alembic_version(src) != alembic_version(dst):
        print("Migrate both databases to the same version first (alembic upgrade head).")
        return 1
    counts = copy_rows(src, dst)
    print(f"Copied {sum(counts.values())} rows in {len(counts)} tables")
    for name, n in counts.items():
        if n:
            print(f"  {name}: {n}")
    for extra in args.merge_cache:
        print(f"Merged {merge_cache(extra, args.to)} cached model answers from {extra}")

    with Session(dst) as session:
        report = chain.verify(session)
    state = "intact" if report.ok else f"broken at event {report.first_bad_seq}: {report.reason}"
    print(f"Audit chain: {report.events} events, {state}")
    src.dispose()
    dst.dispose()
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
