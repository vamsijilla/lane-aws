"""Sources we could not read, so that "nothing found" is never reported as "nothing there".

Two things make a source unread: a file that could not be opened (corrupt, encrypted, an
unsupported format), whose type is unknown, and a document whose extraction failed (a model
outage), whose type is known. Either could hold what a rule or a missing item looks for.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.db.models import Attachment, Document, DocumentExtraction, ExtractionStatus


@dataclass(frozen=True)
class UnreadSource:
    filename: str
    doc_type: str | None  # None: the file could not be opened, so its type is unknown
    reason: str

    def could_hold(self, doc_types: Iterable[str]) -> bool:
        """An unopened file could hold anything; a failed document only what its type holds."""
        return self.doc_type is None or self.doc_type in set(doc_types)


def unread_sources(session: Session, submission_id: str) -> list[UnreadSource]:
    out: dict[tuple[str, str | None], UnreadSource] = {}
    for a in session.scalars(
        select(Attachment).where(
            Attachment.submission_id == submission_id, Attachment.read_error.is_not(None)
        )
    ):
        out[(a.filename, None)] = UnreadSource(a.filename, None, a.read_error or "unreadable")
    for x, d, a in session.execute(
        select(DocumentExtraction, Document, Attachment)
        .join(Document, Document.id == DocumentExtraction.document_id)
        .join(Attachment, Attachment.id == Document.attachment_id)
        .where(
            DocumentExtraction.submission_id == submission_id,
            DocumentExtraction.status == ExtractionStatus.FAILED,
        )
    ):
        reason = x.escalation_reasons[0] if x.escalation_reasons else "extraction failed"
        out.setdefault((a.filename, d.doc_type), UnreadSource(a.filename, d.doc_type, reason))
    return sorted(out.values(), key=lambda u: (u.filename, u.doc_type or ""))


def names(sources: Sequence[UnreadSource]) -> str:
    return ", ".join(dict.fromkeys(u.filename for u in sources))
