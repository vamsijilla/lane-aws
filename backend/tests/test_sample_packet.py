"""P1 gate on the real sample packet: one email, 15 files plus the same 15 inside a zip."""

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lane.audit import chain
from lane.config import get_settings
from lane.db.models import (
    Attachment,
    AttachmentStatus,
    Blob,
    Page,
    PageKind,
    QualityBand,
    RunStatus,
    Submission,
)
from lane.pipeline.registry import PIPELINE
from lane.pipeline.runner import process_submission
from lane.storage.blobs import BlobStore
from tests.test_intake import ingest

REPO = Path(__file__).resolve().parents[2]


def _builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "make_sample_eml", REPO / "scripts/make_sample_eml.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sample_packet_ingests_and_dedupes(db: Session, store: BlobStore) -> None:
    raw = _builder().build(REPO / "data/packet_cobalt_mesa", with_zip=True)

    r = ingest(db, store, raw)
    assert r.created
    assert (r.files, r.unique_files, r.duplicates, r.expanded_archives, r.rejected) == (
        32,  # body + 15 attachments + zip + 15 zip members
        16,  # body + 15 distinct files
        15,
        1,
        0,
    )
    assert r.unsupported == 0

    kinds = dict(
        db.execute(
            select(Blob.kind, func.count())
            .join(Attachment, Attachment.blob_sha256 == Blob.sha256)
            .where(Attachment.status == AttachmentStatus.ACCEPTED)
            .group_by(Blob.kind)
        ).all()
    )
    assert kinds == {"pdf": 12, "xlsx": 3, "text": 1}

    again = ingest(db, store, _builder().build(REPO / "data/packet_cobalt_mesa", with_zip=True))
    assert not again.created
    assert db.scalar(select(func.count()).select_from(Submission)) == 1
    assert chain.verify(db).ok


def test_sample_packet_triage_matches_the_physical_ground_truth(
    db: Session, store: BlobStore
) -> None:
    """P2 gate: IFTA returns are scans, two license photos sit inside the MVR PDF, digital
    PDFs keep a text layer, and the workbooks become worksheets with a hidden lookup sheet."""
    raw = _builder().build(REPO / "data/packet_cobalt_mesa", with_zip=True)
    sub_id = ingest(db, store, raw).submission_id
    run = process_submission(db, store, get_settings(), sub_id, steps=PIPELINE[:1], trigger="test")
    assert run.status is RunStatus.SUCCEEDED

    rows = db.execute(
        select(Attachment.filename, Page.page_no, Page.kind, Page.quality, Page.sheet_hidden)
        .join(Attachment, Page.attachment_id == Attachment.id)
        .where(Page.submission_id == sub_id)
    ).all()
    by_file: dict[str, list[tuple[int, PageKind, QualityBand, bool]]] = {}
    for filename, page_no, kind, quality, hidden in rows:
        by_file.setdefault(filename, []).append((page_no, kind, quality, hidden))

    assert len(rows) == 34
    for quarter in ("Q3 2025.pdf", "Q4 2025.pdf", "Q1 2026.pdf", "Q2 2026.pdf"):
        assert {k for _, k, _, _ in by_file[quarter]} == {PageKind.SCAN}
    mvr = {n: k for n, k, _, _ in by_file["MVR REPORT - COBALT MESA TRANSPORT INC.pdf"]}
    assert len(mvr) == 10
    assert [n for n, k in sorted(mvr.items()) if k is PageKind.PHOTO] == [7, 10]
    assert {k for n, k in mvr.items() if n not in (7, 10)} == {PageKind.TEXT}
    for name in ("AL 24-25.pdf", "MTC 23-24.pdf", "COBALT MESA TRANSPORT INC - QUOTE FORM.pdf"):
        assert by_file[name] == [(1, PageKind.TEXT, QualityBand.CLEAN, False)]
    app = by_file["COBALT MESA TRANSPORT INC - KNIGHT.xlsx"]
    assert [(k, hidden) for _, k, _, hidden in sorted(app)] == [
        (PageKind.SHEET, False),
        (PageKind.SHEET, False),
        (PageKind.SHEET, True),
    ]
