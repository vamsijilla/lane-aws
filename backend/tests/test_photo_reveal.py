"""A license photo is blurred by the API; an underwriter can reveal it, and each reveal is
recorded in the audit trail. A viewer cannot."""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.api.auth import issue_token
from lane.api.main import create_app
from lane.config import get_settings
from lane.db.models import AuditEvent, Document, Page
from lane.pipeline.registry import PIPELINE
from lane.pipeline.runner import process_submission
from lane.storage.blobs import BlobStore
from tests.docs_factory import image_pdf, text_image
from tests.factories import make_email
from tests.llm_fakes import FakeTransport, factory, verdict
from tests.test_intake import ingest

STEPS = [s for s in PIPELINE if s.name in {"triage", "classify"}]


def test_a_license_photo_is_blurred_until_an_underwriter_reveals_it(
    db: Session, store: BlobStore
) -> None:
    card = image_pdf(
        text_image(color=True, lines=["DRIVER LICENSE", "DL C4471925", "DOB 09/14/1979"])
    )
    sid = ingest(db, store, make_email([("license.pdf", card)])).submission_id
    transport = FakeTransport(lambda _r, _t: verdict("drivers_license"))
    process_submission(
        db, store, get_settings(), sid, steps=STEPS, trigger="test", llm_factory=factory(transport)
    )
    page = db.scalars(
        select(Page)
        .join(Document, Document.id == Page.document_id)
        .where(Document.submission_id == sid, Document.doc_type == "drivers_license")
    ).first()
    assert page is not None
    with TestClient(create_app()) as client:
        listed = client.get(f"/api/v1/submissions/{sid}/pages").json()
        assert next(p for p in listed if p["id"] == page.id)["personal_photo"] is True
        blurred = client.get(f"/api/v1/pages/{page.id}/image")
        assert blurred.headers.get("x-lane-blurred") == "1"

        viewer = {"Authorization": f"Bearer {issue_token('viewer', get_settings())}"}
        assert (
            client.get(f"/api/v1/pages/{page.id}/image?reveal=1", headers=viewer).status_code == 403
        )

        shown = client.get(
            f"/api/v1/pages/{page.id}/image?reveal=1"
        )  # tests sign in as underwriter
        assert shown.status_code == 200 and "x-lane-blurred" not in shown.headers
        assert shown.content != blurred.content
        assert "no-store" in shown.headers["cache-control"]
    events = db.scalars(select(AuditEvent).where(AuditEvent.action == "pii.photo_revealed")).all()
    assert len(events) == 1 and events[0].subject_id == page.id
