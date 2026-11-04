"""An email about a submission we already have: linked or attached, never merged on a guess."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.api.main import create_app
from lane.db.models import (
    Attachment,
    AttachmentStatus,
    AuditEvent,
    ResolvedEntity,
    ResolvedField,
    Submission,
)
from lane.intake.link import reference, tag_subject
from lane.intake.service import IngestResult
from lane.storage.blobs import BlobStore
from tests.docs_factory import text_pdf
from tests.factories import PDF, make_email
from tests.test_intake import ingest

APP = text_pdf(lines=["Commercial auto application", "Named Insured: Granite Ridge Freight"])
SIGNED = text_pdf(lines=["Commercial auto application", "Signature: J. Doe", "USDOT 7654321"])


def first(db: Session, store: BlobStore) -> IngestResult:
    r = ingest(
        db,
        store,
        make_email([("app.pdf", APP), ("mvr.pdf", PDF)], message_id="<orig@agency.example>"),
        link=True,
    )
    assert r.process_id == r.submission_id and r.relation is None
    return r


def applicant(db: Session, sid: str, *, name: str, dot: str) -> None:
    """What resolution would have recorded for the first submission."""
    e = ResolvedEntity(
        id=f"a{sid[:20]}", submission_id=sid, kind="applicant", key="applicant", label=name
    )
    db.add(e)
    for attr, value in (("legal_name", name), ("dot_number", dot)):
        db.add(
            ResolvedField(
                id=f"{attr[:4]}{sid[:20]}",
                submission_id=sid,
                entity_id=e.id,
                attribute=attr,
                label=attr,
                kind="text",
                state="known",
                value=value,
                candidates=[{"value": value}],
            )
        )
    db.commit()


def test_the_same_email_twice_changes_nothing(db: Session, store: BlobStore) -> None:
    r = first(db, store)
    raw = make_email([("app.pdf", APP), ("mvr.pdf", PDF)], message_id="<orig@agency.example>")
    again = ingest(db, store, raw, link=True)
    assert not again.created and again.submission_id == r.submission_id
    assert len(db.scalars(select(Submission)).all()) == 1


def test_the_same_packet_in_a_new_email_is_linked_and_not_processed(
    db: Session, store: BlobStore
) -> None:
    r = first(db, store)
    resend = ingest(
        db,
        store,
        make_email([("mvr copy.pdf", PDF), ("app.pdf", APP)], message_id="<resend@agency.example>"),
        link=True,
    )
    assert resend.created and resend.process_id is None
    assert resend.relation == "same_files" and resend.related_to == r.submission_id
    assert "Same files" in (db.get(Submission, resend.submission_id).relation_reason or "")  # type: ignore[union-attr]


def test_a_reply_with_the_reference_attaches_its_new_files(db: Session, store: BlobStore) -> None:
    r = first(db, store)
    subject = tag_subject("Re: information needed before we can quote", r.submission_id)
    assert reference(r.submission_id) in subject
    reply = ingest(
        db,
        store,
        make_email(
            [("signed app.pdf", SIGNED), ("mvr.pdf", PDF)],
            subject=subject,
            body="FEIN is 12-3456789",
            message_id="<reply@agency.example>",
        ),
        link=True,
    )
    assert reply.process_id == r.submission_id and reply.trigger == "follow_up"
    assert reply.relation == "follow_up"
    moved = db.scalars(select(Attachment).where(Attachment.submission_id == r.submission_id)).all()
    by_name = {a.filename: a for a in moved}
    assert by_name["signed app.pdf"].status is AttachmentStatus.ACCEPTED
    # the MVR was sent before: kept as a duplicate, not read again
    assert [a.status for a in moved if a.filename == "mvr.pdf"].count(
        AttachmentStatus.DUPLICATE
    ) == 1
    assert "Email body" in by_name
    events = [e for e in db.scalars(select(AuditEvent)) if e.action == "submission.amended"]
    assert events and events[0].payload["note"] == "Amended by email from the agent"


def test_a_reply_in_the_thread_attaches(db: Session, store: BlobStore) -> None:
    r = first(db, store)
    reply = ingest(
        db,
        store,
        make_email(
            [("signed app.pdf", SIGNED)],
            subject="Re: your request",
            message_id="<reply2@agency.example>",
            headers={
                "In-Reply-To": "<lane-1@carrier.example>",
                "References": "<orig@agency.example> <lane-1@carrier.example>",
            },
        ),
        link=True,
    )
    assert reply.process_id == r.submission_id and reply.relation == "follow_up"


def test_a_reply_without_the_reference_but_the_same_dot_attaches(
    db: Session, store: BlobStore
) -> None:
    r = first(db, store)
    applicant(db, r.submission_id, name="Granite Ridge Freight LLC", dot="7654321")
    reply = ingest(
        db,
        store,
        make_email(
            [("signed app.pdf", SIGNED)], subject="Signed app", message_id="<r3@agency.example>"
        ),
        link=True,
    )
    assert reply.process_id == r.submission_id and reply.relation == "follow_up"
    sub = db.get(Submission, reply.submission_id)
    assert (
        sub is not None
        and sub.relation_how == "identity"
        and "DOT 7654321" in (sub.relation_reason or "")
    )


def test_the_same_dot_from_another_domain_is_only_suggested(db: Session, store: BlobStore) -> None:
    r = first(db, store)
    applicant(db, r.submission_id, name="Granite Ridge Freight LLC", dot="7654321")
    reply = ingest(
        db,
        store,
        make_email(
            [("signed app.pdf", SIGNED)],
            sender="Someone <x@other.example>",
            message_id="<r4@other.example>",
        ),
        link=True,
    )
    assert reply.process_id is None and reply.relation == "possible_follow_up"


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def _auth(client: TestClient) -> dict[str, str]:
    token = client.post("/api/v1/auth/demo-login", json={"role": "underwriter"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_a_name_alone_is_suggested_never_merged_and_a_person_decides(
    client: TestClient, db: Session, store: BlobStore
) -> None:
    r = first(db, store)
    applicant(db, r.submission_id, name="Granite Ridge Freight LLC", dot="7654321")
    other = text_pdf(lines=["Loss run", "Insured: Granite Ridge Freight"])
    reply = ingest(
        db,
        store,
        make_email(
            [("loss run.pdf", other)],
            subject="Granite Ridge Freight loss run",
            message_id="<r5@agency.example>",
        ),
        link=True,
    )
    assert reply.process_id is None and reply.relation == "possible_follow_up"
    assert db.scalars(
        select(Attachment).where(Attachment.submission_id == reply.submission_id)
    ).all()

    rows = client.get("/api/v1/submissions", headers=_auth(client)).json()
    row = next(x for x in rows if x["id"] == reply.submission_id)
    assert row["relation"] == "possible_follow_up" and row["related_to"] == r.submission_id

    out = client.post(f"/api/v1/submissions/{reply.submission_id}/attach", headers=_auth(client))
    assert out.status_code == 200 and out.json()["submission_id"] == r.submission_id
    db.expire_all()
    moved = db.scalars(select(Attachment).where(Attachment.filename == "loss run.pdf")).one()
    assert moved.submission_id == r.submission_id
    rows = client.get("/api/v1/submissions", headers=_auth(client)).json()
    assert reply.submission_id not in {x["id"] for x in rows}  # part of the earlier row now


def test_keep_separate_processes_it_as_its_own(
    client: TestClient, db: Session, store: BlobStore
) -> None:
    r = first(db, store)
    applicant(db, r.submission_id, name="Granite Ridge Freight LLC", dot="7654321")
    reply = ingest(
        db,
        store,
        make_email(
            [("loss run.pdf", text_pdf(lines=["Loss run"]))],
            subject="Granite Ridge Freight",
            message_id="<r6@agency.example>",
        ),
        link=True,
    )
    out = client.post(
        f"/api/v1/submissions/{reply.submission_id}/keep-separate", headers=_auth(client)
    )
    assert out.status_code == 200
    db.expire_all()
    assert db.get(Submission, reply.submission_id).relation == "kept_separate"  # type: ignore[union-attr]


def test_a_file_read_before_is_not_read_again_after_its_submission_is_deleted(
    db: Session, store: BlobStore
) -> None:
    from lane.config import get_settings
    from lane.intake.remove import delete_submission
    from lane.pipeline.registry import PIPELINE
    from lane.pipeline.runner import process_submission

    triage = [s for s in PIPELINE if s.name == "triage"]
    first = ingest(db, store, make_email([("app.pdf", APP)], message_id="<one@agency.example>"))
    run = process_submission(
        db, store, get_settings(), first.submission_id, steps=triage, trigger="t"
    )
    assert run.steps[0].summary["same_file_reused"] == 0
    delete_submission(db, first.submission_id, actor="test")

    again = ingest(db, store, make_email([("app.pdf", APP)], message_id="<two@agency.example>"))
    run = process_submission(
        db, store, get_settings(), again.submission_id, steps=triage, trigger="t"
    )
    assert run.steps[0].summary["same_file_reused"] == 2  # the PDF and the same body text, by file
