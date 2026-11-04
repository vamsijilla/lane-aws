from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.api.main import create_app
from lane.audit import chain
from lane.config import get_settings
from lane.db.models import Document, Page, PageClassification, RunStatus, StepStatus
from lane.llm.gateway import LlmRequest
from lane.pipeline.registry import PIPELINE
from lane.pipeline.runner import process_submission
from lane.storage.blobs import BlobStore
from tests.docs_factory import IFTA_LINES, image_pdf, text_image, text_pdf, workbook
from tests.factories import make_email
from tests.llm_fakes import FakeTransport, factory, verdict
from tests.test_intake import ingest

UP_TO_CLASSIFY = [step for step in PIPELINE if step.name in {"triage", "classify"}]


def scripted(request: LlmRequest[Any], text: str) -> dict[str, Any]:
    """Pretend model: reads the page position and filename that the request carries."""
    if "worksheet" in text:
        return verdict("driver_schedule", confidence="medium")
    if "How the page is stored: scan" in text:
        return verdict("ifta_return", subject_id="ACCT-1", period="Q1", document_date="2026-03-31")
    page_two = "page 2 of 2" in text
    return verdict(
        "loss_history_letter",
        starts_new_document=not page_two,
        subject_id="POL-9",
        document_date="2026-10-06",
    )


def run(db: Session, store: BlobStore, raw: bytes, transport: FakeTransport) -> Any:
    sub_id = ingest(db, store, raw).submission_id
    result = process_submission(
        db,
        store,
        get_settings(),
        sub_id,
        steps=UP_TO_CLASSIFY,
        trigger="test",
        llm_factory=factory(transport),
    )
    return sub_id, result


def docs_of(db: Session, sub_id: str) -> list[Document]:
    return list(
        db.scalars(
            select(Document).where(Document.submission_id == sub_id).order_by(Document.ordinal)
        )
    )


def test_pages_become_logical_documents_with_content_types(db: Session, store: BlobStore) -> None:
    raw = make_email(
        [
            ("Loss runs 2023.pdf", text_pdf(pages=2)),  # named a loss run, reads as a letter
            ("Q1 2026.pdf", image_pdf(text_image())),
            ("Drivers.xlsx", workbook()),
        ]
    )
    sub_id, result = run(db, store, raw, FakeTransport(scripted))

    assert result.status is RunStatus.SUCCEEDED
    classify = result.steps[1]
    assert classify.status is StepStatus.SUCCEEDED
    docs = docs_of(db, sub_id)
    assert [(d.doc_type, d.page_from, d.page_to) for d in docs] == [
        ("email_body", 1, 1),
        ("loss_history_letter", 1, 2),
        ("ifta_return", 1, 1),
        # The hidden lookup tab is no document (tier 1); before, it was counted as page 2
        ("driver_schedule", 1, 1),
    ]
    letter = docs[1]
    # Named as a loss run, reads as a loss history letter: one family, a different format
    assert not letter.filename_mismatch and letter.filename_hint == "loss_run"
    assert "Different format: letter, not a loss run" in letter.confidence_reasons
    assert not docs[2].filename_mismatch
    pages = db.scalars(select(Page).where(Page.submission_id == sub_id)).all()
    lookup = [p for p in pages if p.sheet_name == "Lookups"]
    assert len(lookup) == 1 and lookup[0].document_id is None  # lookup values, no document
    assert all(p.document_id for p in pages if p not in lookup)
    assert db.get(PageClassification, pages[1].id) is not None
    # 2 pdf pages + 1 scan + the drivers sheet; the lookup sheet needs no model call (tier 1)
    assert classify.summary["llm"]["calls"] == 4
    assert chain.verify(db).ok


def test_rerun_reuses_cached_answers_and_keeps_document_ids(db: Session, store: BlobStore) -> None:
    transport = FakeTransport(scripted)
    sub_id, _ = run(db, store, make_email([("a.pdf", text_pdf())]), transport)
    first_ids = [d.id for d in docs_of(db, sub_id)]
    calls = len(transport.calls)

    again = process_submission(
        db,
        store,
        get_settings(),
        sub_id,
        steps=UP_TO_CLASSIFY,
        trigger="rerun",
        llm_factory=factory(transport),
    )
    assert again.steps[1].summary["llm"]["cache_hits"] == 1
    assert len(transport.calls) == calls
    assert [d.id for d in docs_of(db, sub_id)] == first_ids


def test_without_a_model_text_pages_fall_back_to_keywords(db: Session, store: BlobStore) -> None:
    raw = make_email([("x.pdf", text_pdf(lines=IFTA_LINES)), ("scan.pdf", image_pdf(text_image()))])
    sub_id = ingest(db, store, raw).submission_id
    result = process_submission(
        db, store, get_settings(), sub_id, steps=UP_TO_CLASSIFY, trigger="test"
    )
    assert result.steps[1].status is StepStatus.DEGRADED
    assert result.steps[1].summary["llm"]["unavailable"]
    docs = docs_of(db, sub_id)
    text_doc, scan_doc = docs[1], docs[2]
    assert text_doc.doc_type == "ifta_return"
    assert scan_doc.doc_type == "other" and scan_doc.needs_review
    # Strong key phrases type the text page without the model, at medium confidence (nothing
    # cross-checks them); the scan has no text and stays low
    assert text_doc.confidence.value == "medium"
    assert scan_doc.confidence.value == "low"


def test_disagreement_between_tier1_and_the_model_is_sent_to_review(
    db: Session, store: BlobStore
) -> None:
    transport = FakeTransport(lambda r, t: verdict("mvr"))  # text says IFTA, model says MVR
    sub_id, _ = run(db, store, make_email([("x.pdf", text_pdf(lines=IFTA_LINES))]), transport)
    doc = docs_of(db, sub_id)[1]
    assert doc.doc_type == "ifta_return"  # strong key phrases decide; the model is a check
    assert doc.confidence.value == "low" and doc.needs_review
    assert "Tier 1 signals say ifta return but AI reads motor vehicle record" in doc.review_reasons


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def test_documents_endpoint(client: TestClient, db: Session, store: BlobStore) -> None:
    sub_id, _ = run(
        db, store, make_email([("Loss runs.pdf", text_pdf(pages=2))]), FakeTransport(scripted)
    )
    body = client.get(f"/api/v1/submissions/{sub_id}/documents").json()
    letter = body[1]
    assert letter["doc_type_label"] == "Loss history letter"
    assert len(letter["page_ids"]) == 2
    # A loss history letter in a file named "Loss runs" is the record, in another format
    assert letter["filename_mismatch"] is False
    assert letter["format_note"] == "Different format: letter, not a loss run"
    assert "subject_id" not in letter  # identifiers are not exposed
    assert client.get("/api/v1/submissions/nope/documents").status_code == 404


def test_submissions_list_for_the_inbox(client: TestClient, db: Session, store: BlobStore) -> None:
    assert client.get("/api/v1/submissions").json() == []
    raw = make_email([("Loss runs.pdf", text_pdf(pages=2)), ("Loss runs copy.pdf", text_pdf())])
    sub_id, _ = run(db, store, raw, FakeTransport(scripted))
    [item] = client.get("/api/v1/submissions").json()
    assert item["id"] == sub_id
    assert item["sender_name"] == "Agent" and item["sender_email"] == "agent@agency.example"
    assert item["received_at"].endswith("Z")
    assert (item["files"], item["documents"]) == (
        2,
        3,
    )  # two attachments; the body is its own document
    assert item["filename_mismatches"] == 0  # letters named "loss runs": a format, not a misname
    assert item["status"] == "ready" and item["latest_run"]["status"] == "succeeded"
