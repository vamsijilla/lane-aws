"""Deleting a submission leaves nothing of it behind, keeps the audit chain valid, and a restart
ingests the original email again."""

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from lane.audit import chain
from lane.config import get_settings
from lane.db.base import Base
from lane.db.models import AuditEvent, EmailMessage, LlmCall, Run, Submission
from lane.intake.remove import delete_and_reingest, delete_submission
from lane.intake.service import IntakeLimits
from lane.pipeline.registry import PIPELINE
from lane.pipeline.runner import process_submission
from lane.storage.blobs import BlobStore
from tests.docs_factory import text_pdf
from tests.factories import make_email
from tests.llm_fakes import FakeTransport, factory, verdict
from tests.test_intake import ingest


def processed(db: Session, store: BlobStore) -> str:
    raw = make_email([("application.pdf", text_pdf()), ("losses.pdf", text_pdf())])
    sid = ingest(db, store, raw).submission_id
    classifier = FakeTransport(lambda _r, _t: verdict("loss_run", subject_name="Acme"))
    steps = [s for s in PIPELINE if s.name in ("triage", "classify")]
    process_submission(
        db, store, get_settings(), sid, steps=steps, trigger="test", llm_factory=factory(classifier)
    )
    return sid


def count(db: Session, table: str) -> int:
    return int(db.scalar(select(func.count()).select_from(Base.metadata.tables[table])) or 0)


def test_delete_removes_everything_derived_and_keeps_the_audit_chain(
    db: Session, store: BlobStore
) -> None:
    sid = processed(db, store)
    assert count(db, "pages") and count(db, "runs") and count(db, "attachments")
    calls = count(db, "llm_calls")

    removed = delete_submission(db, sid, actor="user:underwriter")

    assert removed is not None and removed.files >= 2
    for table in Base.metadata.sorted_tables:
        if table.name in (
            "audit_events",
            "blobs",
            "llm_calls",
            "llm_cache",
            "page_cache",
            "ocr_cache",
        ):
            continue  # kept on purpose: the same file or request is not read again
        assert count(db, table.name) == 0, table.name
    assert count(db, "llm_calls") == calls  # the cache stays useful
    assert (
        db.scalar(select(func.count()).select_from(LlmCall).where(LlmCall.run_id.is_not(None))) == 0
    )
    if db.get_bind().dialect.name == "sqlite":  # Postgres refuses a dangling reference outright
        assert db.execute(text("PRAGMA foreign_key_check")).all() == []
    last = db.scalars(select(AuditEvent).order_by(AuditEvent.seq.desc())).first()
    assert last is not None and last.action == "submission.deleted" and last.subject_id == sid
    assert chain.verify(db).ok


def test_deleting_a_missing_submission_is_none(db: Session) -> None:
    assert delete_submission(db, "nope", actor="user:underwriter") is None


def test_restart_ingests_the_original_email_again(db: Session, store: BlobStore) -> None:
    sid = processed(db, store)
    result = delete_and_reingest(
        db,
        store,
        sid,
        actor="user:underwriter",
        limits=IntakeLimits.from_settings(get_settings()),
    )
    assert result is not None and result.created and result.submission_id != sid
    assert db.get(Submission, sid) is None
    assert db.scalar(select(func.count()).select_from(EmailMessage)) == 1
    assert db.scalar(select(func.count()).select_from(Run)) == 0  # not processed until asked
    assert chain.verify(db).ok
