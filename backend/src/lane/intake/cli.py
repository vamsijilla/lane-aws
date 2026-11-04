"""Ingest .eml files from the command line: `uv run python -m lane.intake.cli path.eml ...`."""

import argparse
import sys
from pathlib import Path

from lane.config import get_settings
from lane.db.base import get_sessionmaker
from lane.intake.eml import EmailParseError
from lane.intake.service import IntakeLimits, IntakeRejected, ingest_email
from lane.storage.blobs import blob_store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest agent emails into Lane")
    parser.add_argument("paths", nargs="+", type=Path, help=".eml files")
    args = parser.parse_args(argv)

    settings = get_settings()
    store = blob_store(settings)
    limits = IntakeLimits.from_settings(settings)
    failures = 0
    for path in args.paths:
        with get_sessionmaker()() as session:
            try:
                r = ingest_email(session, store, path.read_bytes(), source="cli", limits=limits)
            except (OSError, IntakeRejected, EmailParseError) as exc:
                print(f"{path.name}: rejected. {exc}", file=sys.stderr)
                failures += 1
                continue
        state = "new submission" if r.created else "already received, existing submission kept"
        print(f"{path.name}: {state} {r.submission_id}")
        if r.created:
            print(
                f"  {r.files} files: {r.unique_files} unique, {r.duplicates} duplicates, "
                f"{r.expanded_archives} archives opened, {r.rejected} rejected, "
                f"{r.unsupported} unsupported"
            )
            for note in r.notes:
                print(f"  rejected: {note}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
