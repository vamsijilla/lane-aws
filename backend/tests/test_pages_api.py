from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from lane.api.main import create_app
from tests.docs_factory import image_pdf, text_image, text_pdf, workbook
from tests.factories import make_email


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def submission_id(client: TestClient) -> str:
    raw = make_email(
        [("app.pdf", text_pdf()), ("scan.pdf", image_pdf(text_image())), ("d.xlsx", workbook())]
    )
    res = client.post("/api/v1/intake/eml", content=raw)
    assert res.status_code == 201
    return res.json()["submission_id"]  # TestClient runs the background pipeline before returning


def test_upload_triggers_processing_and_pages_are_listed(
    client: TestClient, submission_id: str
) -> None:
    pages = client.get(f"/api/v1/submissions/{submission_id}/pages").json()
    assert [(p["filename"], p["kind"]) for p in pages] == [
        ("Email body", "email_body"),
        ("app.pdf", "text"),
        ("scan.pdf", "scan"),
        ("d.xlsx", "sheet"),
        ("d.xlsx", "sheet"),
    ]
    runs = client.get(f"/api/v1/submissions/{submission_id}/runs").json()
    assert runs[0]["trigger"] == "intake"
    # No model in tests: triage succeeds, classification degrades to keywords instead of failing.
    assert runs[0]["status"] == "degraded"
    assert [(st["name"], st["status"]) for st in runs[0]["steps"]] == [
        ("triage", "succeeded"),
        ("classify", "degraded"),
        ("extract", "degraded"),  # no model: documents are marked failed, never guessed
        ("resolve", "succeeded"),
        ("rules", "succeeded"),
        ("analyze", "degraded"),  # no model: findings unavailable, everything else completes
        ("actions", "succeeded"),
    ]


def test_page_images_are_cacheable(client: TestClient, submission_id: str) -> None:
    scan = next(
        p
        for p in client.get(f"/api/v1/submissions/{submission_id}/pages").json()
        if p["kind"] == "scan"
    )
    full = client.get(f"/api/v1/pages/{scan['id']}/image")
    assert full.status_code == 200 and full.headers["content-type"] == "image/png"
    assert "immutable" in full.headers["cache-control"]
    thumb = client.get(f"/api/v1/pages/{scan['id']}/image", params={"variant": "thumb"})
    assert len(thumb.content) < len(full.content)
    again = client.get(
        f"/api/v1/pages/{scan['id']}/image", headers={"If-None-Match": full.headers["etag"]}
    )
    assert again.status_code == 304


def test_layouts_cover_words_cells_and_email_text(client: TestClient, submission_id: str) -> None:
    pages = {p["kind"]: p for p in client.get(f"/api/v1/submissions/{submission_id}/pages").json()}
    words = client.get(f"/api/v1/pages/{pages['text']['id']}/layout").json()
    assert words["unit"] == "pt" and words["words"]
    sheet = client.get(f"/api/v1/pages/{pages['sheet']['id']}/layout").json()
    assert "cells" in sheet
    body = client.get(f"/api/v1/pages/{pages['email_body']['id']}/layout").json()
    assert body == {"unit": "text", "text": "Please see attached."}
    assert client.get(f"/api/v1/pages/{pages['scan']['id']}/layout").status_code == 404


def test_reprocess_and_missing_resources(client: TestClient, submission_id: str) -> None:
    assert client.post(f"/api/v1/submissions/{submission_id}/process").status_code == 202
    runs = client.get(f"/api/v1/submissions/{submission_id}/runs").json()
    assert [r["trigger"] for r in runs] == ["rerun", "intake"]
    assert client.get("/api/v1/submissions/nope/pages").status_code == 404
    assert client.get("/api/v1/pages/nope/image").status_code == 404
