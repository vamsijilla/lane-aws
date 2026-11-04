"""Layout memory as a speed figure, never as accuracy.

    uv run python -m lane.evals.layout_speed --source SUBMISSION_ID   (against the eval database)

Accuracy is always scored with layout memory off. This measures what memory saves once people
have confirmed documents: every document of one submission is confirmed through the same path as
the Confirm type action (so each layout records where it came from), then every other submission
is classified again on a scratch copy of the database with layouts on, answers from the cache
only. Model calls and classification time are compared with the stored layout-memory-off runs.
A second pass audits every layout-typed page (rate 1.0) and reports how often the model agreed.
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from lane.config import get_settings
from lane.db.base import get_sessionmaker
from lane.db.models import Document, EmailMessage, LearnedLayout, Run, Step, Submission
from lane.db.scratch import scratch_copy
from lane.documents import layouts
from lane.pipeline.registry import PIPELINE
from lane.pipeline.runner import process_submission
from lane.storage.blobs import blob_store

ROOT = Path(__file__).resolve().parents[4]
REPORT = ROOT / "evals" / "report" / "layout_memory.md"
ACTOR = "eval:confirmed-sample"
CLASSIFY = [s for s in PIPELINE if s.name == "classify"]


@dataclass
class Row:
    subject: str
    calls_off: int
    calls_on: int
    ms_off: int
    ms_on: int
    layout_pages: int


def _latest_classify(session: Session, sid: str) -> Step | None:
    return session.scalars(
        select(Step)
        .join(Run, Run.id == Step.run_id)
        .where(Run.submission_id == sid, Step.name == "classify", Run.trigger != "replay")
        .order_by(Step.started_at.desc())
        .limit(1)
    ).first()


def learn_from(session: Session, source: str) -> int:
    store = blob_store(get_settings())
    docs = session.scalars(
        select(Document).where(Document.submission_id == source, Document.doc_type != "email_body")
    ).all()
    learned = sum(len(layouts.learn(session, store, d, actor=ACTOR)) for d in docs)
    session.commit()
    return learned


def measure(source: str) -> tuple[list[Row], int, tuple[int, int]]:
    settings = get_settings()
    with get_sessionmaker()() as session:
        targets = [
            (sid, subject)
            for sid, subject in session.execute(
                select(Submission.id, EmailMessage.subject)
                .join(EmailMessage, EmailMessage.id == Submission.email_id)
                .where(Submission.id != source)
            )
        ]
        off = {sid: _latest_classify(session, sid) for sid, _ in targets}
        engine = session.get_bind().engine

    rows: list[Row] = []
    on_settings = settings.model_copy(
        update={"llm_cache_only": True, "templates_enabled": True, "templates_audit_rate": 0.1}
    )
    # Layouts are learned on the scratch copy only: the eval database keeps memory off
    with scratch_copy(engine) as copy, sessionmaker(copy, expire_on_commit=False)() as scratch:
        learned = learn_from(scratch, source)
        for sid, subject in targets:
            before = off[sid]
            if before is None:
                continue
            run = process_submission(
                scratch,
                blob_store(on_settings),
                on_settings,
                sid,
                steps=CLASSIFY,
                trigger="what-if",
            )
            step = run.steps[0]
            llm_off = (before.summary or {}).get("llm", {})
            llm_on = (step.summary or {}).get("llm", {})
            rows.append(
                Row(
                    subject=subject,
                    calls_off=int(llm_off.get("calls", 0)),
                    calls_on=int(llm_on.get("calls", 0)),
                    ms_off=before.duration_ms or 0,
                    ms_on=step.duration_ms or 0,
                    layout_pages=int(llm_on.get("layout_pages", 0)),
                )
            )

    audit_settings = on_settings.model_copy(update={"templates_audit_rate": 1.0})
    with scratch_copy(engine) as copy, sessionmaker(copy, expire_on_commit=False)() as scratch:
        learn_from(scratch, source)
        for sid, _ in targets:
            if off[sid] is not None:
                process_submission(
                    scratch,
                    blob_store(audit_settings),
                    audit_settings,
                    sid,
                    steps=CLASSIFY,
                    trigger="what-if",
                )
        audits = sum(r.audits for r in scratch.scalars(select(LearnedLayout)))
        agreed = sum(r.agreements for r in scratch.scalars(select(LearnedLayout)))
    return rows, learned, (agreed, audits)


def markdown(rows: list[Row], learned: int, audit: tuple[int, int]) -> str:
    calls_off = sum(r.calls_off for r in rows)
    calls_on = sum(r.calls_on for r in rows)
    ms_off = sum(r.ms_off for r in rows)
    ms_on = sum(r.ms_on for r in rows)
    saved = calls_off - calls_on
    lines = [
        "# Layout memory: a speed figure, not accuracy",
        "",
        "Classification accuracy is scored with layout memory off (`sample_packet.md`). This",
        f"measures what memory saves after people confirm documents: {learned} layouts were learned",
        "by confirming the sample's documents in the eval database (the Confirm type path, with",
        "provenance), then every other submission was classified again with layouts on, from the",
        "cache only.",
        "",
        "| Submission | Model calls, memory off | Memory on | Pages typed by a layout "
        "| Classify time off | On |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.subject[:60]} | {r.calls_off} | {r.calls_on} | {r.layout_pages} "
            f"| {r.ms_off / 1000:.1f} s | {r.ms_on / 1000:.1f} s |"
        )
    pct = f"{saved / calls_off:.0%}" if calls_off else "0%"
    lines += [
        f"| **All** | **{calls_off}** | **{calls_on}** | | **{ms_off / 1000:.1f} s** "
        f"| **{ms_on / 1000:.1f} s** |",
        "",
        f"Model calls saved: {saved} of {calls_off} ({pct}), at the production audit rate (10% of",
        "layout-typed pages still go to the model).",
        "",
        f"AI audit of every layout-typed page (rate 1.0): the model agreed on {audit[0]} of "
        f"{audit[1]}.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="the submission people confirmed")
    args = parser.parse_args(argv)
    rows, learned, audit = measure(args.source)
    text = markdown(rows, learned, audit)
    REPORT.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
