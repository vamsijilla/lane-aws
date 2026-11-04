"""Snapshot a stored submission as a JSON fixture, so resolution and rules can be tested without
OCR, models or the database: `uv run python -m tests.fixtures.export_sample [SUBMISSION_ID]`."""

import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select

from lane.db.base import get_sessionmaker
from lane.db.models import (
    Attachment,
    Blob,
    Conflict,
    Document,
    DocumentExtraction,
    EmailMessage,
    Fact,
    Page,
    ResolvedField,
    Submission,
)

OUT = Path(__file__).with_name("sample_submission.json")


def _row(obj: Any) -> dict[str, Any]:
    return {c: getattr(obj, c) for c in obj.__table__.columns.keys()}  # noqa: SIM118


def _slice(db: Any, sub: Submission) -> dict[str, list[dict[str, Any]]]:
    sid = sub.id
    email = db.get(EmailMessage, sub.email_id)
    atts = list(db.scalars(select(Attachment).where(Attachment.submission_id == sid)))
    pages = list(db.scalars(select(Page).where(Page.submission_id == sid)))
    docs = list(db.scalars(select(Document).where(Document.submission_id == sid)))
    xs = list(db.scalars(select(DocumentExtraction).where(DocumentExtraction.submission_id == sid)))
    facts = list(db.scalars(select(Fact).where(Fact.submission_id == sid)))
    shas = {email.raw_sha256} | {a.blob_sha256 for a in atts if a.blob_sha256}
    for p in pages:
        shas |= {p.render_sha256, p.thumb_sha256, p.original_sha256, p.layout_sha256}
    blobs = [db.get(Blob, sha) for sha in sorted(x for x in shas if x)]
    for x in xs:
        x.llm_call_id = None  # model calls are not part of the slice
    return {
        "blobs": [_row(b) for b in blobs if b],
        "email_messages": [_row(email)],
        "submissions": [_row(sub)],
        "attachments": [_row(a) for a in atts],
        "documents": [_row(d) | {"superseded_by_id": None} for d in docs],
        "documents_superseded": [
            {"id": d.id, "superseded_by_id": d.superseded_by_id} for d in docs if d.superseded_by_id
        ],
        "pages": [_row(p) for p in pages],
        "document_extractions": [_row(x) for x in xs],
        "facts": [_row(f) for f in facts],
    }


def main() -> None:
    with get_sessionmaker()() as db:
        sid = sys.argv[1] if len(sys.argv) > 1 else db.scalars(select(Submission.id)).first()
        sub = db.get(Submission, sid)
        assert sub is not None
        docs = db.scalars(
            select(Document).where(Document.submission_id == sid).order_by(Document.ordinal)
        )
        facts = db.scalars(select(Fact).where(Fact.submission_id == sid))
        data = {
            "received": sub.email.received_at.date().isoformat(),
            "documents": [_row(d) for d in docs],
            "extractions": {
                x.document_id: x.data
                for x in db.scalars(
                    select(DocumentExtraction).where(DocumentExtraction.submission_id == sid)
                )
                if x.data is not None
            },
            "facts": [[f.document_id, f.path, f.id, f.grounding] for f in facts],
            # Everything resolve, rules and actions read, row by row, to rebuild the submission
            # in a fresh database (tests/slice.py)
            "slice": _slice(db, sub),
            "snapshot": {
                "fields": sorted(
                    [f.label, f.state, f.value_display]
                    for f in db.scalars(
                        select(ResolvedField).where(ResolvedField.submission_id == sid)
                    )
                ),
                "conflicts": sorted(
                    c.title
                    for c in db.scalars(select(Conflict).where(Conflict.submission_id == sid))
                ),
            },
        }
    OUT.write_text(json.dumps(data, default=str, sort_keys=True))
    print(f"Wrote {OUT} ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
