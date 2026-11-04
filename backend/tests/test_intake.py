import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lane.audit import chain
from lane.config import get_settings
from lane.db.models import Attachment, AttachmentRole, AttachmentStatus, AuditEvent, Submission
from lane.intake.service import IngestResult, IntakeLimits, IntakeRejected, ingest_email
from lane.storage.blobs import BlobStore
from tests.factories import JPEG, PDF, make_email, make_zip, ooxml


def ingest(db: Session, store: BlobStore, raw: bytes, *, link: bool = False) -> IngestResult:
    """Each email as its own submission; tests of follow-up linking pass link=True."""
    limits = IntakeLimits.from_settings(get_settings())
    return ingest_email(db, store, raw, source="test", limits=limits, link_related=link)


def attachments(db: Session, submission_id: str) -> list[Attachment]:
    return list(
        db.scalars(
            select(Attachment)
            .where(Attachment.submission_id == submission_id)
            .order_by(Attachment.ordinal)
        )
    )


def by_name(db: Session, submission_id: str) -> dict[str, Attachment]:
    return {a.filename: a for a in attachments(db, submission_id)}


def test_stores_body_and_attachments(db: Session, store: BlobStore) -> None:
    r = ingest(db, store, make_email([("app.pdf", PDF), ("photo.jpg", JPEG)]))
    assert r.created
    atts = attachments(db, r.submission_id)
    assert [(a.role, a.status) for a in atts] == [
        (AttachmentRole.EMAIL_BODY, AttachmentStatus.ACCEPTED),
        (AttachmentRole.ATTACHMENT, AttachmentStatus.ACCEPTED),
        (AttachmentRole.ATTACHMENT, AttachmentStatus.ACCEPTED),
    ]
    assert atts[1].blob is not None and atts[1].blob.kind == "pdf"
    assert store.get(atts[1].blob.storage_key) == PDF


def test_same_email_twice_is_one_submission(db: Session, store: BlobStore) -> None:
    raw = make_email([("app.pdf", PDF)])
    first = ingest(db, store, raw)
    second = ingest(db, store, raw)
    assert first.created and not second.created
    assert second.submission_id == first.submission_id
    assert (second.files, second.unique_files) == (first.files, first.unique_files) == (2, 2)
    assert db.scalar(select(func.count()).select_from(Submission)) == 1
    assert db.scalar(select(func.count()).where(AuditEvent.action == "email.resent")) == 1


def test_resend_with_same_message_id_but_reencoded_bytes_is_still_one(
    db: Session, store: BlobStore
) -> None:
    first = ingest(db, store, make_email([("a.pdf", PDF)], subject="Submission"))
    second = ingest(db, store, make_email([("a.pdf", PDF)], subject="Fwd: Submission"))
    assert not second.created and second.submission_id == first.submission_id


def test_without_message_id_falls_back_to_content_hash(db: Session, store: BlobStore) -> None:
    raw = make_email([("a.pdf", PDF)], message_id=None)
    assert ingest(db, store, raw).created
    assert not ingest(db, store, raw).created
    assert ingest(db, store, make_email([("b.pdf", PDF + b" ")], message_id=None)).created


def test_duplicate_files_are_stored_once_and_marked(db: Session, store: BlobStore) -> None:
    r = ingest(db, store, make_email([("a.pdf", PDF), ("copy of a.pdf", PDF)]))
    atts = by_name(db, r.submission_id)
    assert atts["copy of a.pdf"].status is AttachmentStatus.DUPLICATE
    assert atts["copy of a.pdf"].duplicate_of_id == atts["a.pdf"].id
    assert r.duplicates == 1 and r.unique_files == 2  # body + a.pdf


def test_archives_are_expanded_and_members_deduped(db: Session, store: BlobStore) -> None:
    inner = make_zip({"pack/inner.pdf": JPEG})
    outer = make_zip({"pack/a.pdf": PDF, "pack/nested.zip": inner})
    r = ingest(db, store, make_email([("a.pdf", PDF), ("pack.zip", outer)]))
    atts = by_name(db, r.submission_id)
    assert atts["pack.zip"].status is AttachmentStatus.EXPANDED
    assert atts["nested.zip"].status is AttachmentStatus.EXPANDED
    assert atts["nested.zip"].depth == 1
    assert atts["inner.pdf"].status is AttachmentStatus.ACCEPTED
    assert atts["inner.pdf"].depth == 2 and atts["inner.pdf"].archive_path == "pack/inner.pdf"
    member_pdf = next(a for a in attachments(db, r.submission_id) if a.archive_path == "pack/a.pdf")
    assert member_pdf.status is AttachmentStatus.DUPLICATE
    assert r.expanded_archives == 2


def test_archives_nested_beyond_the_limit_are_rejected(
    db: Session, store: BlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANE_INTAKE_MAX_ARCHIVE_DEPTH", "1")
    get_settings.cache_clear()
    nested = make_zip({"deep.zip": make_zip({"x.pdf": PDF})})
    r = ingest(db, store, make_email([("outer.zip", nested)]))
    atts = by_name(db, r.submission_id)
    assert atts["outer.zip"].status is AttachmentStatus.EXPANDED
    assert atts["deep.zip"].status is AttachmentStatus.REJECTED
    assert atts["deep.zip"].reason == "Archive is nested too deeply to open"


def test_bad_files_are_rejected_visibly_while_the_rest_continue(
    db: Session, store: BlobStore
) -> None:
    raw = make_email(
        [
            ("broken.zip", b"PK\x03\x04 corrupt"),
            ("empty.pdf", b""),
            ("hostile.zip", make_zip({"../escape.pdf": PDF})),
            ("good.pdf", PDF),
        ]
    )
    r = ingest(db, store, raw)
    atts = by_name(db, r.submission_id)
    assert atts["broken.zip"].reason == "Archive is corrupt or not a zip file"
    assert atts["empty.pdf"].reason == "File is empty"
    assert atts["escape.pdf"].reason == "Unsafe path inside archive"
    assert atts["good.pdf"].status is AttachmentStatus.ACCEPTED
    assert r.rejected == 3 and len(r.notes) == 3


def test_unsupported_formats_are_kept_and_flagged(db: Session, store: BlobStore) -> None:
    r = ingest(db, store, make_email([("loss letter.docx", ooxml("word/document.xml"))]))
    att = by_name(db, r.submission_id)["loss letter.docx"]
    assert att.status is AttachmentStatus.ACCEPTED
    assert att.reason == "Stored, but docx files are not read automatically yet"
    assert r.unsupported == 1


def test_filenames_are_display_only_and_sanitized(db: Session, store: BlobStore) -> None:
    r = ingest(db, store, make_email([("../../etc/passwd\x07.pdf", PDF)]))
    assert "passwd.pdf" in by_name(db, r.submission_id)


def test_html_only_body_becomes_text(db: Session, store: BlobStore) -> None:
    html = "<html><body><p>FEIN is 12-3456789</p><script>x()</script></body></html>"
    r = ingest(db, store, make_email(html=html))
    body = attachments(db, r.submission_id)[0]
    assert body.role is AttachmentRole.EMAIL_BODY and body.blob is not None
    assert store.get(body.blob.storage_key).decode() == "FEIN is 12-3456789"


def test_rejects_non_email_and_oversized_input(
    db: Session, store: BlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(IntakeRejected, match="not an email"):
        ingest(db, store, PDF)
    monkeypatch.setenv("LANE_INTAKE_MAX_EMAIL_MB", "0")
    get_settings.cache_clear()
    with pytest.raises(IntakeRejected, match="over the"):
        ingest(db, store, make_email())


def test_every_step_is_in_a_verifiable_audit_chain(db: Session, store: BlobStore) -> None:
    ingest(db, store, make_email([("a.pdf", PDF), ("b.pdf", PDF)]))
    actions = list(db.scalars(select(AuditEvent.action).order_by(AuditEvent.seq)))
    assert actions == [
        "email.received",
        "attachment.accepted",  # body
        "attachment.accepted",
        "attachment.duplicate",
        "intake.completed",
    ]
    assert chain.verify(db).ok
