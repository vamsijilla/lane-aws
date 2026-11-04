"""A file we could not read is not proof that something is absent: the missing list, the rules
and the agent request say "could not be read", never "missing"."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.config import get_settings
from lane.db.models import Attachment
from lane.documents.types import DocType
from lane.llm.gateway import LlmUnavailable
from lane.pipeline.registry import PIPELINE
from lane.pipeline.runner import process_submission
from lane.resolve.build import Builder
from lane.resolve.derive import finalize
from lane.resolve.missing import find_missing
from lane.resolve.unread import UnreadSource, unread_sources
from lane.rules.model import Outcome
from lane.storage.blobs import BlobStore
from tests.docs_factory import text_pdf
from tests.factories import make_email, ooxml
from tests.llm_fakes import FakeTransport, factory, verdict
from tests.test_intake import ingest
from tests.test_resolve import APPLICATION, TODAY, doc
from tests.test_rules import ViewBuilder, clean_submission, results_of

STEPS = {
    name: [s for s in PIPELINE if s.name == name] for name in ("triage", "classify", "extract")
}


def step(db: Session, store: BlobStore, sid: str, name: str, llm: Any = None) -> None:
    kw: dict[str, Any] = {"llm_factory": llm} if llm else {}
    process_submission(db, store, get_settings(), sid, steps=STEPS[name], trigger="test", **kw)


def down(_settings: Any) -> Any:
    raise LlmUnavailable("the model is down")


# --- where unread sources come from ---------------------------------------------------------


def test_unreadable_and_unsupported_files_are_remembered(db: Session, store: BlobStore) -> None:
    raw = make_email(
        [
            ("application.pdf", text_pdf()),
            ("broken.pdf", b"%PDF-1.7 truncated"),
            ("letter.docx", ooxml("word/document.xml")),
        ]
    )
    sid = ingest(db, store, raw).submission_id
    step(db, store, sid, "triage")
    errors = {
        a.filename: a.read_error
        for a in db.scalars(select(Attachment).where(Attachment.submission_id == sid))
    }
    assert errors["application.pdf"] is None
    assert errors["broken.pdf"] == "PDF is corrupt and cannot be opened"
    assert errors["letter.docx"] and "not read automatically" in errors["letter.docx"]
    found = {u.filename: u for u in unread_sources(db, sid)}
    assert set(found) == {"broken.pdf", "letter.docx"}
    assert found["broken.pdf"].doc_type is None  # an unopened file's type is unknown


def test_a_failed_extraction_makes_its_document_an_unread_source(
    db: Session, store: BlobStore
) -> None:
    sid = ingest(db, store, make_email([("losses.pdf", text_pdf())])).submission_id
    step(db, store, sid, "triage")
    classifier = FakeTransport(lambda _r, _t: verdict("loss_run", subject_name="Acme"))
    step(db, store, sid, "classify", factory(classifier))
    step(db, store, sid, "extract", down)
    found = {(u.filename, u.doc_type): u for u in unread_sources(db, sid)}
    losses = found[("losses.pdf", "loss_run")]
    assert "unavailable" in losses.reason
    assert ("Email body", "email_body") in found  # the model read nothing, the body included


# --- the missing list ------------------------------------------------------------------------


def board() -> Any:
    b = Builder([doc("app", "application", "Application")], {"app": APPLICATION}, {}, today=TODAY)
    out = b.build()
    finalize(out, today=TODAY)
    return out


def test_missing_items_do_not_claim_absence_when_a_file_could_not_be_read() -> None:
    plain = {m.code: m for m in find_missing(board())}
    assert plain["APP_FEIN"].detail == "The application has no FEIN or SSN."  # nothing unread

    unread = [UnreadSource("scan0042.pdf", None, "PDF is corrupt and cannot be opened")]
    items = {m.code: m for m in find_missing(board(), unread)}
    assert "scan0042.pdf could not be read and may hold it" in items["APP_FEIN"].detail
    ask = items["UNREADABLE:scan0042.pdf"]
    assert ask.category == "documents" and "PDF is corrupt" in ask.detail
    assert "readable copy" in ask.detail


def test_an_unread_source_only_affects_what_it_could_hold() -> None:
    unread = [UnreadSource("MVRs.pdf", "mvr", "extraction failed")]
    items = {m.code: m for m in find_missing(board(), unread)}
    assert "could not be read" not in items["APP_FEIN"].detail  # an MVR cannot hold a FEIN
    assert items["UNREADABLE:MVRs.pdf"].category == "documents"


# --- the rules -------------------------------------------------------------------------------


def without_fein(unread: list[UnreadSource]) -> Any:
    b = ViewBuilder()
    clean_submission(b)
    applicant = b.view.one("applicant")
    assert applicant is not None
    applicant._fields.pop("fein", None)  # pyright: ignore[reportPrivateUsage]
    b.view.unread = unread
    return results_of(b.view)[0]["APP-01"]


def test_a_missing_fein_refers_when_everything_was_read() -> None:
    r = without_fein([])
    assert r.outcome is Outcome.REFER and r.verdict.reason == "No FEIN or SSN on the application"


def test_a_missing_fein_is_unknown_when_the_application_could_not_be_read() -> None:
    r = without_fein([UnreadSource("application.xlsx", "application", "extraction failed")])
    assert r.outcome is Outcome.UNKNOWN
    assert "application.xlsx could not be read and may hold it" in r.verdict.reason


def test_an_unopened_file_of_unknown_type_could_hold_anything() -> None:
    r = without_fein([UnreadSource("scan0042.pdf", None, "corrupt")])
    assert r.outcome is Outcome.UNKNOWN


def test_an_unrelated_unread_source_changes_nothing() -> None:
    r = without_fein([UnreadSource("losses.pdf", DocType.LOSS_RUN.value, "failed")])
    assert r.outcome is Outcome.REFER


def test_every_declared_source_is_a_real_document_type() -> None:
    from lane.rules.service import ruleset

    types = {t.value for t in DocType}
    declared = {r.id: r.sources for r in ruleset().rules if r.sources}
    assert {"APP-01", "APP-02", "DOC-01", "DOC-02", "DOC-03", "DOC-04", "DOC-05"} <= set(declared)
    assert all(set(s) <= types for s in declared.values())
