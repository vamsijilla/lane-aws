"""Run v2 verification over stored extractions, without any model call.

    uv run python -m lane.evals.verify_offline [--db PATH] [--submission ID] [--json OUT]

For every stored value the field catalog keeps, find where it sits on its page (its label, its
row, or its cell) and read it again in code; run the catalog's validators and the recall checks.
Reports, by tier: verified, disagreed (code read a different value beside the label), only found
on the page, not found; plus failed validators and recall checks, each with its evidence.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from lane.config import get_settings
from lane.db.base import sqlite_engine
from lane.db.models import Blob, Document, DocumentExtraction, Page, PageKind, Submission
from lane.documents.layout import load_layout
from lane.extract.catalog import column_key, entry_for
from lane.extract.engine import _walk  # pyright: ignore[reportPrivateUsage]
from lane.extract.engine_v2 import PLACEHOLDER
from lane.extract.ocr_check import OcrCheck, Spot, gate, reread
from lane.extract.validators import recall_checks, run_validators
from lane.extract.verify import (  # pyright: ignore[reportPrivateUsage]
    Anchor,
    _find_seq,  # pyright: ignore[reportPrivateUsage]
    locate,
    next_quote,
    record_value,
    tokens,
    want_for,
)
from lane.extract.verify import Page as VPage
from lane.storage.blobs import BlobStore, blob_store


@dataclass
class ValueResult:
    document: str
    doc_type: str
    path: str
    tier: str
    value: str
    quote: str | None
    status: str  # verified | disagreed | page_only | not_found
    how: str
    code_read: str | None = None
    folding: str | None = None
    ambiguous: bool = False
    method: str = "text"  # text | vision | sheet
    on_page: bool = True  # the value's words are printed somewhere in the document


@dataclass
class Report:
    values: list[ValueResult] = field(default_factory=lambda: list[ValueResult]())
    dropped: int = 0  # stored values the catalog does not keep
    validators: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    recall: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())

    def counts(self) -> dict[str, dict[str, int]]:
        out: dict[str, Counter[str]] = defaultdict(Counter)
        for v in self.values:
            out[v.tier][v.status] += 1
        return {k: dict(c) for k, c in out.items()}


def _pages(
    session: Session, store: BlobStore, doc: Document, layouts: dict[str, Any] | None = None
) -> dict[int, tuple[Page, VPage | None]]:
    out: dict[int, tuple[Page, VPage | None]] = {}
    for p in session.scalars(select(Page).where(Page.document_id == doc.id).order_by(Page.page_no)):
        layout = (layouts or {}).get(p.id)
        if layout is None and p.layout_sha256 and (b := session.get(Blob, p.layout_sha256)):
            layout = load_layout(store.get(b.storage_key))
        vp = VPage.from_layout(layout, ocr=p.kind in (PageKind.SCAN, PageKind.PHOTO))
        out[p.page_no] = (p, vp)
    return out


def _email_page(session: Session, store: BlobStore, doc: Document) -> VPage:
    """The email body as a page of rows (one per line), so labels anchor the same way."""
    from lane.db.models import EmailMessage
    from lane.intake.eml import parse_eml

    sub = session.get(Submission, doc.submission_id)
    email = session.get(EmailMessage, sub.email_id) if sub else None
    blob = session.get(Blob, email.raw_sha256) if email else None
    text = parse_eml(store.get(blob.storage_key)).body_text if blob else ""
    return VPage.from_text(text)


def check_document(
    session: Session,
    store: BlobStore,
    doc: Document,
    report: Report,
    layouts: dict[str, Any] | None = None,
    ocr_cfg: OcrCheck | None = None,
) -> None:
    """`layouts` replaces stored page layouts (page id -> layout), to measure another OCR."""
    x = session.get(DocumentExtraction, doc.id)
    if x is None or not x.data:
        return
    data: dict[str, Any] = x.data
    pages = _pages(session, store, doc, layouts)
    is_sheet = bool(pages) and all(p.kind is PageKind.SHEET for p, _ in pages.values())
    email = _email_page(session, store, doc) if doc.doc_type == "email_body" else None
    spots: dict[int, Spot] = {}
    if ocr_cfg is not None:
        for p, vp in pages.values():
            blob = session.get(Blob, p.render_sha256) if p.render_sha256 else None
            width = (layouts or {}).get(p.id, {}).get("width") if layouts else None
            if width is None and p.layout_sha256 and (lb := session.get(Blob, p.layout_sha256)):
                width = load_layout(store.get(lb.storage_key)).get("width")
            if vp is not None and blob is not None and width:
                spots[id(vp)] = Spot(store.get(blob.storage_key), float(width))
    from lane.db.models import Fact

    facts = list(session.scalars(select(Fact).where(Fact.document_id == doc.id)))
    if is_sheet:
        for f in facts:
            e = entry_for(doc.doc_type, f.path)
            if e is None or not e.is_fact:
                report.dropped += 1
                continue
            report.values.append(
                ValueResult(
                    doc.title,
                    doc.doc_type,
                    f.path,
                    e.tier,
                    str(f.value_raw),
                    f.quote,
                    "verified",
                    f"cell {f.cell_ref}",
                    method="sheet",
                )
            )
    else:
        for path, value, page_no, quote in _walk(data, "", None, None):
            e = entry_for(doc.doc_type, path)
            if e is None or not e.is_fact:
                report.dropped += 1
                continue
            if PLACEHOLDER.fullmatch(value.strip()):
                continue  # "-" printed for nothing: not a value (as in v2)
            vp = email if email is not None else None
            candidates: list[VPage] = []
            if vp is not None:
                candidates = [vp]
            else:
                order = [page_no] if page_no in pages else []
                order += [n for n in pages if n not in order]
                candidates = [p for n in order if (p := pages[n][1]) is not None]
            row_quote = quote if "[]" in e.path else None
            want = want_for(e.spec, value)
            header = record_value(data, path, column_key(doc.doc_type, path))
            best: Anchor = Anchor("none")
            for vpage in candidates:
                got = locate(
                    vpage,
                    want,
                    row_quote=row_quote,
                    next_quote=next_quote(data, path),
                    column=header,
                    quote=quote,
                )
                if ocr_cfg is not None and vpage.ocr:
                    got = gate(vpage, got, want, ocr_cfg)
                    spot = spots.get(id(vpage))
                    if e.tier == "decision" and not got.verified and spot is not None:
                        got = reread(
                            vpage,
                            got,
                            want,
                            spot,
                            ocr_cfg,
                            quote=quote,
                            row_quote=row_quote,
                            next_quote=next_quote(data, path),
                            column=header,
                        )
                if got.verified:
                    best = got
                    break
                if (got.mismatch or got.unsure or got.kind == "page") and best.kind == "none":
                    best = got
            if best.verified:
                status = "verified"
                how = f"{best.kind}" + (f" '{best.label}' {best.where}" if best.label else "")
                how += ", re-read at anchor" if best.reread else ""
            elif best.unsure:
                status = "not_found"
                how = f"unreadable at that spot (OCR {best.score:.2f})"
            elif best.mismatch:
                status = "disagreed"
                how = f"label '{best.label}'"
            elif best.kind == "page":
                status = "page_only"
                how = "on the page, not beside its label"
            else:
                status = "not_found"
                how = "not found beside a label, in its row, or on the page"
            seq = tokens(value)
            printed = any(_find_seq(p, seq) for p in candidates) if seq else False
            report.values.append(
                ValueResult(
                    doc.title,
                    doc.doc_type,
                    path,
                    e.tier,
                    value,
                    quote,
                    status,
                    how,
                    best.read,
                    best.folding,
                    best.ambiguous,
                    method="vision" if any(p.ocr for p in candidates) else "text",
                    on_page=printed,
                )
            )
    today = doc.document_date and None
    from datetime import date

    for c in run_validators(doc.doc_type, data, today or date.today()):
        if not c.passed:
            report.validators.append({"document": doc.title, **c.as_dict()})
    text = "\n".join(vp.text() for _, vp in pages.values() if vp is not None)
    sheet_rows = None
    for c in recall_checks(doc.doc_type, data, text, sheet_rows=sheet_rows):
        report.recall.append({"document": doc.title, **c.as_dict()})


def migrated_copy(db: Path) -> Path:
    """A copy of a database brought to the current schema, so an older run (the frozen v1
    baseline) is read through today's models without changing the original."""
    import os
    import sqlite3
    import subprocess
    import tempfile
    from contextlib import closing

    out = Path(tempfile.mkdtemp(prefix="lane-baseline-")) / db.name
    with closing(sqlite3.connect(db)) as src, closing(sqlite3.connect(out)) as dst:
        src.backup(dst)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[3],
        env={**os.environ, "LANE_DATABASE_URL": f"sqlite:///{out}"},
        check=True,
        capture_output=True,
    )
    return out


def run(db_url: str, submission_id: str | None) -> Report:
    if db_url.startswith("sqlite:///"):
        db_url = f"sqlite:///{migrated_copy(Path(db_url.removeprefix('sqlite:///')))}"
    engine = sqlite_engine(db_url)
    store = blob_store(get_settings())
    report = Report()
    with sessionmaker(engine)() as session:
        sid = submission_id or session.scalars(select(Submission.id)).first()
        for doc in session.scalars(
            select(Document).where(Document.submission_id == sid).order_by(Document.ordinal)
        ):
            check_document(session, store, doc, report)
    engine.dispose()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", default=str(Path(__file__).resolve().parents[4] / "evals" / "baseline" / "v1.db")
    )
    parser.add_argument("--submission")
    parser.add_argument("--json")
    args = parser.parse_args(argv)
    report = run(f"sqlite:///{args.db}", args.submission)
    print("Values the catalog keeps, by tier and status:")
    for tier, c in sorted(report.counts().items()):
        print(f"  {tier:<11} {c}")
    print(f"Stored values the catalog drops: {report.dropped}")
    print(f"Failed validators: {len(report.validators)}")
    for v in report.validators:
        print(f"  {v['document']}: {v['detail']}")
    print("Recall checks:")
    for r in report.recall:
        print(f"  {'ok  ' if r['passed'] else 'FAIL'} {r['document']}: {r['detail']}")
    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "counts": report.counts(),
                    "dropped": report.dropped,
                    "values": [asdict(v) for v in report.values],
                    "validators": report.validators,
                    "recall": report.recall,
                },
                indent=1,
                default=str,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
