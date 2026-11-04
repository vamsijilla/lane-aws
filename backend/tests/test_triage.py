import json
from dataclasses import replace

import pytest

from lane.config import get_settings
from lane.db.models import PageKind, QualityBand
from lane.documents.triage import TriageConfig, TriageError, triage
from lane.intake.sniff import FileKind
from tests.docs_factory import (
    encrypted_pdf,
    image_pdf,
    jpeg_bytes,
    text_image,
    text_pdf,
    workbook,
)


@pytest.fixture
def cfg() -> TriageConfig:
    return TriageConfig.from_settings(get_settings())


def test_digital_pdf_pages_keep_their_words(cfg: TriageConfig) -> None:
    pages = triage(FileKind.PDF, text_pdf(pages=2), cfg)
    assert [(p.page_no, p.kind, p.quality) for p in pages] == [
        (1, PageKind.TEXT, QualityBand.CLEAN),
        (2, PageKind.TEXT, QualityBand.CLEAN),
    ]
    assert pages[0].render_png and pages[0].thumb_png
    layout = json.loads(pages[1].layout_json or b"{}")
    assert layout["unit"] == "pt" and layout["width"] == pytest.approx(595, abs=1)
    words = [w[4] for w in layout["words"]]
    assert "Jurisdiction" in words and "p2" in words
    x0, y0, x1, y1 = layout["words"][0][:4]
    assert 0 <= x0 < x1 and 0 <= y0 < y1


def test_scanned_page_is_a_clean_scan(cfg: TriageConfig) -> None:
    [page] = triage(FileKind.PDF, image_pdf(text_image()), cfg)
    assert page.kind is PageKind.SCAN
    assert page.has_text_layer is False and page.image_coverage > 0.9
    assert page.quality is QualityBand.CLEAN
    assert page.layout_json is None


def test_blurry_scan_is_poor(cfg: TriageConfig) -> None:
    [page] = triage(FileKind.PDF, image_pdf(text_image(blur=3)), cfg)
    assert page.quality is QualityBand.POOR
    assert any("blurry" in w for w in page.warnings)


def test_a_tilted_scan_is_straightened_and_says_so(cfg: TriageConfig) -> None:
    [page] = triage(FileKind.PDF, image_pdf(text_image(angle=3)), cfg)
    assert page.prep == ["Straightened a 3 degree tilt"]
    assert page.original_png and not any("tilted" in w for w in page.warnings)


def test_without_preparation_a_tilted_scan_is_degraded(cfg: TriageConfig) -> None:
    [page] = triage(FileKind.PDF, image_pdf(text_image(angle=3)), replace(cfg, prep=None))
    assert page.quality is QualityBand.DEGRADED
    assert any("tilted" in w for w in page.warnings)


def test_photo_of_a_card_is_a_photo(cfg: TriageConfig) -> None:
    [page] = triage(FileKind.JPEG, jpeg_bytes(text_image(color=True)), cfg)
    assert page.kind is PageKind.PHOTO
    assert page.render_png and page.render_png.startswith(b"\x89PNG")


def test_workbook_sheets_keep_cell_refs_and_hidden_state(cfg: TriageConfig) -> None:
    drivers, lookups = triage(FileKind.XLSX, workbook(), cfg)
    assert drivers.kind is PageKind.SHEET and drivers.sheet_name == "Drivers"
    grid = json.loads(drivers.layout_json or b"{}")
    cells = {c["ref"]: c for c in grid["cells"]}
    assert cells["A2"]["v"] == "Pat Example"
    assert cells["B2"] == {
        "ref": "B2",
        "r": 2,
        "c": 2,
        "v": "1980-01-02T00:00:00",
        "t": "d",
        "fmt": "yyyy-mm-dd",
    }
    assert cells["D2"]["t"] == "n"
    assert grid["merged"] == ["F1:G1"]
    assert grid["hidden_cols"] == ["H"]
    assert "Sheet has hidden rows or columns" in drivers.warnings
    assert lookups.sheet_hidden and lookups.warnings == [
        "Sheet is hidden; it was not visible to the sender"
    ]


def test_text_is_one_page(cfg: TriageConfig) -> None:
    [page] = triage(FileKind.TEXT, b"FEIN 12-3456789", cfg)
    assert page.kind is PageKind.EMAIL_BODY and page.text_chars == 15


@pytest.mark.parametrize(
    ("kind", "data", "reason"),
    [
        (FileKind.PDF, encrypted_pdf(), "password protected"),
        (FileKind.PDF, b"%PDF-1.7 truncated garbage", "corrupt"),
        (FileKind.XLSX, b"PK\x03\x04 nope", "corrupt"),
        (FileKind.PNG, b"\x89PNG\r\n\x1a\n broken", "corrupt"),
        (FileKind.DOCX, b"irrelevant", "not read automatically"),
    ],
)
def test_unreadable_files_raise_with_a_reason(
    cfg: TriageConfig, kind: FileKind, data: bytes, reason: str
) -> None:
    with pytest.raises(TriageError, match=reason):
        triage(kind, data, cfg)
