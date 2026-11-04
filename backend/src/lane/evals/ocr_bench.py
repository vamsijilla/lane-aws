"""OCR benchmark: re-read every scanned or photographed page with a choice of OCR models and
measure what it buys, without a model call.

    uv run python -m lane.evals.ocr_bench --db ../var/lane.db --models PP-OCRv6/small PP-OCRv6/medium

Per model choice: OCR seconds, mean word confidence, and how many stored decision values the
code read verifies against the new words (lane.evals.verify_offline with the layouts replaced).
`--original` also reads each photo as it was sent, before page preparation.
"""

import argparse
import io
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from lane.config import get_settings
from lane.db.base import sqlite_engine
from lane.db.models import Blob, Document, Page, PageKind
from lane.documents import ocr, prep
from lane.documents.layout import load_layout
from lane.documents.triage import TriageConfig
from lane.evals.verify_offline import Report, check_document
from lane.extract.ocr_check import OcrCheck
from lane.storage.blobs import BlobStore, blob_store


@dataclass
class Result:
    models: str
    seconds: float = 0.0
    words: int = 0
    score_sum: float = 0.0
    decision: int = 0
    verified: int = 0
    reread: int = 0  # verified only by reading the spot again, enlarged
    unreadable: int = 0  # OCR too unsure at the spot to confirm or contradict
    per_page: list[tuple[str, float]] = field(default_factory=lambda: list[tuple[str, float]]())

    @property
    def mean(self) -> float:
        return self.score_sum / self.words if self.words else 0.0


def _image(session: Session, store: BlobStore, sha: str | None) -> Image.Image | None:
    blob = session.get(Blob, sha) if sha else None
    return Image.open(io.BytesIO(store.get(blob.storage_key))).convert("RGB") if blob else None


def layout_for(
    img: Image.Image, stored: dict[str, Any], models: str, cfg: TriageConfig
) -> tuple[dict[str, Any], list[ocr.OcrWord]]:
    """The page's words as triage would store them, read with `models`."""
    tuned, factor = prep.for_ocr(img, cfg.prep) if cfg.prep else (img, 1.0)
    scale = float(stored["width"]) / img.width / factor
    words = ocr.read_words(tuned, models)
    rows = [
        [
            w.x0 * scale,
            w.y0 * scale,
            w.x1 * scale,
            w.y1 * scale,
            w.text,
            0,
            w.line,
            w.word_no,
            w.score,
        ]
        for w in words
    ]
    return {**stored, "words": rows}, words


def bench(
    db: Path,
    models: str,
    submissions: list[str] | None,
    *,
    original: bool = False,
    check: OcrCheck | None = None,
    doc_types: list[str] | None = None,
) -> Result:
    engine = sqlite_engine(f"sqlite:///{db}")
    store = blob_store(get_settings())
    cfg = TriageConfig.from_settings(get_settings())
    r = Result(models + (" (photo as sent)" if original else ""))
    with sessionmaker(engine)() as session:
        q = (
            select(Page, Document)
            .join(Document, Document.id == Page.document_id)
            .where(Page.kind.in_((PageKind.SCAN, PageKind.PHOTO)))
        )
        if submissions:
            q = q.where(Document.submission_id.in_(submissions))
        if doc_types:
            q = q.where(Document.doc_type.in_(doc_types))
        layouts: dict[str, Any] = {}
        docs: dict[str, Document] = {}
        for page, doc in session.execute(q.order_by(Document.ordinal, Page.page_no)):
            sha = page.original_sha256 if original and page.original_sha256 else page.render_sha256
            img = _image(session, store, sha)
            stored_blob = session.get(Blob, page.layout_sha256) if page.layout_sha256 else None
            if img is None or stored_blob is None:
                continue
            stored = load_layout(store.get(stored_blob.storage_key))
            start = time.perf_counter()
            layout, words = layout_for(img, stored, models, cfg)
            r.seconds += time.perf_counter() - start
            r.words += len(words)
            r.score_sum += sum(w.score for w in words)
            r.per_page.append(
                (f"{doc.title} p{page.page_no}", sum(w.score for w in words) / max(1, len(words)))
            )
            layouts[page.id] = layout
            docs[doc.id] = doc
        report = Report()
        for doc in docs.values():
            check_document(
                session,
                store,
                doc,
                report,
                layouts,
                replace(check, models=models) if check else None,
            )
        for v in report.values:
            if v.tier == "decision" and v.method == "vision":
                r.decision += 1
                r.verified += int(v.status == "verified")
                r.reread += int("re-read at anchor" in v.how)
                r.unreadable += int("unreadable" in v.how)
    engine.dispose()
    return r


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", default=str(Path(__file__).resolve().parents[4] / "var" / "lane.db")
    )
    parser.add_argument("--submission", action="append")
    parser.add_argument("--models", nargs="+", default=[ocr.DEFAULT_MODELS])
    parser.add_argument("--original", action="store_true")
    parser.add_argument("--pages", action="store_true")
    parser.add_argument("--doc-type", action="append", help="only documents of this type")
    parser.add_argument(
        "--check",
        action="store_true",
        help="apply the word-confidence gate and the re-read at the anchor (extraction v2 does)",
    )
    args = parser.parse_args(argv)
    s = get_settings()
    check = (
        OcrCheck(s.triage_ocr_min_word_score, s.triage_ocr_models, s.triage_ocr_reread_scale)
        if args.check
        else None
    )
    print(
        "| Models | OCR seconds | Words | Mean word confidence | Decision values verified "
        "| Of those, by re-read | Unreadable spots |"
    )
    print("|---|---|---|---|---|---|---|")
    for m in args.models:
        for orig in [False, True] if args.original else [False]:
            r = bench(
                Path(args.db),
                m,
                args.submission,
                original=orig,
                check=check,
                doc_types=args.doc_type,
            )
            print(
                f"| {r.models} | {r.seconds:.1f} | {r.words} | {r.mean:.3f} | {r.verified} of "
                f"{r.decision} | {r.reread} | {r.unreadable} |"
            )
            if args.pages:
                for name, mean in r.per_page:
                    print(f"|   {name} | | | {mean:.3f} | |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
