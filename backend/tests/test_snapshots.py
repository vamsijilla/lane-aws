"""What the underwriter reads, frozen: rule results, conflicts, missing items and the agent
email for the sample submission. A change here must be deliberate: refresh the snapshot with
LANE_UPDATE_SNAPSHOTS=1 and say why in the commit.

The submission is rebuilt from tests/fixtures/sample_submission.json (export it again with
`uv run python -m tests.fixtures.export_sample` after a pipeline change)."""

import os
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from tests.slice import load_slice, process, render

SNAPSHOTS = Path(__file__).with_name("snapshots")


@pytest.fixture
def rendered(db: Session) -> dict[str, str]:
    sid = load_slice(db)
    process(db, sid)
    return render(db, sid)


@pytest.mark.parametrize("part", ["rules", "conflicts", "missing", "email"])
def test_what_the_underwriter_reads_is_unchanged(rendered: dict[str, str], part: str) -> None:
    path = SNAPSHOTS / f"{part}.txt"
    if os.environ.get("LANE_UPDATE_SNAPSHOTS") or not path.exists():
        path.write_text(rendered[part])
    assert rendered[part] == path.read_text(), f"{part} changed; see {path}"
