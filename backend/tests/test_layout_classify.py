"""Known layouts (tier 2), learned from confirmed documents, type pages before the model: no call
when the layout settles it, the model when it cannot, and a sampled audit that disables a layout
the model disagrees with."""

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.config import get_settings
from lane.db.models import Document, LearnedLayout, Page, PageClassification
from lane.pipeline.registry import PIPELINE
from lane.pipeline.runner import process_submission
from lane.storage.blobs import BlobStore
from tests.docs_factory import text_pdf
from tests.factories import make_email
from tests.llm_fakes import FakeTransport, factory, verdict
from tests.test_intake import ingest

STEPS = [s for s in PIPELINE if s.name in {"triage", "classify"}]
LINES = [
    "Policy Number: ACME12345",
    "Policy Period: 01/01/2025 to 01/01/2026",
    "Run Date: 10/01/2026",
    "TOTAL CLAIMS: 0",
    "LATE REPORTED CLAIMS: 0",
]
TEMPLATE: dict[str, Any] = {
    "id": "test-loss-run",
    "name": "Test carrier loss run",
    "doc_type": "loss_run",
    "status": "active",
    "page": "first",
    "anchors": [
        {"text": t} for t in ("Policy Number:", "Policy Period:", "Run Date:", "TOTAL CLAIMS:")
    ],
    "fields": {
        "subject_id": [r"Policy Number:\s*(?P<value>[A-Z0-9]{5,})"],
        "period": [r"Policy Period:\s*(?P<value>\S+ to \S+)"],
        "document_date": [r"Run Date:\s*(?P<value>\S+)"],
    },
    "required": ["subject_id", "period"],
    "title": "Loss run, {period|years}",
}


def use(
    db: Session, monkeypatch: pytest.MonkeyPatch, template: dict[str, Any], audit: float = 0
) -> None:
    """A layout as if a person had confirmed a document of this kind."""
    db.add(
        LearnedLayout(
            id=template["id"],
            name=template["name"],
            doc_type=template["doc_type"],
            status="active",
            definition=template,
            source_document_id="d" * 32,
            source_submission_id="s" * 32,
            source_page_no=1,
            confirmed_by="user:test",
        )
    )
    db.commit()
    monkeypatch.setenv("LANE_TEMPLATES_AUDIT_RATE", str(audit))
    get_settings.cache_clear()


def run(
    db: Session, store: BlobStore, model_says: str = "loss_run"
) -> tuple[Document, PageClassification, FakeTransport]:
    sid = ingest(db, store, make_email([("scan0042.pdf", text_pdf(lines=LINES))])).submission_id
    transport = FakeTransport(
        lambda _r, _t: verdict(model_says, subject_id="FROM-AI", period="AI period")
    )
    process_submission(
        db, store, get_settings(), sid, steps=STEPS, trigger="test", llm_factory=factory(transport)
    )
    doc = next(
        d
        for d in db.scalars(select(Document).where(Document.submission_id == sid))
        if d.doc_type != "email_body"
    )
    pc = db.scalars(
        select(PageClassification)
        .join(Page, Page.id == PageClassification.page_id)
        .where(Page.document_id == doc.id)
    ).one()
    return doc, pc, transport


def test_a_known_layout_is_typed_without_a_model_call(
    db: Session, store: BlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(db, monkeypatch, TEMPLATE)
    doc, pc, transport = run(db, store)
    pdf_calls = [c for c in transport.calls if c[0].subject_id != doc.id]
    assert doc.doc_type == "loss_run" and doc.title == "Loss run, 2025-2026"
    assert doc.subject_id == "ACME12345" and doc.confidence.value == "high"
    assert pc.signals["method"] == "layout" and pc.signals["template"] == "test-loss-run"
    assert pc.signals["hits"] == 4 and pc.signals["audit"] is None
    assert len(pdf_calls) == 0  # the page never went to the model
    assert any("Matches the known layout" in r for r in doc.confidence_reasons)


def test_an_audited_page_also_goes_to_the_model_and_a_disagreement_is_reviewed(
    db: Session, store: BlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(db, monkeypatch, TEMPLATE, audit=1.0)
    doc, pc, transport = run(db, store, model_says="mvr")
    assert transport.calls  # sampled for audit
    assert pc.signals["audit"] == {"model_type": "mvr", "agrees": False}
    assert doc.doc_type == "loss_run" and doc.needs_review  # the layout stands, a person checks
    assert any("AI reads motor vehicle record" in r for r in doc.review_reasons)
    layout = db.get(LearnedLayout, "test-loss-run")
    assert layout is not None and layout.status == "disabled"  # no longer used
    assert (
        layout.audits == 1
        and layout.agreements == 0
        and "motor vehicle record" in (layout.disabled_reason or "")
    )


def test_a_reading_the_layout_is_known_to_get_is_noted_not_reviewed(
    db: Session, store: BlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    use(db, monkeypatch, {**TEMPLATE, "also_reads_as": ["mvr"]}, audit=1.0)
    doc, _, _ = run(db, store, model_says="mvr")
    assert doc.doc_type == "loss_run" and not doc.needs_review
    assert any("which this layout is known to read as" in r for r in doc.confidence_reasons)


def test_a_field_the_layout_cannot_read_is_read_by_the_model(
    db: Session, store: BlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    strict = {
        **TEMPLATE,
        "fields": {**TEMPLATE["fields"], "carrier": [r"Carrier:\s*(?P<value>.+)$"]},
    }
    strict["required"] = ["subject_id", "period", "carrier"]
    use(db, monkeypatch, strict)
    doc, pc, transport = run(db, store)
    assert transport.calls and pc.signals["missing"] == ["carrier"]
    assert doc.doc_type == "loss_run" and doc.subject_id == "ACME12345"  # layout fields kept
    assert any("AI read what the layout could not: carrier" in r for r in doc.confidence_reasons)
