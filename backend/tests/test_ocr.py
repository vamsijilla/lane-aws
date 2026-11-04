"""The real OCR engine, end to end: word boxes, keyword signals on scans, and the dual read."""

import json
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.config import get_settings
from lane.db.models import Blob, Document, DocumentExtraction, Fact, Page, PageKind
from lane.documents import ocr
from lane.documents.triage import TriageConfig, triage
from lane.extract import schemas as s
from lane.intake.sniff import FileKind
from lane.llm.gateway import LlmRequest
from lane.pipeline.registry import PIPELINE
from lane.pipeline.runner import process_submission
from lane.storage.blobs import BlobStore
from tests.docs_factory import IFTA_LINES, image_pdf, text_image
from tests.factories import make_email
from tests.llm_fakes import FakeTransport, factory, verdict
from tests.test_extract_step import blank, val
from tests.test_intake import ingest


@pytest.fixture(autouse=True)
def ocr_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANE_TRIAGE_OCR", "true")
    # v1's OCR dual read (ocr_exact / ocr_fuzzy); v2's read at the anchor is in test_verify.py
    # and test_two_reads.py
    monkeypatch.setenv("EXTRACT_V2", "0")
    get_settings.cache_clear()


def ifta_scan() -> bytes:
    return image_pdf(text_image(lines=IFTA_LINES))


def test_ocr_reads_words_with_boxes() -> None:
    words = ocr.read_words(text_image(lines=IFTA_LINES))
    texts = [w.text for w in words]
    assert "Jurisdiction" in texts and "Nevada" in texts
    nevada = next(w for w in words if w.text == "Nevada")
    assert nevada.x1 > nevada.x0 and nevada.y1 > nevada.y0 and 0 < nevada.score <= 1


def test_triage_gives_scans_word_boxes_in_page_points() -> None:
    [page] = triage(FileKind.PDF, ifta_scan(), TriageConfig.from_settings(get_settings()))
    assert page.kind is PageKind.SCAN and page.text_source == "ocr"
    assert page.ocr_confidence and page.ocr_confidence > 0.8
    layout = json.loads(page.layout_json or b"{}")
    assert layout["source"] == "ocr" and layout["unit"] == "pt"
    assert all(0 <= w[0] < w[2] <= 612.5 and 0 <= w[1] < w[3] <= 792.5 for w in layout["words"])


def scripted(request: LlmRequest[Any], text: str) -> dict[str, Any]:
    if request.purpose == "classify_page":
        return verdict("ifta_return", subject_id="A-1", period="Q1", document_date="2026-03-31")
    if request.purpose == "extract_ifta_return":
        # The model "reads" the image: one value OCR can confirm, one it cannot.
        out = blank(s.IftaExtraction)
        out["account_number"] = val("Nevada", "Nevada")
        out["taxpayer_name"] = val("NOT ON THE PAGE", "NOT ON THE PAGE")
        out["totals"] = blank(s.IftaTotals)
        return out
    if request.purpose == "extract_email_body":
        return blank(s.EmailExtraction)
    raise AssertionError(request.purpose)


def test_dual_read_confirms_image_values_with_ocr(db: Session, store: BlobStore) -> None:
    sub_id = ingest(db, store, make_email([("q1.pdf", ifta_scan())])).submission_id
    transport = FakeTransport(scripted)
    process_submission(
        db,
        store,
        get_settings(),
        sub_id,
        steps=PIPELINE,
        trigger="test",
        llm_factory=factory(transport),
    )
    # The extraction request carries the image only, so OCR stays an independent reading.
    extract_req = next(c for c in transport.calls if c[0].purpose == "extract_ifta_return")
    assert not any("Nevada" in b.get("text", "") for b in extract_req[1] if b["type"] == "text")

    doc = db.scalars(select(Document).where(Document.doc_type == "ifta_return")).one()
    facts = {f.path: f for f in db.scalars(select(Fact).where(Fact.document_id == doc.id))}
    confirmed, unconfirmed = facts["account_number"], facts["taxpayer_name"]
    # Confirmed by OCR (exact, or fuzzy when OCR merges neighbouring words), with a box.
    assert confirmed.grounding in {"ocr_exact", "ocr_fuzzy"} and confirmed.bbox
    assert confirmed.confidence.value in {"high", "medium"}
    assert unconfirmed.grounding == "image" and unconfirmed.bbox is None
    assert unconfirmed.confidence.value == "medium"
    assert "OCR could not confirm" in " ".join(unconfirmed.confidence_reasons)
    # Classification of the scan now has a keyword signal from the OCR text.
    assert "AI reading agrees" in doc.confidence_reasons  # tier 1 key phrases, model agrees
    assert db.get(DocumentExtraction, doc.id) is not None


def test_without_a_model_scans_are_typed_from_ocr_keywords(db: Session, store: BlobStore) -> None:
    sub_id = ingest(db, store, make_email([("q1.pdf", ifta_scan())])).submission_id
    process_submission(db, store, get_settings(), sub_id, steps=PIPELINE[:2], trigger="test")
    doc = db.scalars(select(Document).where(Document.doc_type != "email_body")).one()
    assert doc.doc_type == "ifta_return"
    page = db.scalars(select(Page).where(Page.document_id == doc.id)).one()
    assert page.text_source == "ocr" and db.get(Blob, page.layout_sha256 or "") is not None
