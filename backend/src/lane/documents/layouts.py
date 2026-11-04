"""Known layouts, learned only from documents a person confirmed (tier 2 of classification).

Nothing ships seeded: a layout exists because someone confirmed a document's type, and it keeps
where it came from (document, page, who, when). Its landmarks are the label-like lines of the
confirmed page; its fields are the labels printed beside the values the document was read with.
The model re-checks a sample of the pages a layout types (templates_audit_rate); a disagreement
disables the layout and sends the document to review.
"""

import hashlib
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.audit import chain
from lane.db.base import utcnow
from lane.db.models import Blob, Document, LearnedLayout, Page, PageKind
from lane.documents import templates
from lane.documents.layout import load_layout
from lane.documents.template_tool import LABEL, propose_anchors
from lane.documents.types import DOC_TYPE_LABELS, DocType
from lane.storage.blobs import BlobStore

# Types whose documents are told apart by whom they are about: a layout types such a page with
# no model call only when it can read the subject itself.
SUBJECT_REQUIRED = {DocType.MVR, DocType.DRIVERS_LICENSE}
MAX_SHEET_ANCHORS = 10


def active(session: Session) -> list[templates.Template]:
    return [
        templates.Template.model_validate(row.definition)
        for row in session.scalars(
            select(LearnedLayout).where(LearnedLayout.status == "active").order_by(LearnedLayout.id)
        )
    ]


def all_layouts(session: Session) -> list[templates.Template]:
    """Active and disabled, for checks and reports."""
    return [
        templates.Template.model_validate(row.definition)
        for row in session.scalars(select(LearnedLayout).order_by(LearnedLayout.id))
    ]


def _layout(session: Session, store: BlobStore, page: Page) -> dict[str, object] | None:
    blob = session.get(Blob, page.layout_sha256) if page.layout_sha256 else None
    return load_layout(store.get(blob.storage_key)) if blob else None


def _field(text: str, value: str | None) -> list[str]:
    """The label printed before a value on its line ('Driver Name: DOE, JANE'), as a
    pattern that reads whatever value follows that label on another document."""
    if not value or len(value) < 3:
        return []
    for line in text.splitlines():
        at = line.lower().find(value.lower())
        if at <= 0:
            continue
        label = line[:at].strip()
        if 2 <= len(label) <= 40 and LABEL.fullmatch(label):
            return [rf"{re.escape(label)}\s*(?P<value>[^\n]{{2,60}}?)\s*$"]
    return []


def _page_anchors(layout: dict[str, object], avoid: list[str]) -> list[templates.Anchor]:
    """Label-only lines and headings (template_tool.propose_anchors), plus the label of every
    'Label: value' line, so a form that prints labels beside their values is learned too."""
    anchors = propose_anchors(layout, avoid)
    have = {a.text.lower() for a in anchors}
    avoid_low = [a.lower() for a in avoid if a and len(a) > 3]
    for ln in templates.lines_of(layout):
        label, colon, _value = ln.text.partition(":")
        label = label.strip()
        if not colon or not 3 <= len(label) <= 40 or any(ch.isdigit() for ch in label):
            continue
        if not LABEL.fullmatch(label) or any(a in label.lower() for a in avoid_low):
            continue
        text = f"{label}:"
        if text.lower() in have:
            continue
        have.add(text.lower())
        x0, y0, x1, y1 = ln.nbox
        anchors.append(
            templates.Anchor(
                text=text, box=(round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)), tol=0.06
            )
        )
    return anchors[:12]


def _sheet_anchors(layout: dict[str, object]) -> list[templates.Anchor]:
    labels: list[str] = []
    for text in templates.sheet_cells(layout):
        t = text.strip()
        if LABEL.fullmatch(t) and not any(ch.isdigit() for ch in t) and t.lower() not in labels:
            labels.append(t.lower())
    return [templates.Anchor(text=t) for t in labels[:MAX_SHEET_ANCHORS]]


def learn(
    session: Session, store: BlobStore, document: Document, *, actor: str
) -> list[LearnedLayout]:
    """Learn layouts from a document a person has just confirmed: its first page and, when it
    has more, its first continuation page. Returns the layouts created or refreshed."""
    pages = list(
        session.scalars(select(Page).where(Page.document_id == document.id).order_by(Page.page_no))
    )
    doc_type = DocType(document.doc_type)
    learned: list[LearnedLayout] = []
    for role, page in (
        ("first", pages[0] if pages else None),
        ("continuation", pages[1] if len(pages) > 1 else None),
    ):
        if page is None:
            continue
        layout = _layout(session, store, page)
        if layout is None:
            continue
        if page.kind is PageKind.SHEET:
            anchors = _sheet_anchors(layout)
            kind = "sheet"
            text = "\n".join(templates.sheet_cells(layout))
        else:
            anchors = _page_anchors(
                layout, [document.subject_name or "", document.subject_id or ""]
            )
            kind = "layout"
            text = "\n".join(ln.text for ln in templates.lines_of(layout))
        if len(anchors) < 3:
            continue  # too little to recognise the layout by
        fields: dict[str, list[str]] = {}
        if role == "first":
            for name in ("subject_name", "subject_id", "period", "document_date"):
                pattern = _field(text, getattr(document, name))
                if pattern:
                    fields[name] = pattern
        required = ["subject_name"] if role == "first" and doc_type in SUBJECT_REQUIRED else []
        digest = hashlib.sha256(
            "|".join([doc_type.value, kind, role, *sorted(a.text for a in anchors)]).encode()
        ).hexdigest()[:12]
        layout_id = f"{doc_type.value}-{role}-{digest}"
        name = f"{DOC_TYPE_LABELS[doc_type]}, {role} page, from {document.title}"
        template = templates.Template(
            id=layout_id,
            name=name,
            doc_type=doc_type,
            kind=kind,  # type: ignore[arg-type]  # "layout" or "sheet"
            status="active",
            page=role,  # type: ignore[arg-type]  # "first" or "continuation"
            anchors=anchors,
            fields=fields,
            required=required,
            source=(
                f"Learned from {document.title}, page {page.page_no}, confirmed by "
                f"{actor.replace('user:', '')}"
            ),
        )
        row = session.get(LearnedLayout, layout_id) or LearnedLayout(id=layout_id)
        row.name = name
        row.doc_type = doc_type.value
        row.status = "active"
        row.definition = template.model_dump(mode="json")
        row.source_document_id = document.id
        row.source_submission_id = document.submission_id
        row.source_page_no = page.page_no
        row.confirmed_by = actor
        row.confirmed_at = utcnow()
        row.audits = row.audits or 0
        row.agreements = row.agreements or 0
        row.disabled_reason = None
        row.disabled_at = None
        session.add(row)
        learned.append(row)
        chain.record(
            session,
            actor=actor,
            action="layout.learned",
            subject_type="layout",
            subject_id=layout_id,
            payload={"document_id": document.id, "page": page.page_no, "doc_type": doc_type.value},
        )
    return learned


def record_audit(
    session: Session, layout_id: str, *, agrees: bool, page_id: str, model_type: str
) -> None:
    """The model re-checked a page this layout typed. A disagreement disables the layout."""
    row = session.get(LearnedLayout, layout_id)
    if row is None:
        return
    row.audits += 1
    if agrees:
        row.agreements += 1
        return
    if row.status == "active":
        row.status = "disabled"
        row.disabled_at = utcnow()
        said = DOC_TYPE_LABELS.get(DocType(model_type), model_type).lower()
        row.disabled_reason = (
            f"The AI audit read a page it typed as {said}, not "
            f"{DOC_TYPE_LABELS[DocType(row.doc_type)].lower()}"
        )
        chain.record(
            session,
            actor="system:classify",
            action="layout.disabled",
            subject_type="layout",
            subject_id=layout_id,
            payload={"page_id": page_id, "model_type": model_type, "reason": row.disabled_reason},
        )
