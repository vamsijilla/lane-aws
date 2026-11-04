from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from lane.intake import cli
from tests.factories import PDF, make_email


def test_cli_ingests_reports_and_is_idempotent(
    db: Session, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    eml = tmp_path / "sub.eml"
    eml.write_bytes(make_email([("a.pdf", PDF), ("bad.zip", b"PK\x03\x04 nope")]))

    assert cli.main([str(eml)]) == 0
    out = capsys.readouterr().out
    assert "new submission" in out
    assert "1 rejected" in out
    assert "rejected: bad.zip: Archive is corrupt" in out

    assert cli.main([str(eml)]) == 0
    assert "already received" in capsys.readouterr().out


def test_cli_reports_unreadable_input(db: Session, tmp_path: Path) -> None:
    not_email = tmp_path / "x.eml"
    not_email.write_bytes(PDF)
    assert cli.main([str(not_email), str(tmp_path / "missing.eml")]) == 1
