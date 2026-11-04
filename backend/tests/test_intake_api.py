from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from lane.api.main import create_app
from tests.factories import PDF, make_email


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def post(client: TestClient, body: bytes):
    return client.post(
        "/api/v1/intake/eml", content=body, headers={"Content-Type": "message/rfc822"}
    )


def test_upload_creates_then_returns_existing(client: TestClient) -> None:
    raw = make_email([("a.pdf", PDF)])
    first = post(client, raw)
    assert first.status_code == 201
    assert first.json()["created"] is True
    assert first.json()["unique_files"] == 2
    second = post(client, raw)
    assert second.status_code == 200
    assert second.json()["submission_id"] == first.json()["submission_id"]


def test_upload_rejects_non_email(client: TestClient) -> None:
    res = post(client, PDF)
    assert res.status_code == 422
    assert "not an email" in res.json()["detail"]


def test_upload_requires_a_body(client: TestClient) -> None:
    assert post(client, b"").status_code == 400
