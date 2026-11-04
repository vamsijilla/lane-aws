"""Score a processed submission against its gold set and write the eval report.

uv run python -m lane.evals.score [GOLD_NAME] [--submission ID]
"""

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from lane.config import get_settings
from lane.db.base import get_sessionmaker
from lane.db.models import (
    AnalysisRecord,
    Attachment,
    Conflict,
    DecisionRecord,
    Document,
    EmailMessage,
    Fact,
    FindingRecord,
    LlmCall,
    MissingInfo,
    Page,
    PageClassification,
    ResolvedEntity,
    ResolvedField,
    RuleOutcomeRecord,
    Run,
    Submission,
)
from lane.db.scratch import scratch_copy
from lane.documents.templates import layout_audits
from lane.evals.gold import EVALS_DIR, Gold, GoldField
from lane.llm.gateway import NOT_CACHED
from lane.pipeline.registry import PIPELINE
from lane.pipeline.runner import process_submission
from lane.storage.blobs import blob_store


@dataclass
class Metric:
    name: str
    correct: int
    total: int
    misses: list[str] = field(default_factory=lambda: list[str]())

    @property
    def rate(self) -> float | None:
        return round(self.correct / self.total, 3) if self.total else None


@dataclass
class Report:
    gold: str
    submission_id: str
    generated_at: str
    metrics: list[Metric]
    figures: dict[str, Any]


def norm(value: Any) -> str:
    """Compare values by meaning: case, currency, separators and trailing zeros ignored."""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip().lower().rstrip(".")
    bare = re.sub(r"[$,\s]", "", text)
    try:
        return format(Decimal(bare).normalize(), "f")
    except InvalidOperation:
        return re.sub(r"\s+", " ", text)


def _fields(session: Session, sid: str, gold: Gold) -> Metric:
    entities = list(
        session.scalars(select(ResolvedEntity).where(ResolvedEntity.submission_id == sid))
    )
    rows = list(session.scalars(select(ResolvedField).where(ResolvedField.submission_id == sid)))
    by_entity: dict[str, dict[str, ResolvedField]] = {}
    for f in rows:
        by_entity.setdefault(f.entity_id, {})[f.attribute] = f
    m = Metric("Field accuracy", 0, len(gold.fields))
    for g in gold.fields:
        label = f"{g.entity}{'[' + g.match + ']' if g.match else ''}.{g.attribute or g.detail}"
        if g.detail:
            entity = next(
                (
                    e
                    for e in entities
                    if e.kind == g.entity
                    and (not g.match or g.match.lower() in f"{e.key} {e.label}".lower())
                ),
                None,
            )
            got_detail = entity.details.get(g.detail) if entity else None
            if got_detail is not None and norm(got_detail) in {norm(v) for v in g.values}:
                m.correct += 1
            else:
                m.misses.append(f"{label}: got {got_detail}, expected {' or '.join(g.values)}")
            continue
        f = _find_field(entities, by_entity, g)
        if f is None:
            m.misses.append(f"{label}: not found")
            continue
        if g.state:
            ok = f.state == g.state
            got = f.state
        else:
            ok = f.state in ("known", "invalid") and norm(f.value) in {norm(v) for v in g.values}
            got = f"{f.state} {f.value_display}"
        if ok:
            m.correct += 1
        else:
            m.misses.append(f"{label}: got {got}, expected {g.state or ' or '.join(g.values)}")
    return m


def _find_field(
    entities: list[ResolvedEntity], by_entity: dict[str, dict[str, ResolvedField]], g: GoldField
) -> ResolvedField | None:
    for e in entities:
        if e.kind != g.entity:
            continue
        if g.match and g.match.lower() not in f"{e.key} {e.label}".lower():
            continue
        f = by_entity.get(e.id, {}).get(g.attribute or "")
        if f is not None:
            return f
    return None


def _documents(session: Session, sid: str, gold: Gold) -> tuple[Metric, Metric, Metric]:
    rows = session.execute(
        select(Document, Attachment.filename)
        .join(Attachment, Attachment.id == Document.attachment_id)
        .where(Document.submission_id == sid)
        .order_by(Attachment.filename, Document.page_from)
    ).all()
    got: dict[str, list[str]] = {}
    for d, filename in rows:
        name = "Email body" if d.doc_type == "email_body" else filename
        got.setdefault(name, []).append(d.doc_type)
    expected_total = sum(len(g.types) for g in gold.documents)
    classified = Metric("Document classification", 0, expected_total)
    split = Metric("Splitting (files with several documents)", 0, 0)
    for g in gold.documents:
        found = got.get(g.file, [])
        overlap = Counter(g.types) & Counter(found)
        classified.correct += sum(overlap.values())
        if sum(overlap.values()) < len(g.types) or len(found) != len(g.types):
            classified.misses.append(f"{g.file}: expected {g.types}, got {found}")
        if len(g.types) > 1:
            split.total += 1
            if sorted(found) == sorted(g.types):
                split.correct += 1
            else:
                split.misses.append(
                    f"{g.file}: expected {len(g.types)} documents, got {len(found)}"
                )
    superseded = Metric("Supersession", 0, len(gold.supersessions))
    for s in gold.supersessions:
        n = sum(
            1
            for d, filename in rows
            if filename == s.file
            and d.doc_type == s.type
            and s.subject_contains.lower() in d.title.lower()
            and d.superseded_by_id
        )
        if n == s.superseded:
            superseded.correct += 1
        else:
            superseded.misses.append(
                f"{s.file} {s.subject_contains}: {n} superseded, expected {s.superseded}"
            )
    return classified, split, superseded


def _conflicts(session: Session, sid: str, gold: Gold) -> tuple[Metric, Metric]:
    titles = [
        c.title for c in session.scalars(select(Conflict).where(Conflict.submission_id == sid))
    ]
    codes = [
        m.code for m in session.scalars(select(MissingInfo).where(MissingInfo.submission_id == sid))
    ]
    recall = Metric("Conflict recall", 0, len(gold.conflicts))
    for g in gold.conflicts:
        hit = any(g.label.lower() in t.lower() for t in titles) or bool(
            g.or_missing and any(c.startswith(g.or_missing) for c in codes)
        )
        if hit:
            recall.correct += 1
        else:
            recall.misses.append(f"{g.id} {g.label}")
    valid = [g.label.lower() for g in gold.conflicts] + [
        v.lower() for v in gold.conflicts_also_valid
    ]
    precision = Metric("Conflict precision", 0, len(titles))
    for t in titles:
        if any(v in t.lower() for v in valid):
            precision.correct += 1
        else:
            precision.misses.append(f"unexpected: {t}")
    return recall, precision


def _missing(session: Session, sid: str, gold: Gold, outcomes: dict[str, str]) -> Metric:
    codes = [
        m.code for m in session.scalars(select(MissingInfo).where(MissingInfo.submission_id == sid))
    ]
    m = Metric("Missing-information recall", 0, len(gold.missing))
    for g in gold.missing:
        hit = any(c.startswith(p) for p in g.codes for c in codes) or any(
            outcomes.get(r) in ("refer", "decline") for r in g.rules
        )
        if hit:
            m.correct += 1
        else:
            m.misses.append(f"{g.id} ({', '.join(g.codes or g.rules)})")
    return m


def _rules(outcomes: dict[str, str], gold: Gold) -> Metric:
    m = Metric("Rule outcomes", 0, len(gold.rules))
    for rule_id, allowed in gold.rules.items():
        got = outcomes.get(rule_id)
        if got in allowed:
            m.correct += 1
        else:
            m.misses.append(f"{rule_id}: got {got}, expected {' or '.join(allowed)}")
    return m


def _route(session: Session, sid: str, gold: Gold) -> Metric:
    d = session.get(DecisionRecord, sid)
    m = Metric("Route and recommendation", 0, 3)
    if d is None:
        m.misses.append("no decision")
        return m
    checks = [
        ("outcome", d.outcome, gold.route.outcome),
        ("team", d.team, gold.route.team),
        ("potential decline", d.potential_decline, gold.route.potential_decline),
    ]
    for name, got, want in checks:
        if got == want:
            m.correct += 1
        else:
            m.misses.append(f"{name}: got {got}, expected {want}")
    if gold.route.potential_decline_rule:
        rule = session.scalars(
            select(RuleOutcomeRecord).where(
                RuleOutcomeRecord.submission_id == sid,
                RuleOutcomeRecord.rule_id == gold.route.potential_decline_rule,
            )
        ).first()
        m.total += 1
        if rule is not None and rule.potential_decline:
            m.correct += 1
        else:
            m.misses.append(f"{gold.route.potential_decline_rule} is not the potential decline")
    return m


def _findings(session: Session, sid: str, gold: Gold) -> Metric:
    analysis = session.get(AnalysisRecord, sid)
    rows = list(session.scalars(select(FindingRecord).where(FindingRecord.submission_id == sid)))
    m = Metric("Expert finding coverage", 0, len(gold.findings))
    if analysis is None or not rows:
        m.misses.append("analysis not run")
        return m
    texts = [f"{f.title} {f.body}".lower() for f in rows]
    for g in gold.findings:
        if any(all(any(w.lower() in t for w in group) for group in g.all) for t in texts):
            m.correct += 1
        else:
            m.misses.append(f"{g.id} {g.topic}")
    return m


def _figures(session: Session, sid: str) -> dict[str, Any]:
    facts = (
        session.scalar(select(func.count()).select_from(Fact).where(Fact.submission_id == sid)) or 0
    )
    grounded = (
        session.scalar(
            select(func.count())
            .select_from(Fact)
            .where(Fact.submission_id == sid, Fact.grounding != "none")
        )
        or 0
    )
    cost_by_purpose: dict[str, float] = {
        purpose: float(total or 0)
        for purpose, total in session.execute(
            select(LlmCall.purpose, func.sum(LlmCall.cost_usd))
            .join(Run, Run.id == LlmCall.run_id)
            .where(Run.submission_id == sid)
            .group_by(LlmCall.purpose)
        )
    }
    analysis = session.get(AnalysisRecord, sid)
    if analysis is not None:
        cost_by_purpose["analyze (latest)"] = analysis.cost_usd
    findings = list(
        session.scalars(select(FindingRecord).where(FindingRecord.submission_id == sid))
    )
    signals = [
        sig
        for (sig,) in session.execute(
            select(PageClassification.signals)
            .join(Page, Page.id == PageClassification.page_id)
            .where(Page.submission_id == sid)
        )
    ]
    by_layout = [s for s in signals if s.get("method") == "layout"]
    audits = layout_audits(signals)  # the count the Rules library shows too
    return {
        "pages_classified": len(signals),
        # Scored with known layouts off: pages typed by tier 1 signals and the model, the honest
        # accuracy; with them on, the same pages cost fewer model calls (reported as speed)
        "layout_memory": any(s.get("method") == "layout" for s in signals),
        "pages_by_layout": len(by_layout),
        "pages_by_tier1": sum(1 for s in signals if s.get("method") == "tier1"),
        "layout_audited": sum(r[1] for r in audits.values()),
        "layout_audit_agreed": sum(r[2] for r in audits.values()),
        "facts": facts,
        "grounding_rate": round(grounded / facts, 3) if facts else None,
        "findings": len(findings),
        "findings_by_kind": dict(Counter(f.kind for f in findings)),
        "findings_cited": all(f.citations for f in findings),
        "cost_usd_by_purpose": {k: round(float(v or 0), 4) for k, v in cost_by_purpose.items()},
    }


def score(session: Session, gold: Gold, submission_id: str) -> Report:
    outcomes = {
        r.rule_id: r.outcome
        for r in session.scalars(
            select(RuleOutcomeRecord).where(RuleOutcomeRecord.submission_id == submission_id)
        )
    }
    classified, split, superseded = _documents(session, submission_id, gold)
    recall, precision = _conflicts(session, submission_id, gold)
    metrics = [
        classified,
        split,
        superseded,
        _fields(session, submission_id, gold),
        recall,
        precision,
        _missing(session, submission_id, gold, outcomes),
        _rules(outcomes, gold),
        _route(session, submission_id, gold),
        _findings(session, submission_id, gold),
    ]
    return Report(
        gold.name,
        submission_id,
        datetime.now(UTC).isoformat(timespec="seconds"),
        metrics,
        _figures(session, submission_id),
    )


def find_submission(session: Session, gold: Gold) -> str | None:
    """The latest decided submission whose email subject matches the gold set, leaving out the
    perturbed copies (their Message-IDs contain "perturb-")."""
    return session.scalar(
        select(Submission.id)
        .join(EmailMessage, EmailMessage.id == Submission.email_id)
        .join(DecisionRecord, DecisionRecord.submission_id == Submission.id)
        .where(
            EmailMessage.subject.contains(gold.email_subject_contains),
            ~func.coalesce(EmailMessage.message_id, "").contains("perturb-"),
        )
        .order_by(Submission.created_at.desc())
    )


def markdown(report: Report) -> str:
    lines = [
        f"# Eval report: {report.gold}",
        "",
        f"Submission `{report.submission_id}`, generated {report.generated_at}.",
        "",
        "| Metric | Score | Rate |",
        "|---|---|---|",
    ]
    for m in report.metrics:
        rate = f"{m.rate:.0%}" if m.rate is not None else "n/a"
        lines.append(f"| {m.name} | {m.correct} / {m.total} | {rate} |")
    f = report.figures
    lines += [
        "",
        f"Facts: {f['facts']}, grounded on the source: {f['grounding_rate']:.1%}."
        if f["grounding_rate"] is not None
        else f"Facts: {f['facts']}.",
        f"Findings: {f['findings']} {f['findings_by_kind']}, every finding cited: {f['findings_cited']}.",
        "Model cost (USD): "
        + ", ".join(f"{k} {v:.4f}" for k, v in f["cost_usd_by_purpose"].items()),
        "",
        "## Misses",
        "",
    ]
    for m in report.metrics:
        for miss in m.misses:
            lines.append(f"- {m.name}: {miss}")
    if not any(m.misses for m in report.metrics):
        lines.append("None.")
    return "\n".join(lines) + "\n"


def write(report: Report, directory: Path = EVALS_DIR / "report") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    payload = asdict(report)
    for m, raw in zip(report.metrics, payload["metrics"], strict=True):
        raw["rate"] = m.rate
    (directory / f"{report.gold}.json").write_text(json.dumps(payload, indent=2) + "\n")
    path = directory / f"{report.gold}.md"
    path.write_text(markdown(report))
    return path


def what_if(session: Session, gold: Gold, sid: str, **overrides: Any) -> tuple[Report, int]:
    """Score the same submission processed again with other settings (templates off, say), on
    a copy of the database, with model answers only from the cache. Nothing stored changes and
    nothing is spent. Returns the report and how many model answers the cache did not have;
    metrics that depend on those answers are not comparable."""
    with scratch_copy(session.get_bind().engine) as engine:
        settings = get_settings().model_copy(update={"llm_cache_only": True, **overrides})
        with sessionmaker(engine, expire_on_commit=False)() as scratch:
            run = process_submission(
                scratch,
                blob_store(settings),
                settings,
                sid,
                steps=PIPELINE,
                trigger="what-if",
                replay=True,
            )
            missed = (
                scratch.scalar(
                    select(func.count())
                    .select_from(LlmCall)
                    .where(LlmCall.run_id == run.id, LlmCall.error == NOT_CACHED)
                )
                or 0
            )
            return score(scratch, gold, sid), missed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score a submission against a gold set")
    parser.add_argument("gold", nargs="?", default="sample_packet")
    parser.add_argument("--submission")
    parser.add_argument(
        "--templates-off",
        action="store_true",
        help="score a what-if run with layout templates off (cache only, on a copy)",
    )
    args = parser.parse_args(argv)
    gold = Gold.load(args.gold)
    with get_sessionmaker()() as session:
        sid = args.submission or find_submission(session, gold)
        if sid is None:
            print(f"No processed submission matches {gold.email_subject_contains!r}. Run make run.")
            return 1
        if args.templates_off:
            report, missed = what_if(session, gold, sid, templates_enabled=False)
            print(markdown(report))
            print("What-if run with layout templates off; nothing was written.")
            if missed:
                print(
                    f"{missed} model answers were not in the cache (requests differ when titles "
                    "or types differ), so metrics that depend on them are not comparable. "
                    "Classification, splitting and supersession are."
                )
            return 0
        report = score(session, gold, sid)
    path = write(report)
    print(markdown(report))
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
