"""Compare two pipeline runs of the same submission: what the underwriter reads, and why it
differs.

    uv run python -m lane.evals.compare_runs --a ../evals/baseline/v1.db --b ../var/lane.db

Prints rule outcomes and the route side by side, a diff of each rendered part (rules,
conflicts, missing items, agent email), and every resolved field whose value, state or
candidates changed, with the documents behind each candidate, so each difference can be traced
to the fact that changed.
"""

import argparse
import difflib
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from lane.db.base import sqlite_engine
from lane.db.models import (
    DecisionRecord,
    ResolvedEntity,
    ResolvedField,
    RuleOutcomeRecord,
    Submission,
)
from lane.evals.render import render


def _open(path: str) -> tuple[Any, Session, str]:
    engine = sqlite_engine(f"sqlite:///{path}")
    session = sessionmaker(engine)()
    sid = session.scalars(select(Submission.id)).first()
    assert sid is not None, f"no submission in {path}"
    return engine, session, sid


def _fields(session: Session, sid: str) -> dict[str, dict[str, Any]]:
    ents = {
        e.id: e
        for e in session.scalars(select(ResolvedEntity).where(ResolvedEntity.submission_id == sid))
    }
    out: dict[str, dict[str, Any]] = {}
    for f in session.scalars(select(ResolvedField).where(ResolvedField.submission_id == sid)):
        e = ents[f.entity_id]
        out[f"{e.kind}:{e.key}.{f.attribute}"] = {
            "label": f"{e.label}: {f.label}",
            "state": f.state,
            "value": f.value_display,
            "candidates": sorted(
                (
                    str(c.get("display")),
                    tuple(sorted(str(s.get("title")) for s in c.get("sources", []))),
                )
                for c in f.candidates
            ),
        }
    return out


def compare(a_path: str, b_path: str) -> dict[str, Any]:
    ea, sa, ida = _open(a_path)
    eb, sb, idb = _open(b_path)
    try:
        ra, rb = render(sa, ida), render(sb, idb)
        rules_a = {
            r.rule_id: (r.outcome, r.potential_decline)
            for r in sa.scalars(
                select(RuleOutcomeRecord).where(RuleOutcomeRecord.submission_id == ida)
            )
        }
        rules_b = {
            r.rule_id: (r.outcome, r.potential_decline)
            for r in sb.scalars(
                select(RuleOutcomeRecord).where(RuleOutcomeRecord.submission_id == idb)
            )
        }
        da, db = sa.get(DecisionRecord, ida), sb.get(DecisionRecord, idb)
        fa, fb = _fields(sa, ida), _fields(sb, idb)
    finally:
        sa.close()
        sb.close()
        ea.dispose()
        eb.dispose()
    changed_fields: list[dict[str, Any]] = []
    for key in sorted(set(fa) | set(fb)):
        x, y = fa.get(key), fb.get(key)
        if x == y:
            continue
        changed_fields.append({"field": key, "a": x, "b": y})
    return {
        "rules_changed": {
            k: {"a": rules_a.get(k), "b": rules_b.get(k)}
            for k in sorted(set(rules_a) | set(rules_b))
            if rules_a.get(k) != rules_b.get(k)
        },
        "route": {
            "a": [da.outcome, da.team, da.headline, da.potential_decline] if da else None,
            "b": [db.outcome, db.team, db.headline, db.potential_decline] if db else None,
        },
        "diffs": {
            part: "".join(
                difflib.unified_diff(
                    ra[part].splitlines(keepends=True),
                    rb[part].splitlines(keepends=True),
                    f"v1/{part}",
                    f"v2/{part}",
                    n=1,
                )
            )
            for part in ra
        },
        "fields_changed": changed_fields,
    }


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[4]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", default=str(root / "evals" / "baseline" / "v1.db"))
    parser.add_argument("--b", default=str(root / "var" / "lane.db"))
    parser.add_argument("--json")
    args = parser.parse_args(argv)
    out = compare(args.a, args.b)
    print("Rule outcomes changed:", out["rules_changed"] or "none")
    print("Route:", "same" if out["route"]["a"] == out["route"]["b"] else out["route"])
    for part, diff in out["diffs"].items():
        print(f"\n== {part}: {'identical' if not diff else 'differs'}")
        if diff:
            print(diff)
    print(f"\nResolved fields that changed: {len(out['fields_changed'])}")
    for c in out["fields_changed"]:
        print(f"- {c['field']}\n    v1: {c['a']}\n    v2: {c['b']}")
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
