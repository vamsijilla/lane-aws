"""A person can say what a document is; the label survives every classification run."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.api.main import create_app
from lane.config import get_settings
from lane.db.models import AuditEvent, Document, DocumentLabel
from lane.pipeline.registry import PIPELINE
from lane.pipeline.runner import process_submission
from lane.storage.blobs import BlobStore
from tests.docs_factory import text_pdf
from tests.factories import make_email
from tests.llm_fakes import FakeTransport, factory, verdict
from tests.test_intake import ingest

STEPS = [s for s in PIPELINE if s.name in {"triage", "classify"}]


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def unrecognised(db: Session, store: BlobStore) -> tuple[str, FakeTransport]:
    """A file the classifier cannot place."""
    sid = ingest(db, store, make_email([("scan0042.pdf", text_pdf())])).submission_id
    transport = FakeTransport(lambda _r, _t: verdict("other", confidence="low"))
    process_submission(
        db, store, get_settings(), sid, steps=STEPS, trigger="test", llm_factory=factory(transport)
    )
    return sid, transport


def the_pdf(db: Session, sid: str) -> Document:
    return next(
        d
        for d in db.scalars(select(Document).where(Document.submission_id == sid))
        if d.doc_type != "email_body"
    )


def test_a_label_wins_over_the_classifier_on_every_run(db: Session, store: BlobStore) -> None:
    sid, transport = unrecognised(db, store)
    doc = the_pdf(db, sid)
    assert doc.doc_type == "other"
    db.add(
        DocumentLabel(
            document_id=doc.id,
            submission_id=sid,
            doc_type="mvr",
            was="other",
            reason="It is the driver's MVR",
            actor="user:underwriter",
        )
    )
    db.commit()
    process_submission(
        db, store, get_settings(), sid, steps=STEPS, trigger="test", llm_factory=factory(transport)
    )
    db.expire_all()
    doc = the_pdf(db, sid)
    assert doc.doc_type == "mvr" and doc.confidence.value == "high" and not doc.needs_review
    assert doc.confidence_reasons == ["Labelled by underwriter: It is the driver's MVR"]


def test_label_endpoints(
    client: TestClient, db: Session, store: BlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[Any] = []
    monkeypatch.setattr("lane.pipeline.background.process", lambda *a: started.append(a))
    sid, _ = unrecognised(db, store)
    doc_id = the_pdf(db, sid).id
    url = f"/api/v1/documents/{doc_id}/label"
    assert client.post(url, json={"doc_type": "nonsense", "reason": "x"}).status_code == 422
    assert client.post(url, json={"doc_type": "mvr", "reason": " "}).status_code == 422
    assert client.post(url, json={"doc_type": "email_body", "reason": "x"}).status_code == 422
    viewer = client.post("/api/v1/auth/demo-login", json={"role": "viewer"}).json()["token"]
    denied = client.post(
        url,
        json={"doc_type": "mvr", "reason": "x"},
        headers={"Authorization": f"Bearer {viewer}"},
    )
    assert denied.status_code == 403

    assert client.post(url, json={"doc_type": "mvr", "reason": "Driver record"}).status_code == 202
    assert started == [(sid, "relabel")]  # processed again, so the document is read as an MVR
    listed = client.get(f"/api/v1/submissions/{sid}/documents").json()
    label = next(d for d in listed if d["id"] == doc_id)["label"]
    assert label["doc_type"] == "mvr" and label["was"] == "other"
    types = {t["value"] for t in client.get("/api/v1/document-types").json()}
    assert "mvr" in types and "email_body" not in types

    assert client.delete(url).status_code == 202
    assert client.delete(url).status_code == 404
    db.expire_all()
    actions = [e.action for e in db.scalars(select(AuditEvent))]
    assert "document.relabeled" in actions and "document.label_cleared" in actions
