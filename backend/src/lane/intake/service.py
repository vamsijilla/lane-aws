"""Turn one raw email into a stored submission.

Every file the agent sent is accounted for: stored, marked as a duplicate, expanded (archives)
or rejected with a reason. Nothing is silently dropped. Re-sending the same email returns the
existing submission.
"""

from dataclasses import dataclass, field
from datetime import UTC

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lane.audit import chain
from lane.config import Settings
from lane.db.models import (
    Attachment,
    AttachmentRole,
    AttachmentStatus,
    Blob,
    EmailMessage,
    Submission,
)
from lane.intake import link
from lane.intake.archive import ArchiveLimits, read_zip
from lane.intake.eml import ParsedEmail, parse_eml
from lane.intake.sniff import SUPPORTED_KINDS, FileKind, sniff
from lane.storage.blobs import BlobStore, sha256_hex
from lane.storage.records import ensure_blob

ACTOR = "system:intake"
MB = 1024 * 1024


class IntakeRejected(ValueError):
    """The email as a whole cannot be accepted (too large, not an email)."""


@dataclass(frozen=True)
class IntakeLimits:
    max_email_bytes: int
    max_depth: int
    archive: ArchiveLimits

    @classmethod
    def from_settings(cls, s: Settings) -> "IntakeLimits":
        return cls(
            max_email_bytes=s.intake_max_email_mb * MB,
            max_depth=s.intake_max_archive_depth,
            archive=ArchiveLimits(
                max_members=s.intake_max_archive_members,
                max_member_bytes=s.intake_max_file_mb * MB,
                max_total_bytes=s.intake_max_expanded_mb * MB,
                max_compression_ratio=s.intake_max_compression_ratio,
            ),
        )


@dataclass
class IngestResult:
    submission_id: str
    email_id: str
    created: bool
    files: int = 0
    unique_files: int = 0
    duplicates: int = 0
    expanded_archives: int = 0
    rejected: int = 0
    unsupported: int = 0
    notes: list[str] = field(default_factory=lambda: list[str]())
    # What to run now: this submission, the one its files were attached to, or nothing
    process_id: str | None = None
    trigger: str = "intake"
    relation: str | None = None
    related_to: str | None = None


def ingest_email(
    session: Session,
    store: BlobStore,
    raw: bytes,
    *,
    source: str,
    limits: IntakeLimits,
    link_related: bool = True,
) -> IngestResult:
    """Ingest and commit. Safe to call repeatedly with the same email.

    With link_related (the default), an email about a submission we already have is linked to
    it (lane/intake/link.py); evaluation runs that ingest variants of one packet turn it off."""
    if len(raw) > limits.max_email_bytes:
        raise IntakeRejected(
            f"Email is {len(raw):,} bytes, over the {limits.max_email_bytes:,} limit"
        )
    if sniff(raw).kind is not FileKind.EML:
        raise IntakeRejected("File is not an email message (.eml)")

    parsed = parse_eml(raw)
    key = f"mid:{parsed.message_id}" if parsed.message_id else f"sha:{sha256_hex(raw)}"

    existing = _find_existing(session, key)
    if existing:
        return _report_resend(session, existing)

    try:
        ingestor = _Ingestor(session, store, limits)
        result = ingestor.run(raw, parsed, key, source)
        result.process_id = result.submission_id
        if link_related:
            placed = link.place(session, store, ingestor.submission, parsed)
            result.process_id = placed.process_id
            result.trigger = placed.trigger
            result.relation = placed.relation
            result.related_to = placed.target_id
        session.commit()
    except IntegrityError:
        # A concurrent ingest of the same email won the race.
        session.rollback()
        existing = _find_existing(session, key)
        if existing is None:
            raise
        return _report_resend(session, existing)
    return result


def _find_existing(session: Session, key: str) -> Submission | None:
    return session.scalars(
        select(Submission).join(EmailMessage).where(EmailMessage.idempotency_key == key)
    ).first()


def _report_resend(session: Session, submission: Submission) -> IngestResult:
    chain.record(
        session,
        actor=ACTOR,
        action="email.resent",
        subject_type="submission",
        subject_id=submission.id,
        payload={"note": "Same message received again; existing submission kept"},
    )
    session.commit()
    return summarize(submission, created=False)


def summarize(submission: Submission, *, created: bool) -> IngestResult:
    """Counts for a stored submission, recomputed from its attachment records."""
    r = IngestResult(submission.id, submission.email_id, created=created)
    for att in submission.attachments:
        r.files += 1
        match att.status:
            case AttachmentStatus.ACCEPTED:
                r.unique_files += 1
                if att.blob and FileKind(att.blob.kind) not in SUPPORTED_KINDS:
                    r.unsupported += 1
            case AttachmentStatus.DUPLICATE:
                r.duplicates += 1
            case AttachmentStatus.EXPANDED:
                r.expanded_archives += 1
            case AttachmentStatus.REJECTED:
                r.rejected += 1
                r.notes.append(f"{att.filename}: {att.reason}")
    return r


class _Ingestor:
    def __init__(self, session: Session, store: BlobStore, limits: IntakeLimits) -> None:
        self.session = session
        self.store = store
        self.limits = limits
        self.first_by_sha: dict[str, Attachment] = {}
        self.ordinal = 0
        self.result: IngestResult | None = None

    def run(self, raw: bytes, parsed: ParsedEmail, key: str, source: str) -> IngestResult:
        raw_blob = self._blob(raw)
        email = EmailMessage(
            idempotency_key=key,
            message_id=parsed.message_id,
            raw_sha256=raw_blob.sha256,
            source=source,
            from_addr=parsed.from_addr,
            to_addrs=parsed.to_addrs,
            subject=parsed.subject,
            # in UTC, so a database that drops the zone (SQLite) keeps the right instant
            sent_at=parsed.sent_at.astimezone(UTC) if parsed.sent_at else None,
            headers=parsed.headers,
        )
        submission = Submission(email=email)
        self.session.add_all([email, submission])
        self.session.flush()
        self.submission = submission
        self.result = IngestResult(submission.id, email.id, created=True)

        chain.record(
            self.session,
            actor=ACTOR,
            action="email.received",
            subject_type="submission",
            subject_id=submission.id,
            payload={
                "from": parsed.from_addr,
                "subject": parsed.subject,
                "message_id": parsed.message_id,
                "raw_sha256": raw_blob.sha256,
                "parts": len(parsed.parts),
                "source": source,
            },
        )

        if parsed.body_text:
            self._add(
                parsed.body_text.encode(),
                filename="Email body",
                declared_mime="text/plain",
                role=AttachmentRole.EMAIL_BODY,
            )
        for part in parsed.parts:
            self._add(part.data, filename=part.filename, declared_mime=part.declared_mime)

        r = self.result
        chain.record(
            self.session,
            actor=ACTOR,
            action="intake.completed",
            subject_type="submission",
            subject_id=submission.id,
            payload={
                "files": r.files,
                "unique_files": r.unique_files,
                "duplicates": r.duplicates,
                "expanded_archives": r.expanded_archives,
                "rejected": r.rejected,
                "unsupported": r.unsupported,
            },
        )
        return r

    def _blob(self, data: bytes) -> Blob:
        return ensure_blob(self.session, self.store, data)

    def _add(
        self,
        data: bytes,
        *,
        filename: str,
        declared_mime: str | None,
        role: AttachmentRole = AttachmentRole.ATTACHMENT,
        parent: Attachment | None = None,
        archive_path: str | None = None,
        depth: int = 0,
        rejected: str | None = None,
    ) -> Attachment:
        assert self.result is not None
        self.ordinal += 1
        att = Attachment(
            submission_id=self.submission.id,
            ordinal=self.ordinal,
            role=role,
            filename=filename,
            declared_mime=declared_mime,
            parent_id=parent.id if parent else None,
            archive_path=archive_path,
            depth=depth,
        )

        if rejected:
            return self._finish(att, AttachmentStatus.REJECTED, reason=rejected)
        if not data:
            return self._finish(att, AttachmentStatus.REJECTED, reason="File is empty")

        blob = self._blob(data)
        att.blob_sha256 = blob.sha256
        kind = FileKind(blob.kind)

        first = self.first_by_sha.get(blob.sha256)
        if first is not None:
            att.duplicate_of_id = first.id
            return self._finish(
                att, AttachmentStatus.DUPLICATE, reason=f"Same bytes as {first.filename}"
            )

        if kind is FileKind.ZIP:
            return self._expand(att, data, depth)

        self.first_by_sha[blob.sha256] = att
        reason = None
        if kind not in SUPPORTED_KINDS:
            reason = f"Stored, but {kind.value} files are not read automatically yet"
            self.result.unsupported += 1
        self.result.unique_files += 1
        return self._finish(att, AttachmentStatus.ACCEPTED, reason=reason)

    def _expand(self, att: Attachment, data: bytes, depth: int) -> Attachment:
        assert self.result is not None and att.blob_sha256 is not None
        if depth >= self.limits.max_depth:
            return self._finish(
                att, AttachmentStatus.REJECTED, reason="Archive is nested too deeply to open"
            )
        listing = read_zip(data, self.limits.archive)
        if listing.error:
            return self._finish(att, AttachmentStatus.REJECTED, reason=listing.error)

        self.first_by_sha[att.blob_sha256] = att
        self.result.expanded_archives += 1
        self._finish(
            att,
            AttachmentStatus.EXPANDED,
            reason=f"Opened archive with {len(listing.members)} files",
        )
        for member in listing.members:
            self._add(
                member.data or b"",
                filename=member.path.rsplit("/", 1)[-1] or member.path,
                declared_mime=None,
                role=AttachmentRole.ARCHIVE_MEMBER,
                parent=att,
                archive_path=member.path,
                depth=depth + 1,
                rejected=member.rejected,
            )
        return att

    def _finish(
        self, att: Attachment, status: AttachmentStatus, *, reason: str | None
    ) -> Attachment:
        assert self.result is not None
        att.status = status
        att.reason = reason
        self.session.add(att)
        self.session.flush()
        self.result.files += 1
        if status is AttachmentStatus.DUPLICATE:
            self.result.duplicates += 1
        elif status is AttachmentStatus.REJECTED:
            self.result.rejected += 1
            self.result.notes.append(f"{att.filename}: {reason}")
        chain.record(
            self.session,
            actor=ACTOR,
            action=f"attachment.{status.value}",
            subject_type="attachment",
            subject_id=att.id,
            payload={
                "submission_id": att.submission_id,
                "filename": att.filename,
                "archive_path": att.archive_path,
                "sha256": att.blob_sha256,
                "role": att.role.value,
                "reason": reason,
            },
        )
        return att
