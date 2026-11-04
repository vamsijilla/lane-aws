"""Perturbation suite: process each perturbed email and check its expected effect against the
baseline submission (the unperturbed sample).

    uv run python -m lane.evals.perturb [--reprocess]

The perturbed emails come from scripts/make_perturbations.py (var/perturb/<name>.eml); the
expectations live in evals/perturb/<name>.yaml. The report goes to evals/report/perturbations.*.
"""

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lane.config import get_settings
from lane.db.base import get_sessionmaker
from lane.db.models import (
    Attachment,
    AuditEvent,
    Conflict,
    DecisionRecord,
    Document,
    DocumentExtraction,
    MissingInfo,
    Page,
    QualityBand,
    RuleOutcomeRecord,
    Run,
)
from lane.evals.gold import EVALS_DIR, Gold
from lane.evals.score import find_submission
from lane.intake.service import IntakeLimits, ingest_email
from lane.pipeline.registry import PIPELINE
from lane.pipeline.runner import process_submission
from lane.security.injection import ACTION as INJECTION
from lane.storage.blobs import blob_store

PERTURB_DIR = EVALS_DIR / "perturb"
SYNTHETIC_DIR = EVALS_DIR / "synthetic"
EMAIL_DIR = EVALS_DIR.parent / "var" / "perturb"
REPORT = EVALS_DIR / "report"


class Expect(BaseModel):
    same_route: bool = True
    same_rules_except: list[str] = Field(default_factory=lambda: list[str]())
    documents: int | None = None
    min_filename_mismatches: int | None = None
    missing_present: list[str] = Field(default_factory=lambda: list[str]())
    missing_absent: list[str] = Field(default_factory=lambda: list[str]())
    min_security_notes: int | None = None
    unreadable_files: list[str] = Field(default_factory=lambda: list[str]())
    low_quality_files: list[str] = Field(default_factory=lambda: list[str]())
    rules: dict[str, str] = Field(default_factory=lambda: dict[str, str]())  # rule -> outcome
    run_completes: bool = True
    # Files whose extraction must fail a recall check (rows on the page the read did not return)
    recall_failed: list[str] = Field(default_factory=lambda: list[str]())
    # A rule may become unknown, never anything else, when values it reads are in review
    # (read once, not verified: an image OCR cannot read). Uncertain values decide nothing.
    unknown_when_unverified: bool = False
    # A worse copy of a document never makes an outcome less careful. False only for
    # perturbations that add information or remove a document outright (not a worse copy).
    never_less_careful: bool = True


class Perturbation(BaseModel):
    name: str
    what: str
    expect: Expect


@dataclass
class Observed:
    documents: int
    filename_mismatches: int
    route: tuple[str, str | None, bool] | None  # outcome, team, potential decline
    headline: str | None
    rules: dict[str, str]
    missing: list[str]
    security_notes: int
    unreadable: list[str]
    run_status: str | None
    low_quality: list[str] = field(default_factory=lambda: list[str]())  # files with a poor page
    recall_failed: list[str] = field(default_factory=lambda: list[str]())  # files with rows unread
    unverified_rules: list[str] = field(default_factory=lambda: list[str]())  # unknown: in review


@dataclass
class Check:
    check: str
    ok: bool
    detail: str


@dataclass
class Result:
    name: str
    what: str
    submission_id: str
    passed: bool
    checks: list[Check] = field(default_factory=lambda: list[Check]())
    headline: str | None = None


def observe(session: Session, sid: str) -> Observed:
    d = session.get(DecisionRecord, sid)
    run = session.scalars(
        select(Run).where(Run.submission_id == sid).order_by(Run.started_at.desc())
    ).first()
    attachments = {
        a.id: a.filename
        for a in session.scalars(select(Attachment).where(Attachment.submission_id == sid))
    }
    events = session.scalars(
        select(AuditEvent).where(
            AuditEvent.action.in_((INJECTION, "attachment.unreadable", "attachment.damaged"))
        )
    )
    notes = 0
    unreadable: set[str] = set()
    for e in events:
        if e.action == INJECTION and e.payload.get("submission_id") == sid:
            notes += 1
        elif e.subject_id in attachments:  # unreadable, or damaged and only partly read
            unreadable.add(attachments[e.subject_id])
    return Observed(
        documents=session.scalar(
            select(func.count()).select_from(Document).where(Document.submission_id == sid)
        )
        or 0,
        filename_mismatches=session.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.submission_id == sid, Document.filename_mismatch)
        )
        or 0,
        route=(d.outcome, d.team, d.potential_decline) if d else None,
        headline=d.headline if d else None,
        rules={
            r.rule_id: f"{r.outcome}{' (potential decline)' if r.potential_decline else ''}"
            for r in session.scalars(
                select(RuleOutcomeRecord).where(RuleOutcomeRecord.submission_id == sid)
            )
        },
        missing=sorted(
            session.scalars(select(MissingInfo.code).where(MissingInfo.submission_id == sid))
        ),
        unverified_rules=sorted(
            r.rule_id
            for r in session.scalars(
                select(RuleOutcomeRecord).where(RuleOutcomeRecord.submission_id == sid)
            )
            if r.outcome == "unknown" and "could not be verified" in (r.reason or "")
        ),
        security_notes=notes,
        unreadable=sorted(unreadable),
        run_status=run.status.value if run else None,
        low_quality=sorted(
            {
                attachments[p.attachment_id]
                for p in session.scalars(select(Page).where(Page.submission_id == sid))
                if p.quality is not QualityBand.CLEAN and p.attachment_id in attachments
            }
        ),
        recall_failed=sorted(
            {
                attachments.get(doc.attachment_id, doc.title)
                for x, doc in session.execute(
                    select(DocumentExtraction, Document)
                    .join(Document, Document.id == DocumentExtraction.document_id)
                    .where(DocumentExtraction.submission_id == sid)
                )
                if any(
                    str(c.get("name", "")).startswith("recall:") and not c.get("passed")
                    for c in x.checks
                )
            }
        ),
    )


def check(p: Perturbation, got: Observed, base: Observed) -> list[Check]:
    e = p.expect
    out: list[Check] = []
    if e.same_route:
        out.append(
            Check(
                "Same recommendation and route",
                got.route == base.route,
                f"{got.headline} {got.route} vs {base.headline} {base.route}",
            )
        )
    changed = sorted(
        r for r in base.rules if got.rules.get(r) != base.rules[r] and r not in e.same_rules_except
    )
    # A worse copy of a document never makes the system less careful: nothing that was not a
    # pass becomes one (or not applicable)
    eased = sorted(
        r
        for r in base.rules
        if r in got.rules
        and not base.rules[r].startswith(("pass", "n/a"))
        and got.rules[r].startswith(("pass", "n/a"))
    )
    if e.never_less_careful:
        out.append(
            Check(
                "No outcome becomes less careful",
                not eased,
                ", ".join(f"{r}: {base.rules[r]} -> {got.rules[r]}" for r in eased) or "none",
            )
        )
    if e.unknown_when_unverified:
        held = [r for r in changed if r in got.unverified_rules]
        changed = [r for r in changed if r not in held]
        if held:
            out.append(
                Check(
                    "Rules reading values in review can't be checked yet, never another outcome",
                    True,
                    ", ".join(f"{r}: {base.rules[r]} -> unknown" for r in held),
                )
            )
    out.append(
        Check(
            "Other rule outcomes unchanged",
            not changed,
            ", ".join(f"{r}: {base.rules[r]} -> {got.rules.get(r)}" for r in changed)
            or "none changed",
        )
    )
    if e.documents is not None:
        out.append(
            Check(
                f"{e.documents} documents", got.documents == e.documents, f"{got.documents} found"
            )
        )
    if e.min_filename_mismatches is not None:
        out.append(
            Check(
                f"At least {e.min_filename_mismatches} misnamed files caught",
                got.filename_mismatches >= e.min_filename_mismatches,
                f"{got.filename_mismatches} caught",
            )
        )
    for code in e.missing_present:
        out.append(
            Check(
                f"Missing item {code}",
                any(c.startswith(code) for c in got.missing),
                "listed" if any(c.startswith(code) for c in got.missing) else "not listed",
            )
        )
    for code in e.missing_absent:
        present = [c for c in got.missing if c.startswith(code)]
        out.append(Check(f"No missing item {code}", not present, ", ".join(present) or "absent"))
    if e.min_security_notes is not None:
        out.append(
            Check(
                f"At least {e.min_security_notes} injection attempts recorded",
                got.security_notes >= e.min_security_notes,
                f"{got.security_notes} recorded",
            )
        )
    for rule, outcome in e.rules.items():
        out.append(
            Check(
                f"{rule} is {outcome}",
                got.rules.get(rule) == outcome,
                str(got.rules.get(rule)),
            )
        )
    for name in e.low_quality_files:
        out.append(
            Check(
                f"{name} marked as low quality",
                name in got.low_quality,
                ", ".join(got.low_quality) or "none flagged",
            )
        )
    for name in e.recall_failed:
        out.append(
            Check(
                f"{name} fails its recall check (rows on the page not read)",
                name in got.recall_failed,
                ", ".join(got.recall_failed) or "none",
            )
        )
    for name in e.unreadable_files:
        out.append(
            Check(
                f"{name} fails visibly", name in got.unreadable, ", ".join(got.unreadable) or "none"
            )
        )
    if e.run_completes:
        out.append(
            Check(
                "The run completes",
                got.run_status in ("succeeded", "degraded"),
                str(got.run_status),
            )
        )
    return out


class SyntheticExpect(BaseModel):
    route: tuple[str, str, bool]
    only_unknown: list[str]
    conflicts: int
    missing: int
    documents: int


class Synthetic(BaseModel):
    name: str
    what: str
    email: str
    expect: SyntheticExpect


def check_synthetic(s: Synthetic, got: Observed, conflicts: int) -> list[Check]:
    e = s.expect
    not_clean = sorted(
        f"{r}: {o}"
        for r, o in got.rules.items()
        if r not in e.only_unknown and o not in ("pass", "n/a")
    )
    unknown = sorted(r for r, o in got.rules.items() if o == "unknown")
    return [
        Check("Route", got.route == tuple(e.route), f"{got.headline} {got.route}"),
        Check("Every other rule passes", not not_clean, ", ".join(not_clean) or "all pass"),
        Check(
            f"Only {', '.join(e.only_unknown)} can't be checked yet",
            unknown == sorted(e.only_unknown),
            ", ".join(unknown) or "none",
        ),
        Check(f"{e.conflicts} conflicts", conflicts == e.conflicts, f"{conflicts} found"),
        Check(
            f"{e.missing} missing items",
            len(got.missing) == e.missing,
            ", ".join(got.missing) or "none",
        ),
        Check(f"{e.documents} documents", got.documents == e.documents, f"{got.documents} found"),
    ]


def run_synthetic(session: Session, *, reprocess: bool) -> list[Result]:
    settings = get_settings().model_copy(update={"templates_enabled": False})  # memory off
    store = blob_store(settings)
    results: list[Result] = []
    for spec_path in sorted(SYNTHETIC_DIR.glob("*.yaml")):
        s = Synthetic.model_validate(yaml.safe_load(spec_path.read_text()))
        eml = EVALS_DIR.parent / s.email
        if not eml.exists():
            raise SystemExit(f"{eml} is missing. Run scripts/make_synthetic_packet.py first.")
        sid = ingest_email(
            session,
            store,
            eml.read_bytes(),
            source="synthetic",
            limits=IntakeLimits.from_settings(settings),
            link_related=False,  # variants of one packet are separate cases here
        ).submission_id
        if reprocess or session.get(DecisionRecord, sid) is None:
            print(f"Processing {s.name}...", flush=True)
            process_submission(session, store, settings, sid, steps=PIPELINE, trigger="synthetic")
        got = observe(session, sid)
        conflicts = (
            session.scalar(
                select(func.count()).select_from(Conflict).where(Conflict.submission_id == sid)
            )
            or 0
        )
        checks = check_synthetic(s, got, conflicts)
        results.append(
            Result(
                f"synthetic: {s.name}", s.what, sid, all(c.ok for c in checks), checks, got.headline
            )
        )
    return results


def run_suite(session: Session, *, reprocess: bool, only: set[str] | None = None) -> list[Result]:
    settings = get_settings().model_copy(update={"templates_enabled": False})  # memory off
    store = blob_store(settings)
    gold = Gold.load("sample_packet")
    base_id = find_submission(session, gold)
    if base_id is None:
        raise SystemExit("Process the sample submission first (make run).")
    base = observe(session, base_id)
    results: list[Result] = []
    for spec_path in sorted(PERTURB_DIR.glob("*.yaml")):
        p = Perturbation.model_validate(yaml.safe_load(spec_path.read_text()))
        eml = EMAIL_DIR / f"{p.name}.eml"
        if not eml.exists():
            raise SystemExit(f"{eml} is missing. Run scripts/make_perturbations.py first.")
        ingested = ingest_email(
            session,
            store,
            eml.read_bytes(),
            source="perturb",
            limits=IntakeLimits.from_settings(settings),
            link_related=False,  # variants of one packet are separate cases here
        )
        sid = ingested.submission_id
        if (reprocess and (only is None or p.name in only)) or session.get(
            DecisionRecord, sid
        ) is None:
            print(f"Processing {p.name}...", flush=True)
            process_submission(session, store, settings, sid, steps=PIPELINE, trigger="perturb")
        got = observe(session, sid)
        checks = check(p, got, base)
        results.append(Result(p.name, p.what, sid, all(c.ok for c in checks), checks, got.headline))
    return results


def markdown(results: list[Result]) -> str:
    lines = [
        "# Perturbation suite",
        "",
        f"Generated {datetime.now(UTC).isoformat(timespec='seconds')}. Each perturbed email is processed "
        "end to end and compared with the unperturbed sample.",
        "",
        "| Perturbation | Result | Checks |",
        "|---|---|---|",
    ]
    for r in results:
        passed = sum(c.ok for c in r.checks)
        lines.append(
            f"| {r.name} | {'pass' if r.passed else 'FAIL'} | {passed} / {len(r.checks)} |"
        )
    for r in results:
        lines += ["", f"## {r.name}", "", r.what, ""]
        lines += [f"- {'ok' if c.ok else 'FAIL'}: {c.check} ({c.detail})" for c in r.checks]
    return "\n".join(lines) + "\n"


def write(results: list[Result], directory: Path = REPORT) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "results": [asdict(r) for r in results],
    }
    (directory / "perturbations.json").write_text(json.dumps(payload, indent=2) + "\n")
    path = directory / "perturbations.md"
    path.write_text(markdown(results))
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the perturbation suite")
    parser.add_argument("--reprocess", action="store_true", help="process the emails again")
    parser.add_argument("--only", nargs="*", help="with --reprocess, only these perturbations")
    args = parser.parse_args(argv)
    with get_sessionmaker()() as session:
        only = set(args.only) if args.only else None
        results = run_suite(session, reprocess=args.reprocess, only=only) + run_synthetic(
            session, reprocess=args.reprocess and only is None
        )
    path = write(results)
    print(markdown(results))
    print(f"Wrote {path}")
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
