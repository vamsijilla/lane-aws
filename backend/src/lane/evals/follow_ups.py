"""Follow-up emails against a real submission: what Lane does with each kind of second email.

    uv run python -m lane.evals.follow_ups --submission ID --reply path/to/reply.eml

Each case runs on its own scratch copy of the database, so the real results never change:

1. the same email twice: nothing new
2. the same packet in a new email: linked as the same files, nothing processed
3. a reply with the reference token: attached, the submission runs again
4. a reply without the token whose PDFs carry the applicant's DOT, from the same domain: attached
5. a reply that only names the applicant: suggested as a possible follow-up, never merged

It also times a resend of the whole packet as a new submission with the OCR cache empty and then
warm (triage only), and writes evals/report/follow_ups.md.
"""

import argparse
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from lane.config import get_settings
from lane.db.base import get_sessionmaker
from lane.db.models import Blob, OcrCacheEntry, Submission
from lane.db.scratch import scratch_copy
from lane.intake.link import TOKEN
from lane.intake.service import IngestResult, IntakeLimits, ingest_email
from lane.pipeline.registry import PIPELINE
from lane.pipeline.runner import process_submission
from lane.storage.blobs import blob_store

ROOT = Path(__file__).resolve().parents[4]
REPORT = ROOT / "evals" / "report" / "follow_ups.md"
TRIAGE = [s for s in PIPELINE if s.name == "triage"]


@dataclass
class Case:
    name: str
    expected: str
    got: str
    passed: bool


def _email(raw: bytes) -> EmailMessage:
    msg = message_from_bytes(raw, policy=policy.default)
    assert isinstance(msg, EmailMessage)
    return msg


def _new_id(raw: bytes) -> bytes:
    """The same email, sent again as a new message."""
    msg = _email(raw)
    del msg["Message-ID"]
    msg["Message-ID"] = make_msgid(domain="resend.example")
    return msg.as_bytes()


def _without_reference(raw: bytes) -> bytes:
    msg = _email(raw)
    subject = TOKEN.sub("", str(msg["Subject"])).strip()
    del msg["Subject"], msg["References"], msg["In-Reply-To"], msg["Message-ID"]
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain="noref.example")
    return msg.as_bytes()


def _name_only(raw: bytes, applicant: str) -> bytes:
    """From the agent's domain, naming the applicant, with no reference, thread or numbers."""
    reply = _email(raw)
    msg = EmailMessage()
    msg["From"] = reply["From"]
    msg["To"] = reply["To"]
    msg["Subject"] = f"{applicant}: one more document"
    msg["Message-ID"] = make_msgid(domain="nameonly.example")
    msg.set_content(f"Hi,\n\nOne more document for {applicant}.\n")
    msg.add_attachment(
        b"%PDF-1.4\n% a placeholder the reader never opens\n",
        maintype="application",
        subtype="pdf",
        filename="note.pdf",
    )
    return msg.as_bytes()


def _ingest(session: Session, raw: bytes) -> IngestResult:
    s = get_settings()
    return ingest_email(
        session, blob_store(s), raw, source="eval", limits=IntakeLimits.from_settings(s)
    )


def _on_copy(engine: Engine, fn: Callable[[Session], Case]) -> Case:
    with scratch_copy(engine) as copy, sessionmaker(copy, expire_on_commit=False)() as session:
        return fn(session)


def run(submission_id: str, reply: bytes) -> tuple[list[Case], tuple[float, float, float, int]]:
    settings = get_settings()
    store = blob_store(settings)
    with get_sessionmaker()() as session:
        sub = session.get(Submission, submission_id)
        assert sub is not None, f"No submission {submission_id}"
        blob = session.get(Blob, sub.email.raw_sha256)
        assert blob is not None
        original = store.get(blob.storage_key)
        from lane.overview.service import known_values

        applicant = known_values(session, sub.id).get(("applicant", "legal_name")) or ""
        engine = session.get_bind().engine

    def twice(s: Session) -> Case:
        r = _ingest(s, original)
        ok = not r.created and r.submission_id == submission_id
        return Case("Same email twice", "No change", "No change" if ok else "New submission", ok)

    def same_packet(s: Session) -> Case:
        r = _ingest(s, _new_id(original))
        ok = r.relation == "same_files" and r.related_to == submission_id and not r.process_id
        got = f"{r.relation or 'new submission'}, processed: {'yes' if r.process_id else 'no'}"
        return Case("Same packet in a new email", "Linked as same files, not processed", got, ok)

    def with_reference(s: Session) -> Case:
        r = _ingest(s, reply)
        how = s.get(Submission, r.submission_id)
        ok = r.relation == "follow_up" and r.process_id == submission_id
        got = f"{r.relation or 'new submission'} by {how.relation_how if how else '?'}"
        return Case("Reply with the reference", "Attached", got, ok)

    def by_dot(s: Session) -> Case:
        r = _ingest(s, _without_reference(reply))
        how = s.get(Submission, r.submission_id)
        ok = r.relation == "follow_up" and r.process_id == submission_id
        got = f"{r.relation or 'new submission'} by {how.relation_how if how else '?'}"
        return Case("Reply without the reference, same DOT", "Attached", got, ok)

    def by_name(s: Session) -> Case:
        r = _ingest(s, _name_only(reply, applicant))
        ok = r.relation == "possible_follow_up" and not r.process_id
        got = f"{r.relation or 'new submission'}, merged: {'yes' if r.process_id else 'no'}"
        return Case("Reply matching only by name", "Suggested, not merged", got, ok)

    cases = [_on_copy(engine, f) for f in (twice, same_packet, with_reference, by_dot, by_name)]

    def resend_timing(s: Session) -> tuple[float, float, float, int]:
        for row in s.scalars(select(OcrCacheEntry)):
            s.delete(row)
        s.commit()
        off = settings.model_copy(update={"triage_reuse_pages": False, "triage_ocr_cache": False})
        ocr_only = off.model_copy(update={"triage_ocr_cache": True})
        times: list[float] = []
        # before: nothing reused; the OCR cache filling, then alone; then both, as in production
        for cfg in (ocr_only, ocr_only, settings):
            r = ingest_email(
                s,
                store,
                _new_id(original),
                source="eval",
                limits=IntakeLimits.from_settings(cfg),
                link_related=False,  # processed as a new submission, to time it
            )
            t = time.perf_counter()
            process_submission(s, store, cfg, r.submission_id, steps=TRIAGE, trigger="eval")
            times.append(time.perf_counter() - t)
        pages = len(s.scalars(select(OcrCacheEntry)).all())
        return times[0], times[1], times[2], pages

    with scratch_copy(engine) as copy, sessionmaker(copy, expire_on_commit=False)() as session:
        timing = resend_timing(session)
    return cases, timing


def markdown(cases: list[Case], timing: tuple[float, float, float, int]) -> str:
    lines = [
        "# Follow-up emails",
        "",
        "Each case ran against the sample submission on its own scratch copy of the database "
        "(`lane.evals.follow_ups`).",
        "",
        "| Case | Expected | Got | Pass |",
        "|---|---|---|---|",
        *(f"| {c.name} | {c.expected} | {c.got} | {'yes' if c.passed else 'NO'} |" for c in cases),
        "",
        f"{sum(c.passed for c in cases)} of {len(cases)} pass.",
        "",
        "## Resend time (triage, the whole packet as a new submission)",
        "",
        "| Reuse | Triage time |",
        "|---|---|",
        f"| None, every page read again (before) | {timing[0]:.1f} s |",
        f"| OCR cache only (file sha256, page, engine and version, settings) | {timing[1]:.1f} s |",
        f"| Pages of a file read before reused, by sha256 (after) | {timing[2]:.1f} s |",
        "",
        f"{timing[3]} scanned pages went through OCR once. OCR is a minority of triage time;"
        " rendering and preparing scans is most of it, so the whole reading is reused by file.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submission", required=True)
    parser.add_argument("--reply", type=Path, required=True)
    args = parser.parse_args(argv)
    cases, timing = run(args.submission, args.reply.read_bytes())
    text = markdown(cases, timing)
    REPORT.write_text(text)
    print(text)
    return 0 if all(c.passed for c in cases) else 1


if __name__ == "__main__":
    sys.exit(main())
