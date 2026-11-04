"""Find text in submitted documents that tries to instruct a reader or a system.

Document text is always data: models see it wrapped and labeled, and rules never read it as
instructions. This scan makes attempts visible: each one is recorded in the audit chain and shown
to the underwriter, whether or not any model runs.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.audit import chain
from lane.config import Settings
from lane.db.models import Attachment, AuditEvent, Blob, Page, PageKind, Submission
from lane.storage.blobs import BlobStore

ACTION = "security.injection_detected"


@dataclass(frozen=True)
class Detection:
    page_id: str
    filename: str
    page_no: int
    quote: str


def _hidden_text(session: Session, page: Page) -> str:
    """Text triage dropped as hidden (white, invisible, tiny): no longer in the page's words."""
    return " ".join(
        str(e.payload.get("text", ""))
        for e in session.scalars(
            select(AuditEvent).where(
                AuditEvent.action == HIDDEN_ACTION, AuditEvent.subject_id == page.id
            )
        )
    )


def _page_text(store: BlobStore, session: Session, page: Page) -> str:
    """Everything on the page, seen or hidden: an instruction in white text is still one."""
    return f"{_visible_text(store, session, page)} {_hidden_text(session, page)}"


def _visible_text(store: BlobStore, session: Session, page: Page) -> str:
    if page.kind is PageKind.EMAIL_BODY:
        blob = page.attachment.blob
        return store.get(blob.storage_key).decode("utf-8", errors="replace") if blob else ""
    if not page.layout_sha256:
        return ""
    blob = session.get(Blob, page.layout_sha256)
    if blob is None:
        return ""
    loaded: object = json.loads(store.get(blob.storage_key))
    if not isinstance(loaded, dict):
        return ""
    words = cast(list[list[Any]], cast(dict[str, Any], loaded).get("words", []))
    return " ".join(str(w[4]) for w in words if len(w) > 4)


def scan(
    session: Session, store: BlobStore, settings: Settings, submission: Submission
) -> list[Detection]:
    patterns = [re.compile(p, re.IGNORECASE) for p in settings.injection_patterns]
    found: list[Detection] = []
    pages = session.execute(
        select(Page, Attachment.filename)
        .join(Attachment, Attachment.id == Page.attachment_id)
        .where(Attachment.submission_id == submission.id)
    ).all()
    for page, filename in pages:
        text = _page_text(store, session, page)
        for pat in patterns:
            m = pat.search(text)
            if m:
                start = max(0, m.start() - 40)
                quote = re.sub(r"\s+", " ", text[start : m.end() + 60]).strip()
                found.append(Detection(page.id, filename, page.page_no, quote))
                break
    return found


def record(session: Session, submission: Submission, found: list[Detection]) -> int:
    """Record each detection once (per page), however often the submission is reprocessed."""
    seen = set(
        session.scalars(
            select(AuditEvent.subject_id).where(
                AuditEvent.action == ACTION, AuditEvent.subject_type == "page"
            )
        )
    )
    new = 0
    for d in found:
        if d.page_id in seen:
            continue
        chain.record(
            session,
            actor="system:security",
            action=ACTION,
            subject_type="page",
            subject_id=d.page_id,
            payload={
                "submission_id": submission.id,
                "filename": d.filename,
                "page": d.page_no,
                "text": d.quote,
            },
        )
        new += 1
    session.commit()
    return new


HIDDEN_ACTION = "security.hidden_text"


def record_hidden(
    session: Session,
    submission: Submission,
    page_id: str,
    filename: str,
    page_no: int,
    spans: list[dict[str, Any]],
) -> int:
    """Each hidden span once per page: what it said, where, and how it was hidden."""
    seen = {
        (e.subject_id, str(e.payload.get("text")))
        for e in session.scalars(
            select(AuditEvent).where(
                AuditEvent.action == HIDDEN_ACTION, AuditEvent.subject_id == page_id
            )
        )
    }
    new = 0
    for span in spans:
        if (page_id, str(span["text"])) in seen:
            continue
        chain.record(
            session,
            actor="system:security",
            action=HIDDEN_ACTION,
            subject_type="page",
            subject_id=page_id,
            payload={
                "submission_id": submission.id,
                "filename": filename,
                "page": page_no,
                "text": span["text"],
                "reason": span["reason"],
                "bbox": span["bbox"],
            },
        )
        new += 1
    return new
