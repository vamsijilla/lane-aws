"""The sample submission rebuilt in a fresh database from tests/fixtures/sample_submission.json,
then run through resolve, rules and actions (the email is the template draft; no model).

`render(db)` returns the text people read: rule results, conflicts, missing items and the
agent email. Snapshot tests compare it with tests/snapshots/, so any change to what the
underwriter sees is deliberate.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import DateTime, Table, update
from sqlalchemy.orm import Session

from lane.actions.service import build_actions
from lane.config import get_settings
from lane.db.base import Base
from lane.db.models import (
    Document,
    Submission,
)
from lane.evals.render import render as render
from lane.llm.gateway import LlmUnavailable
from lane.resolve.service import resolve_submission
from lane.rules.service import decide_submission
from tests.sample import FIXTURE, raw

# The v1 pipeline's full database, frozen at the v1-baseline tag (gitignored; see
# evals/baseline/README.md). The fixture slice in tests/fixtures/ is what the tests load.
BASELINE_DB = FIXTURE.parents[3] / "evals" / "baseline" / "v1.db"

ORDER = (
    "blobs",
    "email_messages",
    "submissions",
    "attachments",
    "documents",
    "pages",
    "document_extractions",
    "facts",
)


def _typed(table: Table, row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col in table.columns:
        if col.name not in row:
            continue
        v = row[col.name]
        if isinstance(col.type, DateTime) and isinstance(v, str):
            v = datetime.fromisoformat(v)
        out[col.name] = v
    return out


def load_slice(db: Session) -> str:
    """Insert the sample's rows; returns the submission id."""
    data = raw()["slice"]
    for name in ORDER:
        table = Base.metadata.tables[name]
        rows = [_typed(table, r) for r in data[name]]
        if rows:
            db.execute(table.insert(), rows)
    for r in data["documents_superseded"]:
        db.execute(
            update(Document)
            .where(Document.id == r["id"])
            .values(superseded_by_id=r["superseded_by_id"])
        )
    db.commit()
    return str(data["submissions"][0]["id"])


def _no_model(_settings: Any) -> Any:
    raise LlmUnavailable("snapshot tests draft from the template")


def process(db: Session, sid: str) -> None:
    sub = db.get(Submission, sid)
    assert sub is not None
    resolve_submission(db, sub)
    decide_submission(db, sub, run_id=None)
    build_actions(db, sub, get_settings(), _no_model, run_id=None)


def today() -> date:
    return date.fromisoformat(raw()["received"])
