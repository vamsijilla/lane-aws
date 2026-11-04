"""Is a new email about a submission we already have?

Checked in this order, and the first that answers wins:

1. Same files: every file it carries is already in an open submission. It is linked there and
   nothing is processed (a resend of the packet in a new email).
2. Reference token: the "[Ref LN-xxxxxxxx]" Lane puts in the subject of every agent request.
3. Thread: In-Reply-To or References name an email of an open submission.
4. Identity: a DOT, MC or FEIN number in the new email matches the applicant of an open
   submission, and the sender writes from the same domain.

Any of 2 to 4 is confident: the new files are attached to that submission and it runs again.
An identity match from another domain, or the applicant's name alone, is only a suggestion: the
email waits as "Possible follow-up" until a person attaches it or keeps it separate. Lane never
merges on a guess.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import pymupdf
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lane.audit import chain
from lane.db.base import utcnow
from lane.db.models import (
    Attachment,
    AttachmentRole,
    AttachmentStatus,
    ResolvedEntity,
    ResolvedField,
    Submission,
    SubmissionStatus,
)
from lane.intake.eml import ParsedEmail
from lane.intake.sniff import FileKind
from lane.storage.blobs import BlobStore

ACTOR = "system:intake"
TOKEN = re.compile(r"\[\s*Ref\s+LN-([0-9A-F]{8})\s*\]", re.IGNORECASE)
MESSAGE_ID = re.compile(r"<([^<>\s]+)>")
IDENTITY = {
    "dot_number": re.compile(r"\b(?:US\s*)?DOT\s*(?:#|No\.?|Number)?\s*[:#]?\s*(\d{5,8})\b", re.I),
    "mc_number": re.compile(r"\bMC\s*(?:#|No\.?|Number)?\s*[:#-]?\s*(\d{5,8})\b", re.I),
    "fein": re.compile(r"\b(\d{2}-\d{7})\b"),
}
IDENTITY_LABELS = {"dot_number": "DOT", "mc_number": "MC", "fein": "FEIN"}
LEGAL_SUFFIXES = {"inc", "llc", "corp", "corporation", "co", "company", "ltd", "lp", "llp"}
PDF_PAGES = 3  # identity numbers sit on the first pages of an application


def reference(submission_id: str) -> str:
    """The stable reference for a submission, printed in every agent request's subject."""
    return f"LN-{submission_id[:8].upper()}"


def tag_subject(subject: str, submission_id: str) -> str:
    ref = f"[Ref {reference(submission_id)}]"
    return subject if ref in subject else f"{subject} {ref}"


@dataclass(frozen=True)
class Placement:
    """What intake does with a new email: process it as its own submission, process the
    submission it was attached to, or nothing (same files; a suggestion awaiting a person)."""

    process_id: str | None
    trigger: str
    relation: str | None = None
    target_id: str | None = None
    reason: str | None = None


def place(session: Session, store: BlobStore, new: Submission, parsed: ParsedEmail) -> Placement:
    """Decide and record how a just-ingested email relates to open submissions."""
    open_subs = _open_submissions(session, new.id)
    if not open_subs:
        return Placement(new.id, "intake")

    hashes = _file_hashes(new)
    if hashes:
        for sub in open_subs:
            if hashes <= _all_hashes(sub):
                reason = f"Same files as the submission from {sub.email.from_addr}"
                _relate(session, new, sub, "same_files", "files", reason)
                new.status = SubmissionStatus.READY
                return Placement(None, "intake", "same_files", sub.id, reason)

    text = _text(store, new, parsed)
    domain = _domain(parsed.from_addr)

    m = TOKEN.search(parsed.subject or "") or TOKEN.search(parsed.body_text or "")
    if m:
        target = next((s for s in open_subs if s.id.startswith(m.group(1).lower())), None)
        if target is not None:
            return _attach(session, new, target, "reference", f"Reference {reference(target.id)}")

    thread_ids = {
        mid
        for h in ("In-Reply-To", "References")
        for mid in MESSAGE_ID.findall(parsed.headers.get(h, ""))
    }
    if thread_ids:
        for sub in open_subs:
            if thread_ids & _message_ids(session, sub):
                return _attach(session, new, sub, "thread", "Reply in the same email thread")

    found = {k: {_digits(v) for v in rx.findall(text)} for k, rx in IDENTITY.items()}
    for sub in open_subs:
        ids = _identity(session, sub)
        shared = [
            f"{IDENTITY_LABELS[k]} {v}"
            for k, vals in found.items()
            for v in vals & ids.get(k, set())
        ]
        if not shared:
            continue
        if domain and domain in _domains(session, sub):
            return _attach(session, new, sub, "identity", f"Same {', '.join(shared)} from {domain}")
        return _suggest(
            session, new, sub, "identity", f"Same {', '.join(shared)}, but sent from {domain}"
        )

    low = _norm(f"{parsed.subject}\n{text}")
    for sub in open_subs:
        name = _applicant_name(session, sub)
        if name and len(name) >= 6 and f" {name} " in f" {low} ":
            return _suggest(
                session, new, sub, "name", "Names the same applicant; nothing else matches"
            )
    return Placement(new.id, "intake")


def attach(session: Session, new: Submission, target: Submission, *, actor: str) -> Placement:
    """A person attached a suggested follow-up."""
    return _attach(
        session, new, target, new.relation_how or "person", "Attached by a person", actor
    )


def keep_separate(session: Session, new: Submission, *, actor: str) -> Placement:
    new.relation = "kept_separate"
    new.relation_at = utcnow()
    new.relation_by = actor
    chain.record(
        session,
        actor=actor,
        action="submission.kept_separate",
        subject_type="submission",
        subject_id=new.id,
        payload={"suggested": new.related_to_id},
    )
    return Placement(new.id, "intake", "kept_separate", new.related_to_id)


def _open_submissions(session: Session, exclude: str) -> list[Submission]:
    """Submissions an email can join: not failed, and not themselves folded into another."""
    return list(
        session.scalars(
            select(Submission)
            .where(
                Submission.id != exclude,
                Submission.status != SubmissionStatus.FAILED,
                (Submission.relation.is_(None)) | (Submission.relation == "kept_separate"),
            )
            .order_by(Submission.created_at.desc())
        )
    )


def _files(sub: Submission) -> Iterable[Attachment]:
    return (
        a
        for a in sub.attachments
        if a.role is not AttachmentRole.EMAIL_BODY
        and a.status in (AttachmentStatus.ACCEPTED, AttachmentStatus.DUPLICATE)
        and a.blob_sha256
    )


def _file_hashes(sub: Submission) -> set[str]:
    return {a.blob_sha256 for a in _files(sub) if a.blob_sha256}


def _all_hashes(sub: Submission) -> set[str]:
    """Every file a submission holds, archives and their members included."""
    return {
        a.blob_sha256
        for a in sub.attachments
        if a.blob_sha256 and a.role is not AttachmentRole.EMAIL_BODY
    }


def _text(store: BlobStore, sub: Submission, parsed: ParsedEmail) -> str:
    """The email's words and the text layer of its PDFs' first pages, for identity numbers."""
    parts = [parsed.subject or "", parsed.body_text or ""]
    for a in _files(sub):
        if a.blob is not None and FileKind(a.blob.kind) is FileKind.PDF:
            parts.append(_pdf_text(store.get(a.blob.storage_key)))
    return "\n".join(parts)


def _pdf_text(data: bytes) -> str:
    try:
        doc: Any = pymupdf.open(stream=data, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError):
        return ""  # an unreadable file is triage's to report, not intake's
    with doc:
        return "\n".join(
            str(doc[i].get_text("text")) for i in range(min(PDF_PAGES, doc.page_count))
        )


def _message_ids(session: Session, sub: Submission) -> set[str]:
    emails = [sub.email, *(c.email for c in _children(session, sub))]
    return {e.message_id for e in emails if e.message_id}


def _domains(session: Session, sub: Submission) -> set[str]:
    emails = [sub.email, *(c.email for c in _children(session, sub))]
    return {d for d in (_domain(e.from_addr) for e in emails) if d}


def _children(session: Session, sub: Submission) -> list[Submission]:
    return list(
        session.scalars(
            select(Submission).where(
                Submission.related_to_id == sub.id, Submission.relation == "follow_up"
            )
        )
    )


def _identity(session: Session, sub: Submission) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for f in _applicant_fields(session, sub, list(IDENTITY)):
        vals = {_digits(c.get("value")) for c in f.candidates} | {_digits(f.value)}
        out.setdefault(f.attribute, set()).update(v for v in vals if len(v) >= 5)
    return out


def _applicant_name(session: Session, sub: Submission) -> str | None:
    for f in _applicant_fields(session, sub, ["legal_name"]):
        if f.value:
            words = _norm(str(f.value)).split()
            while words and words[-1] in LEGAL_SUFFIXES:
                words.pop()
            return " ".join(words)
    return None


def _applicant_fields(session: Session, sub: Submission, attrs: list[str]) -> list[ResolvedField]:
    return list(
        session.scalars(
            select(ResolvedField)
            .join(ResolvedEntity, ResolvedEntity.id == ResolvedField.entity_id)
            .where(
                ResolvedField.submission_id == sub.id,
                ResolvedEntity.kind == "applicant",
                ResolvedField.attribute.in_(attrs),
            )
        )
    )


def _relate(
    session: Session,
    new: Submission,
    target: Submission,
    relation: str,
    how: str,
    reason: str,
    actor: str = ACTOR,
) -> None:
    new.related_to_id = target.id
    new.relation = relation
    new.relation_how = how
    new.relation_reason = reason
    new.relation_at = utcnow()
    new.relation_by = actor
    chain.record(
        session,
        actor=actor,
        action=f"submission.{relation}",
        subject_type="submission",
        subject_id=new.id,
        payload={"related_to": target.id, "how": how, "reason": reason},
    )


def _suggest(
    session: Session, new: Submission, target: Submission, how: str, reason: str
) -> Placement:
    _relate(session, new, target, "possible_follow_up", how, reason)
    new.status = SubmissionStatus.READY
    return Placement(None, "intake", "possible_follow_up", target.id, reason)


def _attach(
    session: Session,
    new: Submission,
    target: Submission,
    how: str,
    reason: str,
    actor: str = ACTOR,
) -> Placement:
    """Move the new email's files into the target submission, which then runs again. A file
    the target already has is kept as a duplicate of it, so it is not read twice."""
    _relate(session, new, target, "follow_up", how, reason, actor)
    have = {a.blob_sha256: a for a in target.attachments if a.blob_sha256}
    start = (
        session.scalar(
            select(func.max(Attachment.ordinal)).where(Attachment.submission_id == target.id)
        )
        or 0
    )
    moved = added = 0
    for att in sorted(new.attachments, key=lambda a: a.ordinal):
        att.submission_id = target.id
        att.ordinal = start + att.ordinal
        moved += 1
        if att.status is AttachmentStatus.ACCEPTED and att.blob_sha256 in have:
            first = have[att.blob_sha256]
            att.status = AttachmentStatus.DUPLICATE
            att.duplicate_of_id = first.id
            att.reason = f"Same bytes as {first.filename}, sent earlier"
        elif att.status is AttachmentStatus.ACCEPTED:
            added += 1
    new.status = SubmissionStatus.READY
    chain.record(
        session,
        actor=actor,
        action="submission.amended",
        subject_type="submission",
        subject_id=target.id,
        payload={
            "note": "Amended by email from the agent",
            "email_id": new.email_id,
            "from_submission": new.id,
            "how": how,
            "reason": reason,
            "files": moved,
            "new_files": added,
        },
    )
    session.flush()
    session.expire(new, ["attachments"])
    session.expire(target, ["attachments"])
    return Placement(target.id, "follow_up", "follow_up", target.id, reason)


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _domain(addr: str | None) -> str:
    return (addr or "").rsplit("@", 1)[-1].lower().strip(">") if addr and "@" in addr else ""


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
