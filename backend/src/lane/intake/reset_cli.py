"""Delete every submission: `uv run python -m lane.intake.reset_cli`.

`make reset` runs this, then ingests and processes the sample package, so the demo has one
submission. The audit log and the model cache are kept.
"""

from sqlalchemy import select

from lane.db.base import get_sessionmaker
from lane.db.models import Submission
from lane.intake.remove import delete_submission


def main() -> int:
    with get_sessionmaker()() as session:
        ids = list(session.scalars(select(Submission.id)))
        for sid in ids:
            delete_submission(session, sid, actor="cli:reset")
    print(f"Deleted {len(ids)} submissions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
