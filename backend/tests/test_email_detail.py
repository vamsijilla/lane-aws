"""An email of the submission opens as it arrived: who sent it to whom, when, its words and its
files, each tied to what Lane made of it. Read from the stored message, never retyped."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from lane.api.main import create_app
from lane.db.models import Submission
from lane.storage.blobs import BlobStore
from tests.factories import PDF, make_email
from tests.test_intake import ingest


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def _auth(client: TestClient, role: str = "underwriter") -> dict[str, str]:
    token = client.post("/api/v1/auth/demo-login", json={"role": role}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_the_original_email_opens_with_its_body_and_files(
    client: TestClient, db: Session, store: BlobStore
) -> None:
    raw = make_email([("a.pdf", PDF), ("again.pdf", PDF)], body="Please quote this account.")
    sid = ingest(db, store, raw).submission_id
    sub = db.get(Submission, sid)
    assert sub is not None
    r = client.get(f"/api/v1/submissions/{sid}/emails/{sub.email.id}", headers=_auth(client))
    assert r.status_code == 200
    e = r.json()
    assert e["role"] == "original" and e["sender"] == "agent@agency.example"
    assert e["to"] == ["submissions@carrier.example"]
    assert "Please quote this account." in e["body"]
    files = {a["filename"]: a for a in e["attachments"]}
    assert set(files) == {"a.pdf", "again.pdf"}
    # The second copy of the same file is marked as a duplicate of the first
    assert files["again.pdf"]["duplicate_of"] == "a.pdf"
    assert files["a.pdf"]["duplicate_of"] is None and files["a.pdf"]["attachment_id"]


def test_an_email_from_another_submission_is_not_found(
    client: TestClient, db: Session, store: BlobStore
) -> None:
    one = ingest(db, store, make_email([("a.pdf", PDF)], message_id="<a@x>")).submission_id
    two = ingest(db, store, make_email([("b.pdf", PDF + b" ")], message_id="<b@x>")).submission_id
    other = db.get(Submission, two)
    assert other is not None
    r = client.get(f"/api/v1/submissions/{one}/emails/{other.email.id}", headers=_auth(client))
    assert r.status_code == 404


def test_operations_cannot_read_an_email(client: TestClient, db: Session, store: BlobStore) -> None:
    sid = ingest(db, store, make_email([("a.pdf", PDF)])).submission_id
    sub = db.get(Submission, sid)
    assert sub is not None
    r = client.get(f"/api/v1/submissions/{sid}/emails/{sub.email.id}", headers=_auth(client, "ops"))
    assert r.status_code == 403


def test_driver_identifiers_in_the_body_are_masked(
    client: TestClient, db: Session, store: BlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = make_email([("a.pdf", PDF)], body="Licence D1234567 for the new driver.")
    sid = ingest(db, store, raw).submission_id
    sub = db.get(Submission, sid)
    assert sub is not None
    monkeypatch.setattr("lane.api.overview.pii_texts", lambda *_: {"D1234567": "[licence 567]"})
    r = client.get(f"/api/v1/submissions/{sid}/emails/{sub.email.id}", headers=_auth(client))
    assert "D1234567" not in r.json()["body"] and "[licence 567]" in r.json()["body"]
