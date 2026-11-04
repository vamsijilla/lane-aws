"""Make and check layout templates from documents people have confirmed.

    uv run python -m lane.documents.template_tool propose --document <id> --name "..." [--page N]
    uv run python -m lane.documents.template_tool check

`propose` reads a page of a confirmed document and suggests landmarks: label-like lines (words,
not values: no digits, not the document's subject), spread down the page, each with the region
it sits in. It prints a draft template; a person adds the field patterns and sets it active.

`check` runs every template (drafts too) against every stored page and reports, per template,
which document types it matched. A template that matches a page of another type is not precise
enough to use.
"""

import argparse
import re
import sys
from collections import Counter
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.config import get_settings
from lane.db.base import get_sessionmaker
from lane.db.models import Blob, Document, Page, PageKind
from lane.documents import templates
from lane.documents.layout import load_layout, words_to_text
from lane.storage.blobs import BlobStore, blob_store

LABEL = re.compile(r"[A-Za-z][A-Za-z &/().,'#*-]{2,48}:?")
MAX_ANCHORS = 10


def _layout(session: Session, store: BlobStore, page: Page) -> dict[str, Any] | None:
    blob = session.get(Blob, page.layout_sha256) if page.layout_sha256 else None
    return load_layout(store.get(blob.storage_key)) if blob else None


def propose_anchors(layout: dict[str, Any], avoid: list[str]) -> list[templates.Anchor]:
    """Label-like lines, spread down the page; never values or the document's subject."""
    avoid_low = [a.lower() for a in avoid if a and len(a) > 3]
    candidates: list[templates.Line] = []
    seen: set[str] = set()
    for ln in templates.lines_of(layout):
        text = ln.text.strip()
        low = text.lower()
        if not LABEL.fullmatch(text) or any(ch.isdigit() for ch in text):
            continue
        if any(a in low or low in a for a in avoid_low) or low in seen:
            continue
        seen.add(low)
        candidates.append(ln)
    # Prefer labels ("Policy Number:") and headings in capitals, then spread down the page.
    candidates.sort(key=lambda ln: (not ln.text.endswith(":"), not ln.text.isupper()))
    chosen = sorted(candidates[: MAX_ANCHORS * 2], key=lambda ln: ln.nbox[1])
    step = max(1, len(chosen) // MAX_ANCHORS)
    return [
        templates.Anchor(
            text=ln.text.strip(),
            box=(
                round(ln.nbox[0], 3),
                round(ln.nbox[1], 3),
                round(ln.nbox[2], 3),
                round(ln.nbox[3], 3),
            ),
            tol=0.06,
        )
        for ln in chosen[::step][:MAX_ANCHORS]
    ]


def propose(
    session: Session, store: BlobStore, document_id: str, name: str, page_offset: int = 0
) -> templates.Template:
    doc = session.get(Document, document_id)
    if doc is None:
        raise SystemExit(f"No document {document_id}")
    page = session.scalars(
        select(Page)
        .where(Page.document_id == doc.id)
        .order_by(Page.page_no)
        .offset(page_offset)
        .limit(1)
    ).first()
    if page is None:
        raise SystemExit("That document has no such page")
    layout = _layout(session, store, page)
    if layout is None:
        raise SystemExit("That page has no words to learn from")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return templates.Template(
        id=slug,
        name=name,
        doc_type=templates.DocType(doc.doc_type),
        kind="layout",
        status="draft",
        page="first" if page_offset == 0 else "continuation",
        anchors=propose_anchors(layout, [doc.subject_name or "", doc.subject_id or ""]),
        source=f"Proposed from a confirmed {doc.doc_type.replace('_', ' ')}, page {page.page_no}",
    )


def check(session: Session, store: BlobStore) -> dict[str, Counter[str]]:
    """For each template, how many stored pages of each document type it matched."""
    from lane.documents.layouts import all_layouts

    all_templates = all_layouts(session)
    report: dict[str, Counter[str]] = {t.id: Counter() for t in all_templates}
    rows = session.execute(
        select(Page, Document.doc_type).join(Document, Page.document_id == Document.id)
    ).all()
    for page, doc_type in rows:
        if page.kind is PageKind.EMAIL_BODY:
            continue
        layout = _layout(session, store, page)
        if layout is None:
            continue
        text = words_to_text(layout) if "words" in layout else ""
        if "cells" in layout:
            text = "\n".join(templates.sheet_cells(layout))
        for t in all_templates:
            if templates.match_page([t], text=text, layout=layout, sheet_name=page.sheet_name):
                report[t.id][doc_type] += 1
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Make and check layout templates")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("propose", help="suggest a template from a confirmed document")
    p.add_argument("--document", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--page", type=int, default=1, help="1 for a first page, 2+ for a later page")
    sub.add_parser("check", help="run every template against every stored page")
    args = parser.parse_args(argv)
    settings = get_settings()
    store = blob_store(settings)
    with get_sessionmaker()() as session:
        if args.cmd == "propose":
            t = propose(session, store, args.document, args.name, args.page - 1)
            yaml.safe_dump(
                t.model_dump(mode="json", exclude_defaults=True), sys.stdout, sort_keys=False
            )
            return 0
        report = check(session, store)
        from lane.documents.layouts import all_layouts

        by_id = {t.id: t for t in all_layouts(session)}
        clean = True
        for tid, counts in report.items():
            t = by_id[tid]
            wrong = {k: v for k, v in counts.items() if k != t.doc_type.value}
            clean = clean and not wrong
            status = "PRECISE" if not wrong else "MATCHES OTHER TYPES"
            print(f"{status:20} {tid} ({t.status}): {dict(counts) or 'no matches'}")
        return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
