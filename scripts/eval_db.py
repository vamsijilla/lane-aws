"""Rebuild the eval database from the main one, so evals never add submissions to the inbox.

    cd backend && uv run python ../scripts/eval_db.py postgresql+psycopg://localhost:5433/lane_eval

Drops the target database if it exists, creates it, migrates it, and copies every row of the
main database (LANE_DATABASE_URL) into it: the sample, its runs and the whole model cache, so
perturbations processed there answer from the cache. Postgres only.
"""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url

from lane.config import get_settings
from lane.db.base import engine_for
from lane.db.scratch import copy_rows

BACKEND = Path(__file__).resolve().parents[1] / "backend"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    target = make_url(argv[0])
    source_url = get_settings().resolved_database_url
    if not source_url.startswith("postgresql") or target.get_backend_name() != "postgresql":
        print("Both the main database and the eval database must be Postgres.")
        return 2
    if target.database == make_url(source_url).database:
        print("The eval database must not be the main database.")
        return 2

    admin = engine_for(source_url)
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{target.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    url = target.render_as_string(hide_password=False)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env={**os.environ, "LANE_DATABASE_URL": url},
        check=True,
        capture_output=True,
    )
    dst = engine_for(url)
    counts = copy_rows(admin, dst)
    admin.dispose()
    dst.dispose()
    print(
        f"{target.database}: {counts['submissions']} submissions, {counts['llm_cache']} cached answers"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
