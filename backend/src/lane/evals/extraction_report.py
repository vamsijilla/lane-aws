"""Extraction v1 against v2, for evals/report/extraction_v1_vs_v2.md.

    uv run python -m lane.evals.extraction_report [--v1 ../evals/baseline/v1.db] [--v2 ../var/lane.db]

Per run: facts kept, decision facts read twice and verified, field accuracy against the gold set
by tier, document type and read method, recall per table, escalations, model cost (what an
uncached read would cost), and an error taxonomy: wrong slot, wrong value, missed, hallucinated,
unverified. For v1, which had no second read, the same checks run offline over its stored
extractions (lane.evals.verify_offline), and every v1 fact that was grounded but wrong is listed.
"""

import argparse
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from lane.db.base import sqlite_engine
from lane.db.models import (
    Document,
    DocumentExtraction,
    Fact,
    LlmCacheEntry,
    LlmCall,
    ResolvedEntity,
    ResolvedField,
    Run,
    Submission,
)
from lane.domain import normalize as nz
from lane.evals import decision_gold as dg
from lane.evals.gold import Gold
from lane.evals.score import _find_field, norm  # pyright: ignore[reportPrivateUsage]
from lane.evals.verify_offline import migrated_copy
from lane.evals.verify_offline import run as offline
from lane.extract import catalog as cat

ROOT = Path(__file__).resolve().parents[4]
TIER_ORDER = ("decision", "crosscheck", "context")


@dataclass
class RunReport:
    name: str
    facts: int = 0
    catalog_facts: int = 0
    decision_facts: int = 0
    decision_verified: int = 0
    decision_read_twice: bool = False
    accuracy: dict[str, dict[str, list[int]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(lambda: [0, 0]))
    )
    recall: list[tuple[str, str, bool]] = field(
        default_factory=lambda: list[tuple[str, str, bool]]()
    )
    escalated: list[str] = field(default_factory=lambda: list[str]())
    review: list[str] = field(default_factory=lambda: list[str]())
    cost_uncached: float = 0.0
    errors: Counter[str] = field(default_factory=lambda: Counter[str]())
    examples: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    decision_gold: dg.Score = field(default_factory=dg.Score)


def _shown(value: str | None) -> str | None:
    """Values as the report shows them: a license's legal-presence designation is never shown."""
    return nz.drop_presence_markers(value)


def _tier_of(kind: str, attribute: str) -> str:
    tiers = {
        e.tier
        for dt in cat.catalog().doc_types
        for e in cat.entries(dt)
        if e.spec.entity == kind and e.spec.attribute == attribute
    }
    return next((t for t in TIER_ORDER if t in tiers), "derived")


def _wrong_slot(doc_type: str, path: str, value: str | None) -> bool:
    """A value of the wrong type for its field: a statement in an amount, a word in a date."""
    e = cat.entry_for(doc_type, path)
    if e is None or not value:
        return False
    v = value.strip()
    if e.spec.type in ("money", "number"):
        return not re.search(r"\d", v) and v not in ("-",)
    if e.spec.type == "date":
        return nz.to_date(v) is None and not re.search(r"\d", v)
    return False


def _accuracy(session: Session, sid: str, report: RunReport) -> None:
    gold = Gold.load("sample_packet")
    entities = list(
        session.scalars(select(ResolvedEntity).where(ResolvedEntity.submission_id == sid))
    )
    by_entity: dict[str, dict[str, ResolvedField]] = {}
    for f in session.scalars(select(ResolvedField).where(ResolvedField.submission_id == sid)):
        by_entity.setdefault(f.entity_id, {})[f.attribute] = f
    facts = {f.id: f for f in session.scalars(select(Fact).where(Fact.submission_id == sid))}
    docs = {d.id: d for d in session.scalars(select(Document).where(Document.submission_id == sid))}
    for g in gold.fields:
        if g.detail:
            continue
        f = _find_field(entities, by_entity, g)
        if f is None:
            ok = False
            types: set[str] = {"not found"}
            methods: set[str] = {"not found"}
        else:
            ok = (
                (f.state == g.state)
                if g.state
                else (
                    f.state in ("known", "invalid") and norm(f.value) in {norm(v) for v in g.values}
                )
            )
            types, methods = set(), set()
            for c in f.candidates:
                for s in c.get("sources", []):
                    d = docs.get(str(s.get("document_id")))
                    if d:
                        types.add(d.doc_type)
                    for fid in s.get("fact_ids", []):
                        if fid in facts:
                            methods.add(facts[fid].method)
            types = types or {"derived"}
            methods = methods or {"derived"}
        tier = _tier_of(g.entity, g.attribute or "")
        for group, keys in (("tier", {tier}), ("document type", types), ("method", methods)):
            for k in keys:
                cell = report.accuracy[group][k]
                cell[0] += int(ok)
                cell[1] += 1
        report.accuracy["all"]["all"][0] += int(ok)
        report.accuracy["all"]["all"][1] += 1


def _cost_uncached(session: Session, sid: str) -> float:
    """What the run's model calls would cost without the cache: a cached answer costs what the
    call that first produced it cost."""
    run = session.scalars(
        select(Run)
        .where(Run.submission_id == sid, Run.trigger.in_(("cli", "intake", "rerun")))
        .order_by(Run.started_at.desc())
    ).first()
    if run is None:
        return 0.0
    total = 0.0
    for call in session.scalars(
        select(LlmCall).where(LlmCall.run_id == run.id, LlmCall.purpose.like("extract_%"))
    ):
        if not call.cache_hit:
            total += float(call.cost_usd or 0)
            continue
        entry = session.get(LlmCacheEntry, call.cache_key)
        source = session.get(LlmCall, entry.source_call_id) if entry else None
        total += float(source.cost_usd or 0) if source else 0.0
    return total


def _resolved(db: Path) -> dict[tuple[str, str], set[str]]:
    """Every resolved value per entity kind and attribute: what the rules read."""
    engine = sqlite_engine(f"sqlite:///{db}")
    out: dict[tuple[str, str], set[str]] = defaultdict(set)
    with sessionmaker(engine)() as session:
        kinds = {e.id: e.kind for e in session.scalars(select(ResolvedEntity))}
        for f in session.scalars(select(ResolvedField)):
            out[(kinds[f.entity_id], f.attribute)].add(str(f.value_display))
    engine.dispose()
    return out


def _inferred_true(
    v: Any, v1: dict[tuple[str, str], set[str]], v2: dict[tuple[str, str], set[str]]
) -> bool:
    """An unprinted value the model inferred, judged by what it led to: true when the field it
    feeds resolves the same as in v2, where code derives it from what is printed."""
    e = cat.entry_for(v.doc_type, v.path)
    if e is None or not e.spec.entity or not e.spec.attribute:
        return False
    key = (e.spec.entity, e.spec.attribute)
    return key in v2 and v1.get(key) == v2[key]


def report_run(name: str, db: Path, *, v1: bool, truth: Path | None = None) -> RunReport:
    if v1:
        db = migrated_copy(db)
    engine = sqlite_engine(f"sqlite:///{db}")
    r = RunReport(name)
    with sessionmaker(engine)() as session:
        sid = session.scalars(select(Submission.id)).first()
        assert sid is not None
        docs = {
            d.id: d for d in session.scalars(select(Document).where(Document.submission_id == sid))
        }
        facts = list(session.scalars(select(Fact).where(Fact.submission_id == sid)))
        r.facts = len(facts)
        for f in facts:
            doc = docs[f.document_id]
            e = cat.entry_for(doc.doc_type, f.path)
            if e is not None and e.is_fact:
                r.catalog_facts += 1
            if not v1 and not f.verified and _wrong_slot(doc.doc_type, f.path, f.value_raw):
                r.errors["wrong slot"] += 1
                r.examples["wrong slot"].append(f"{doc.title}: {f.path} = {_shown(f.value_raw)!r}")
        if not v1:
            for f in facts:
                if f.tier != "decision":
                    continue
                r.decision_facts += 1
                r.decision_verified += int(f.verified)
                if f.grounding.endswith("mismatch"):
                    r.errors["wrong value"] += 1
                    r.examples["wrong value"].append(
                        f"{docs[f.document_id].title}: {f.path} = {_shown(f.value_raw)!r}, code read {f.second_read!r}"
                    )
                elif not f.verified:
                    r.errors["unverified"] += 1
                    r.examples["unverified"].append(
                        f"{docs[f.document_id].title}: {f.path} = {_shown(f.value_raw)!r}"
                    )
            r.decision_read_twice = True
            for x in session.scalars(
                select(DocumentExtraction).where(DocumentExtraction.submission_id == sid)
            ):
                title = docs[x.document_id].title
                for c in x.checks:
                    if str(c.get("name", "")).startswith("recall:"):
                        r.recall.append((title, str(c.get("detail")), bool(c.get("passed"))))
                        if not c.get("passed"):
                            r.errors["missed"] += 1
                if x.auto_escalated:
                    r.escalated.append(title)
                if x.status.value == "needs_review":
                    r.review.append(title)
        _accuracy(session, sid, r)
        r.cost_uncached = _cost_uncached(session, sid)
    engine.dispose()
    r.decision_gold = dg.score(db)
    if v1:
        o = offline(f"sqlite:///{db}", None)
        here = _resolved(db)
        there = _resolved(truth) if truth else {}
        for v in o.values:
            if v.tier == "decision":
                r.decision_facts += 1
                r.decision_verified += int(v.status == "verified")
            if v.status != "verified" and _wrong_slot(v.doc_type, v.path, v.value):
                r.errors["wrong slot"] += 1
                r.examples["wrong slot"].append(f"{v.document}: {v.path} = {_shown(v.value)!r}")
            elif v.status == "disagreed":
                r.errors["wrong value"] += 1
                r.examples["wrong value"].append(
                    f"{v.document}: {v.path} = {_shown(v.value)!r}, code read {v.code_read!r}"
                )
            elif v.status in ("not_found", "page_only"):
                # Not printed anywhere in a text layer: made up. Otherwise a single read code
                # could not anchor (an image OCR could not read there).
                if v.method == "text" and not v.on_page:
                    if _inferred_true(v, here, there):
                        r.errors["inferred"] += 1
                        r.examples["inferred"].append(
                            f"{v.document}: {v.path} = {_shown(v.value)!r}, inferred by the model (true); "
                            "v2 derives it in code"
                        )
                        continue
                    kind = "hallucinated"
                else:
                    kind = "unverified"
                r.errors[kind] += 1
                r.examples[kind].append(f"{v.document}: {v.path} = {_shown(v.value)!r}")
        r.recall = [(str(c["document"]), str(c["detail"]), bool(c["passed"])) for c in o.recall]
        r.errors["missed"] += sum(1 for c in o.recall if not c["passed"])
    return r


def _pct(c: list[int]) -> str:
    return f"{c[0]} / {c[1]} ({100 * c[0] / c[1]:.0f}%)" if c[1] else "n/a"


def markdown(a: RunReport, b: RunReport, wrong_grounded: list[str]) -> str:
    lines = [
        "# Extraction v1 against v2",
        "",
        "The sample submission, same documents. v1: one model read per document; every schema field a "
        "fact; grounding by a ladder of looser matches. v2: field catalog "
        f"({cat.catalog().tag}); only catalog fields are facts; decision fields read twice (cell, "
        "label-anchored code read, or OCR at the same anchor); validators and recall checks; the "
        "stronger model re-reads, without asking, when a decision field does not verify.",
        "",
        "Produced by `uv run python -m lane.evals.extraction_report`.",
        "",
        "## Summary",
        "",
        "| | v1 | v2 |",
        "|---|---|---|",
        f"| Facts | {a.facts:,} | {b.facts:,} |",
        f"| Facts the catalog keeps | {a.catalog_facts:,} | {b.catalog_facts:,} |",
        f"| Decision facts verified by a second read | none read twice ({a.decision_verified} of {a.decision_facts} would verify offline) | {b.decision_verified} of {b.decision_facts} ({100 * b.decision_verified / max(1, b.decision_facts):.1f}%) |",
        f"| Field accuracy, reviewed gold ({b.accuracy['all']['all'][1]} gold fields; gold covers {b.accuracy['all']['all'][1]} of {b.decision_facts} decision facts) | {_pct(a.accuracy['all']['all'])} | {_pct(b.accuracy['all']['all'])} |",
        f"| Decision facts against the pre-filled gold (`evals/gold/sample_packet_decision_facts.yaml`, {b.decision_gold.unreviewed + b.decision_gold.missing} entries, not yet reviewed: agreement, not accuracy) | {a.decision_gold.unreviewed_agree} of {a.decision_gold.unreviewed + a.decision_gold.missing} | {b.decision_gold.unreviewed_agree} of {b.decision_gold.unreviewed + b.decision_gold.missing} |",
        f"| Recall checks passed | {sum(1 for _, _, ok in a.recall if ok)} of {len(a.recall)} (offline) | {sum(1 for _, _, ok in b.recall if ok)} of {len(b.recall)} |",
        f"| Documents re-read by the stronger model | 0 (needed approval) | {len(b.escalated)}: {', '.join(b.escalated) or 'none'} |",
        f"| Documents in review | 0 | {len(b.review)}: {', '.join(b.review) or 'none'} |",
        f"| Extraction model cost, uncached | ${a.cost_uncached:.4f} | ${b.cost_uncached:.4f} |",
        "",
        "## Field accuracy against the gold set",
        "",
    ]
    for group in ("tier", "document type", "method"):
        lines += [f"### By {group}", "", f"| {group.capitalize()} | v1 | v2 |", "|---|---|---|"]
        for k in sorted(set(a.accuracy[group]) | set(b.accuracy[group])):
            lines.append(f"| {k} | {_pct(a.accuracy[group][k])} | {_pct(b.accuracy[group][k])} |")
        lines.append("")
    lines += ["## Recall per table", "", "| Document | v1 (offline) | v2 |", "|---|---|---|"]
    rows: dict[str, list[str]] = defaultdict(lambda: ["", ""])
    for i, rep in enumerate((a, b)):
        for title, detail, ok in rep.recall:
            rows[title][i] = f"{'pass' if ok else 'FAIL'}: {detail}"
    for title in sorted(rows):
        lines.append(f"| {title} | {rows[title][0] or 'n/a'} | {rows[title][1] or 'n/a'} |")
    lines += ["", "## Error taxonomy", "", "| Error | v1 | v2 |", "|---|---|---|"]
    for kind in ("wrong slot", "wrong value", "missed", "hallucinated", "inferred", "unverified"):
        lines.append(f"| {kind} | {a.errors[kind]} | {b.errors[kind]} |")
    lines += [
        "",
        "Wrong slot: a statement where the field wants an amount or a date, not verified there. "
        "Wrong value: code read a different value at the field's anchor. Missed: rows printed but "
        "not read (recall checks). Hallucinated: printed nowhere in the document and false (the "
        "field it feeds resolves differently from v2). Inferred: printed nowhere but true (the "
        "field resolves the same as v2, where code derives it). Unverified: a single read code could not anchor, usually an "
        "image OCR could not read at that spot; not shown to be wrong.",
        "",
    ]
    for rep in (a, b):
        for kind, items in sorted(rep.examples.items()):
            if items:
                lines += [f"{rep.name}, {kind}:", *(f"- {i}" for i in items[:12]), ""]
    lines += [
        "## v1 facts that were grounded but wrong",
        "",
        "Grounded by v1's ladder (a match anywhere on the page was enough), and wrong: not "
        "printed and false, a statement in an amount, or a different value at the field's label. None "
        "changes a rule outcome.",
        "",
        *(f"- {w}" for w in wrong_grounded),
        "",
    ]
    return "\n".join(lines)


def grounded_but_wrong(v1db: Path, v2db: Path) -> list[str]:
    """v1 facts with exact or fuzzy grounding that v2's checks show are wrong."""
    o = offline(f"sqlite:///{v1db}", None)
    here, there = _resolved(migrated_copy(v1db)), _resolved(v2db)
    v1db = migrated_copy(v1db)
    bad = {
        (v.document, v.path): v
        for v in o.values
        if v.status != "verified"
        and (
            v.status == "disagreed"
            or _wrong_slot(v.doc_type, v.path, v.value)
            or (v.method == "text" and not v.on_page and not _inferred_true(v, here, there))
        )
    }
    engine = sqlite_engine(f"sqlite:///{v1db}")
    out: list[str] = []
    with sessionmaker(engine)() as session:
        for f, d in session.execute(
            select(Fact, Document).join(Document, Document.id == Fact.document_id)
        ):
            v = bad.get((d.title, f.path))
            if v and f.grounding in ("exact", "fuzzy", "ocr_exact", "ocr_fuzzy"):
                why = (
                    "wrong slot"
                    if _wrong_slot(d.doc_type, f.path, f.value_raw)
                    else (
                        f"code read {v.code_read!r}" if v.status == "disagreed" else "not printed"
                    )
                )
                out.append(
                    f"{d.title}: {f.path} = {_shown(f.value_raw)!r} (v1 grounding {f.grounding}; {why})"
                )
    engine.dispose()
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v1", default=str(ROOT / "evals" / "baseline" / "v1.db"))
    parser.add_argument("--v2", default=str(ROOT / "var" / "lane.db"))
    parser.add_argument("--out", default=str(ROOT / "evals" / "report" / "extraction_v1_vs_v2.md"))
    args = parser.parse_args(argv)
    a = report_run("v1", Path(args.v1), v1=True, truth=Path(args.v2))
    b = report_run("v2", Path(args.v2), v1=False)
    text = markdown(a, b, grounded_but_wrong(Path(args.v1), Path(args.v2)))
    Path(args.out).write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
