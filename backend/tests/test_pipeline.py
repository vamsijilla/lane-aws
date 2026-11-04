import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lane.audit import chain
from lane.config import get_settings
from lane.db.models import (
    AuditEvent,
    Page,
    PageKind,
    RunStatus,
    StepStatus,
    Submission,
    SubmissionStatus,
)
from lane.pipeline.registry import PIPELINE
from lane.pipeline.runner import (
    StepContext,
    StepDef,
    StepOutcome,
    TransientStepError,
    process_submission,
)
from lane.storage.blobs import BlobStore
from tests.docs_factory import image_pdf, text_image, text_pdf, workbook
from tests.factories import make_email, ooxml
from tests.test_intake import ingest

TRIAGE = [s for s in PIPELINE if s.name == "triage"]


def run(db: Session, store: BlobStore, submission_id: str, steps: list[StepDef] = PIPELINE):
    return process_submission(db, store, get_settings(), submission_id, steps=steps, trigger="test")


def test_triage_builds_pages_and_reports_unreadable_files(db: Session, store: BlobStore) -> None:
    raw = make_email(
        [
            ("application.pdf", text_pdf(pages=2)),
            ("ifta.pdf", image_pdf(text_image())),
            ("drivers.xlsx", workbook()),
            ("broken.pdf", b"%PDF-1.7 truncated"),
            ("letter.docx", ooxml("word/document.xml")),
        ]
    )
    sub_id = ingest(db, store, raw).submission_id

    result = run(db, store, sub_id, TRIAGE)

    assert result.status is RunStatus.DEGRADED
    [step] = result.steps
    assert step.status is StepStatus.DEGRADED
    assert step.summary["kinds"] == {"email_body": 1, "text": 2, "scan": 1, "sheet": 2}
    assert [f["filename"] for f in step.summary["unreadable"]] == ["broken.pdf"]
    assert step.summary["unreadable"][0]["reason"] == "PDF is corrupt and cannot be opened"
    assert step.summary["unsupported"] == ["letter.docx"]
    assert db.get(Submission, sub_id).status is SubmissionStatus.READY  # type: ignore[union-attr]

    pages = db.scalars(select(Page).where(Page.submission_id == sub_id)).all()
    scan = next(p for p in pages if p.kind is PageKind.SCAN)
    assert scan.render_sha256 and scan.thumb_sha256 and scan.layout_sha256 is None
    assert chain.verify(db).ok


def test_rerun_reuses_existing_pages(db: Session, store: BlobStore) -> None:
    sub_id = ingest(db, store, make_email([("a.pdf", text_pdf())])).submission_id
    run(db, store, sub_id, TRIAGE)
    before = db.scalar(select(func.count()).select_from(Page))

    second = run(db, store, sub_id, TRIAGE)

    assert second.steps[0].summary["reused_attachments"] == 2  # body + a.pdf
    assert db.scalar(select(func.count()).select_from(Page)) == before


def _boom(ctx: StepContext) -> StepOutcome:
    raise ValueError("model returned nonsense")


def test_a_failed_optional_step_degrades_the_run_and_later_steps_still_run(
    db: Session, store: BlobStore
) -> None:
    sub_id = ingest(db, store, make_email()).submission_id
    ran: list[str] = []

    def after(ctx: StepContext) -> StepOutcome:
        ran.append("after")
        return StepOutcome()

    result = run(
        db,
        store,
        sub_id,
        [StepDef("analyze", "1", _boom, required=False), StepDef("after", "1", after)],
    )
    assert result.status is RunStatus.DEGRADED
    assert result.steps[0].status is StepStatus.FAILED
    assert result.steps[0].error == "ValueError: model returned nonsense"
    assert ran == ["after"]


def test_a_failed_required_step_stops_the_run(db: Session, store: BlobStore) -> None:
    sub_id = ingest(db, store, make_email()).submission_id
    result = run(db, store, sub_id, [StepDef("triage", "1", _boom), StepDef("never", "1", _boom)])
    assert result.status is RunStatus.FAILED
    assert [s.name for s in result.steps] == ["triage"]
    assert db.get(Submission, sub_id).status is SubmissionStatus.FAILED  # type: ignore[union-attr]
    assert db.scalar(select(func.count()).where(AuditEvent.action == "step.failed")) == 1


def test_transient_errors_are_retried(
    db: Session, store: BlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("lane.pipeline.runner.time.sleep", lambda _s: None)
    sub_id = ingest(db, store, make_email()).submission_id
    calls: list[int] = []

    def flaky(ctx: StepContext) -> StepOutcome:
        calls.append(1)
        if len(calls) < 3:
            raise TransientStepError("rate limited")
        return StepOutcome(summary={"ok": True})

    result = run(db, store, sub_id, [StepDef("extract", "1", flaky, max_attempts=3)])
    assert result.status is RunStatus.SUCCEEDED
    assert result.steps[0].attempt == 3 and result.steps[0].summary == {"ok": True}


def test_unknown_submission_raises(db: Session, store: BlobStore) -> None:
    with pytest.raises(LookupError):
        run(db, store, "missing")
