"""Run and inspect the pipeline from the command line.

uv run python -m lane.pipeline.cli process [SUBMISSION_ID | --latest]
uv run python -m lane.pipeline.cli pages   [SUBMISSION_ID | --latest]
"""

import argparse
import json
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.actions.service import build_actions
from lane.analyze.service import analyze_submission, approve_analysis
from lane.config import get_settings
from lane.db.base import get_sessionmaker
from lane.db.models import (
    AgentRequestItem,
    AnalysisRecord,
    Attachment,
    Conflict,
    DecisionRecord,
    Document,
    DocumentExtraction,
    FindingRecord,
    MissingInfo,
    Page,
    ResolvedEntity,
    RuleOutcomeRecord,
    Submission,
)
from lane.extract.service import approve_escalations, pending_escalations
from lane.llm.gateway import LlmGateway
from lane.pipeline.registry import PIPELINE
from lane.pipeline.runner import process_submission
from lane.storage.blobs import blob_store


def _resolve(session: Session, submission_id: str | None) -> Submission:
    if submission_id:
        sub = session.get(Submission, submission_id)
    else:
        sub = session.scalars(select(Submission).order_by(Submission.created_at.desc())).first()
    if sub is None:
        raise SystemExit("No matching submission. Ingest an email first (make ingest).")
    return sub


def cmd_process(session: Session, sub: Submission) -> int:
    settings = get_settings()
    run = process_submission(
        session, blob_store(settings), settings, sub.id, steps=PIPELINE, trigger="cli"
    )
    print(f"Run {run.id}: {run.status.value}")
    for step in run.steps:
        ms = step.duration_ms or 0
        print(f"  {step.name:<10} {step.status.value:<10} {ms:>6} ms  {step.summary}")
        if step.error:
            print(f"    error: {step.error}")
    return 0 if run.status.value != "failed" else 1


def cmd_pages(session: Session, sub: Submission) -> int:
    rows = session.execute(
        select(Page, Attachment.filename)
        .join(Attachment, Page.attachment_id == Attachment.id)
        .where(Page.submission_id == sub.id)
        .order_by(Attachment.ordinal, Page.page_no)
    ).all()
    header = ("file", "pg", "kind", "text", "img", "sharp", "color", "skew", "quality", "notes")
    print("{:<44} {:>3} {:<10} {:>5} {:>4} {:>6} {:>6} {:>6} {:<9} {}".format(*header))
    for page, filename in rows:
        name = filename if len(filename) <= 44 else filename[:41] + "..."
        notes = "; ".join(
            ([f"sheet {page.sheet_name!r}"] if page.sheet_name else []) + page.warnings
        )
        print(
            f"{name:<44} {page.page_no:>3} {page.kind.value:<10} {page.text_chars:>5} "
            f"{page.image_coverage:>4.2f} {_num(page.sharpness):>6} {_num(page.colorfulness):>6} "
            f"{_num(page.skew_deg):>6} {page.quality.value:<9} {notes}"
        )
    print(f"{len(rows)} pages")
    return 0


def cmd_documents(session: Session, sub: Submission) -> int:
    rows = session.execute(
        select(Document, Attachment.filename)
        .join(Attachment, Document.attachment_id == Attachment.id)
        .where(Document.submission_id == sub.id)
        .order_by(Document.ordinal)
    ).all()
    for doc, filename in rows:
        name = filename if len(filename) <= 40 else filename[:37] + "..."
        pages = f"p{doc.page_from}" + (f"-{doc.page_to}" if doc.page_to != doc.page_from else "")
        flags = [
            label
            for label, on in (
                ("superseded", doc.superseded_by_id),
                ("filename mismatch", doc.filename_mismatch),
                ("review", doc.needs_review),
            )
            if on
        ]
        print(
            f"{doc.ordinal:>3} {name:<40} {pages:<7} {doc.doc_type:<20} "
            f"{doc.confidence.value:<7} {doc.title}  {', '.join(flags)}"
        )
    print(f"{len(rows)} documents")
    return 0


def cmd_resolution(session: Session, sub: Submission) -> int:
    """Conflicts, missing information and who/what the submission is about."""
    conflicts = session.scalars(select(Conflict).where(Conflict.submission_id == sub.id)).all()
    print(f"Conflicts ({len(conflicts)})")
    for c in conflicts:
        values = "  vs  ".join(
            f"{x['display']} ({', '.join(src['title'][:30] for src in x['sources'])})"
            for x in c.candidates
        )
        print(f"  [{c.type}] {c.title}\n      {values}")
    missing = session.scalars(select(MissingInfo).where(MissingInfo.submission_id == sub.id)).all()
    print(f"Missing or incomplete ({len(missing)})")
    for m in missing:
        print(f"  {m.title}: {m.detail}")
    for kind in ("driver", "vehicle"):
        entities = session.scalars(
            select(ResolvedEntity).where(
                ResolvedEntity.submission_id == sub.id, ResolvedEntity.kind == kind
            )
        ).all()
        print(f"{kind.capitalize()}s ({len(entities)})")
        for e in entities:
            where = "scheduled" if e.details.get("scheduled") else "only in loss history"
            print(f"  {e.label}: {where}; found in {', '.join(e.found_in)}")
    return 0


def cmd_rules(session: Session, sub: Submission) -> int:
    """The decision and every rule outcome, grouped by guideline section."""
    decision = session.get(DecisionRecord, sub.id)
    if decision is None:
        print("No decision yet. Run make process.")
        return 1
    tag = " (potential decline)" if decision.potential_decline else ""
    print(f"{decision.headline}{tag}")
    print(f"  Route: {decision.route_reason}")
    print(
        f"  Counting from the {decision.anchor.replace('_', ' ')} ({decision.anchor_date}); ruleset {decision.ruleset_version}"
    )
    results = session.scalars(
        select(RuleOutcomeRecord)
        .where(RuleOutcomeRecord.submission_id == sub.id)
        .order_by(RuleOutcomeRecord.ordinal)
    ).all()
    section = None
    for r in results:
        if r.section != section:
            section = r.section
            print(f"\n{section}")
        marker = " potential decline" if r.potential_decline else ""
        print(f"  {r.rule_id:<8} {r.outcome.upper():<8}{marker} {r.title}: {r.reason}")
        for item in r.items:
            print(f"             - {item}")
        if r.anchor_note:
            print(f"             note: {r.anchor_note}")
        for s in r.sensitivity:
            print(f"             if {s['field']} = {s['value']}: {s['outcome']}")
    return 0


def cmd_analyze(session: Session, sub: Submission, *, approve: bool = False) -> int:
    """Run the analyst (cached when nothing changed), then print the summary and findings.
    A paid run waits for approval, as in the app; `--approve` is that approval, given here."""
    result = analyze_submission(session, sub, get_settings(), LlmGateway, run_id=None)
    record = session.get(AnalysisRecord, sub.id)
    if result.status == "awaiting_approval" and record is not None:
        if not approve:
            print(
                f"Analysis waits for approval ({result.error}). Run with --approve to pay for it."
            )
            return 1
        approve_analysis(session, record, actor="user:cli")
        result = analyze_submission(session, sub, get_settings(), LlmGateway, run_id=None)
    record = session.get(AnalysisRecord, sub.id)
    print(f"Analysis {result.status}: {result.attempts} attempt(s), ${result.cost_usd:.4f}")
    if result.error:
        print(f"  {result.error}")
    if record is None or not record.summary:
        return 0 if result.status == "ok" else 1
    print()
    for s in record.summary:
        print(f"{s['text']} {''.join(f'[{n}]' for n in s['footnotes'])}")
    print()
    for note in record.footnotes:
        print(f"  [{note['n']}] {note['ref']} {note['label']}")
    findings = session.scalars(
        select(FindingRecord)
        .where(FindingRecord.submission_id == sub.id)
        .order_by(FindingRecord.ordinal)
    ).all()
    print(f"\n{len(findings)} findings")
    for f in findings:
        print(f"\n  {f.severity.upper():<6} {f.kind:<14} {f.title}  [{f.confidence}]")
        print(f"         {f.body}")
        print(f"         basis: {f.basis}")
        print(f"         cites: {', '.join(f.citations)}")
    for r in record.rejected:
        print(f"  rejected: {r['title']}: {r['reason']}")
    for n in record.security_notes:
        print(f"  security: {n['text']} ({n['where']})")
    return 0


def cmd_actions(session: Session, sub: Submission) -> int:
    """Build the agent request and draft email, then print them. Nothing is sent."""
    result = build_actions(session, sub, get_settings(), LlmGateway, run_id=None)
    items = session.scalars(
        select(AgentRequestItem)
        .where(AgentRequestItem.submission_id == sub.id)
        .order_by(AgentRequestItem.ordinal)
    ).all()
    print(f"{len(items)} request items")
    for i in items:
        flag = "" if i.included else " (excluded)"
        print(f"  [{i.group}] {i.text}{flag}  <- {', '.join(i.source_refs[:4])}")
    d = result.draft
    print(f"\nDraft v{d.version} ({d.method}{', ' + d.model if d.model else ''}), not sent")
    print(f"To: {d.to}\nSubject: {d.subject}\n\n{d.body}")
    for problem in d.problems:
        print(f"  note: {problem}")
    return 0


def cmd_escalate(session: Session, sub: Submission, *, yes: bool) -> int:
    """List documents waiting for the stronger model, ask, then approve and rerun."""
    settings = get_settings()
    pending = pending_escalations(session, sub.id)
    if not pending:
        print("Nothing is waiting for escalation.")
        return 0
    total = sum(p.escalation_cost_usd or 0 for p in pending)
    print(f"{len(pending)} documents failed their checks on {settings.llm_extract_model}:")
    for p in pending:
        doc = session.get(Document, p.document_id)
        print(f"  {doc.title if doc else p.document_id}  (about ${p.escalation_cost_usd or 0:.3f})")
        for reason in p.escalation_reasons:
            print(f"    - {reason}")
    question = f"Re-read them with {settings.llm_escalation_model} for about ${total:.2f}? [y/N] "
    if not yes and input(question).strip().lower() not in {"y", "yes"}:
        print("Not approved. Nothing was sent.")
        return 0
    approve_escalations(session, sub.id, actor="user:cli")
    return cmd_process(session, sub)


def cmd_export(session: Session, sub: Submission) -> int:
    """The whole submission as JSON: documents, extracted data, checks and statuses."""
    docs = session.scalars(
        select(Document).where(Document.submission_id == sub.id).order_by(Document.ordinal)
    ).all()
    extractions = {
        x.document_id: x
        for x in session.scalars(
            select(DocumentExtraction).where(DocumentExtraction.submission_id == sub.id)
        )
    }
    rows = [(d, extractions.get(d.id)) for d in docs]
    out = {
        "submission_id": sub.id,
        "subject": sub.email.subject,
        "documents": [
            {
                "title": d.title,
                "doc_type": d.doc_type,
                "file": d.attachment.filename,
                "pages": [d.page_from, d.page_to],
                "confidence": d.confidence.value,
                "superseded": bool(d.superseded_by_id),
                "filename_mismatch": d.filename_mismatch,
                "extraction": None
                if x is None
                else {
                    "status": x.status.value,
                    "model": x.model,
                    "checks": x.checks,
                    "data": x.data,
                },
            }
            for d, x in rows
        ],
    }
    print(json.dumps(out, indent=2, default=str))
    return 0


def _num(value: float | None) -> str:
    return "" if value is None else f"{value:g}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lane pipeline")
    parser.add_argument(
        "command",
        choices=[
            "process",
            "pages",
            "documents",
            "escalate",
            "export",
            "resolution",
            "rules",
            "analyze",
            "actions",
        ],
    )
    parser.add_argument("--yes", action="store_true", help="approve escalation without asking")
    parser.add_argument(
        "--approve", action="store_true", help="approve the paid analyst run (analyze only)"
    )
    parser.add_argument("submission_id", nargs="?")
    parser.add_argument("--latest", action="store_true", help="use the most recent submission")
    args = parser.parse_args(argv)
    with get_sessionmaker()() as session:
        sub = _resolve(session, None if args.latest else args.submission_id)
        if args.command == "escalate":
            return cmd_escalate(session, sub, yes=args.yes)
        if args.command == "analyze":
            return cmd_analyze(session, sub, approve=args.approve)
        handlers = {
            "process": cmd_process,
            "pages": cmd_pages,
            "documents": cmd_documents,
            "export": cmd_export,
            "resolution": cmd_resolution,
            "rules": cmd_rules,
            "analyze": cmd_analyze,
            "actions": cmd_actions,
        }
        return handlers[args.command](session, sub)


if __name__ == "__main__":
    sys.exit(main())
