"""Gold for every decision-tier fact, pre-filled from a run and flagged for a person's review.

    uv run python -m lane.evals.decision_gold write [--db ../var/lane.db]
    uv run python -m lane.evals.decision_gold score --db PATH

`write` lists every decision fact of the run (document, path, value, page or cell, the verbatim
quote, whether v2 verified it) with `reviewed: false`, keeping the entries a person already
reviewed. A reviewer confirms each value or corrects it and sets `reviewed: true`. `score`
compares a run's decision facts with the file, reporting reviewed and unreviewed entries apart:
only reviewed entries measure accuracy; unreviewed ones measure agreement with the pre-fill.
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from lane.db.base import sqlite_engine
from lane.db.models import Document, Fact, Submission
from lane.evals.score import norm
from lane.extract import catalog as cat

ROOT = Path(__file__).resolve().parents[4]
GOLD = ROOT / "evals" / "gold" / "sample_packet_decision_facts.yaml"

HEADER = """\
# Decision-tier facts of the sample submission, one entry per value a rule or conflict reads.
# Pre-filled from an extraction v2 run (lane.evals.decision_gold write), NOT yet reviewed.
# To review: check each value against the document; correct `value` if it is wrong; set
# `reviewed: true`. Entries already reviewed are kept when the file is rewritten.
"""


@dataclass
class Score:
    reviewed: int = 0
    reviewed_ok: int = 0
    unreviewed: int = 0
    unreviewed_agree: int = 0
    missing: int = 0  # gold entries the run has no fact for

    def line(self) -> str:
        return (
            f"reviewed {self.reviewed_ok} of {self.reviewed}; unreviewed (agreement with the "
            f"pre-fill) {self.unreviewed_agree} of {self.unreviewed}; no fact for {self.missing}"
        )


def _decision_facts(db: Path) -> list[dict[str, Any]]:
    engine = sqlite_engine(f"sqlite:///{db}")
    out: list[dict[str, Any]] = []
    with sessionmaker(engine)() as session:
        sid = session.scalars(select(Submission.id)).first()
        rows = session.execute(
            select(Fact, Document)
            .join(Document, Document.id == Fact.document_id)
            .where(Fact.submission_id == sid)
            .order_by(Document.ordinal, Fact.path)
        )
        for f, d in rows:
            e = cat.entry_for(d.doc_type, f.path)
            if e is None or e.tier != "decision" or not e.is_fact:
                continue
            out.append(
                {
                    "document": d.title,
                    "path": f.path,
                    "value": f.value_raw,
                    "where": f"cell {f.cell_ref}" if f.cell_ref else f"page {f.page_no}",
                    "quote": f.quote,
                    "verified_by_v2": bool(f.verified),
                    "reviewed": False,
                }
            )
    engine.dispose()
    return out


def load(path: Path = GOLD) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return cast(list[dict[str, Any]], yaml.safe_load(path.read_text())["facts"])


def write(db: Path, path: Path = GOLD) -> int:
    kept = {(g["document"], g["path"]): g for g in load(path) if g.get("reviewed")}
    facts = [kept.get((f["document"], f["path"]), f) for f in _decision_facts(db)]
    body = yaml.safe_dump({"facts": facts}, sort_keys=False, allow_unicode=True, width=120)
    path.write_text(HEADER + body)
    return len(facts)


def score(db: Path, path: Path = GOLD) -> Score:
    """Facts are matched by document and path; values by the eval's normal form."""
    got = {(f["document"], f["path"]): f["value"] for f in _decision_facts(db)}
    s = Score()
    for g in load(path):
        value = got.get((g["document"], g["path"]))
        if value is None:
            s.missing += 1
            continue
        ok = norm(str(value)) == norm(str(g["value"]))
        if g.get("reviewed"):
            s.reviewed += 1
            s.reviewed_ok += int(ok)
        else:
            s.unreviewed += 1
            s.unreviewed_agree += int(ok)
    return s


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("write", "score"))
    parser.add_argument("--db", default=str(ROOT / "var" / "lane.db"))
    args = parser.parse_args(argv)
    if args.action == "write":
        print(f"Wrote {write(Path(args.db))} decision facts to {GOLD}")
    else:
        from lane.evals.verify_offline import migrated_copy

        print(score(migrated_copy(Path(args.db))).line())
    return 0


if __name__ == "__main__":
    sys.exit(main())
