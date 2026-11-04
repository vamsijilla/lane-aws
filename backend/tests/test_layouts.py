"""Known layouts are learned only when a person confirms a document, and keep where they came
from; the next document laid out the same way is typed with no model call."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.api.main import create_app
from lane.config import get_settings
from lane.db.models import AuditEvent, Document, LearnedLayout, Page, PageClassification
from lane.pipeline.registry import PIPELINE
from lane.pipeline.runner import process_submission
from lane.storage.blobs import BlobStore
from tests.docs_factory import text_pdf
from tests.factories import make_email
from tests.llm_fakes import FakeTransport, factory, verdict
from tests.test_intake import ingest

STEPS = [s for s in PIPELINE if s.name in {"triage", "classify"}]
LINES = [
    "Carrier Statement of Claims",
    "Named Insured: Granite Ridge Freight",
    "Policy Number: ACME12345",
    "Policy Period: 01/01/2025 to 01/01/2026",
    "Valuation Date: 10/01/2026",
    "Claim Count: 0",
]


@pytest.fixture
def client(db: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("LANE_TEMPLATES_AUDIT_RATE", "0")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c


def process(db: Session, store: BlobStore, message_id: str) -> tuple[Document, FakeTransport]:
    raw = make_email([("statement.pdf", text_pdf(lines=LINES))], message_id=message_id)
    sid = ingest(db, store, raw).submission_id
    transport = FakeTransport(lambda _r, _t: verdict("loss_run", subject_id="ACME12345"))
    process_submission(
        db, store, get_settings(), sid, steps=STEPS, trigger="test", llm_factory=factory(transport)
    )
    doc = next(
        d
        for d in db.scalars(select(Document).where(Document.submission_id == sid))
        if d.doc_type != "email_body"
    )
    return doc, transport


def confirm(client: TestClient, document_id: str):
    token = client.post("/api/v1/auth/demo-login", json={"role": "underwriter"}).json()["token"]
    return client.post(
        f"/api/v1/documents/{document_id}/confirm", headers={"Authorization": f"Bearer {token}"}
    )


def test_a_confirmed_document_teaches_a_layout_with_where_it_came_from(
    client: TestClient, db: Session, store: BlobStore
) -> None:
    first, transport = process(db, store, "<a@agency.example>")
    assert transport.calls  # nothing learned yet: the model types it
    assert db.scalars(select(LearnedLayout)).all() == []

    r = confirm(client, first.id)
    assert r.status_code == 200 and r.json()["layouts"]
    row = db.scalars(select(LearnedLayout)).one()
    assert row.status == "active" and row.doc_type == "loss_run"
    assert row.source_document_id == first.id and row.source_submission_id == first.submission_id
    assert row.source_page_no == 1 and row.confirmed_by.endswith("underwriter")
    assert "Policy Number:" in [a["text"] for a in row.definition["anchors"]]
    actions = {e.action for e in db.scalars(select(AuditEvent))}
    assert {"layout.learned", "document.confirmed"} <= actions

    second, transport = process(db, store, "<b@agency.example>")
    pdf_calls = [c for c in transport.calls if c[0].subject_id != second.id]
    assert second.doc_type == "loss_run" and pdf_calls == []  # typed by the learned layout
    page = db.scalars(select(Page).where(Page.document_id == second.id)).one()
    signals = db.get(PageClassification, page.id).signals  # type: ignore[union-attr]
    assert signals["method"] == "layout" and signals["template"] == row.id


def test_viewers_cannot_teach_layouts(client: TestClient, db: Session, store: BlobStore) -> None:
    doc, _ = process(db, store, "<c@agency.example>")
    token = client.post("/api/v1/auth/demo-login", json={"role": "viewer"}).json()["token"]
    r = client.post(
        f"/api/v1/documents/{doc.id}/confirm", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403
    assert db.scalars(select(LearnedLayout)).all() == []
