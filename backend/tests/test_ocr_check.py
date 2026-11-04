"""On images the code read is OCR: it counts only when OCR is sure of the words, and a decision
value that did not verify is read again from its spot, enlarged."""

from typing import Any

from lane.documents.imaging import to_png
from lane.extract.ocr_check import OcrCheck, Spot, gate, reread
from lane.extract.verify import Page, Want, locate
from tests.docs_factory import text_image

CFG = OcrCheck(min_score=0.85)


def ocr_page(rows: list[list[tuple[str, float, float]]], top: float = 20, h: float = 10) -> Page:
    """Rows of (word, x, OCR score); each row 1.4 heights below the last."""
    words: list[list[Any]] = []
    for r, row in enumerate(rows):
        for i, (text, x, score) in enumerate(row):
            y = top + r * h * 1.4
            words.append([x, y, x + 0.7 * h * len(text), y + h, text, 0, r, i, score])
    page = Page.from_layout({"words": words}, ocr=True)
    assert page is not None
    return page


def test_a_sure_read_verifies_and_carries_its_score() -> None:
    p = ocr_page([[("DOT:", 10, 0.99), ("3481107", 60, 0.97)]])
    want = Want("identifier", "3481107", ("DOT",))
    got = gate(p, locate(p, want), want, CFG)
    assert got.verified and got.score == 0.97


def test_an_unsure_read_does_not_verify() -> None:
    p = ocr_page([[("DOT:", 10, 0.99), ("3481107", 60, 0.62)]])
    want = Want("identifier", "3481107", ("DOT",))
    got = gate(p, locate(p, want), want, CFG)
    assert not got.verified and got.unsure and got.score == 0.62


def test_an_unsure_read_of_something_else_is_unreadable_not_a_disagreement() -> None:
    p = ocr_page([[("DOT:", 10, 0.99), ("3481101", 60, 0.55)]])
    want = Want("identifier", "3481107", ("DOT",))
    first = locate(p, want)
    assert first.mismatch
    got = gate(p, first, want, CFG)
    assert not got.mismatch and got.unsure


def test_an_identifier_needs_every_word_sure() -> None:
    p = ocr_page([[("VIN:", 10, 0.99), ("1FUJ", 60, 0.99), ("GLDR", 100, 0.70)]])
    want = Want("identifier", "1FUJ GLDR", ("VIN",))
    assert gate(p, locate(p, want), want, CFG).unsure


def test_a_value_is_read_again_at_its_anchor() -> None:
    img = text_image(lines=["Policy Number: GAL7102318", "Run Date: 10/06/2026"])
    # The page's first OCR was unsure of the number: the code read did not count
    p = ocr_page(
        [[("Policy", 120, 0.99), ("Number:", 220, 0.99), ("GAL7102318", 360, 0.40)]], top=120, h=30
    )
    want = Want("identifier", "GAL7102318", ("Policy Number",))
    first = gate(p, locate(p, want), want, CFG)
    assert not first.verified
    spot = Spot(to_png(img), float(img.width))  # page units are image pixels here
    got = reread(
        p, first, want, spot, CFG, quote=None, row_quote=None, next_quote=None, column=None
    )
    assert got.verified and got.reread and got.score is not None and got.score >= 0.85


def test_poor_pages_and_license_photos_go_to_the_stronger_model_first() -> None:
    from types import SimpleNamespace

    from lane.config import Settings
    from lane.db.models import PageKind, QualityBand
    from lane.extract.service import reads_strong_first

    def item(doc_type: str, kind: PageKind, quality: QualityBand) -> Any:
        page = SimpleNamespace(page=SimpleNamespace(kind=kind, quality=quality))
        return SimpleNamespace(
            doc=SimpleNamespace(doc_type=doc_type),
            pages=[page],
            is_image=kind in (PageKind.SCAN, PageKind.PHOTO),
        )

    s = Settings()
    assert reads_strong_first(item("drivers_license", PageKind.PHOTO, QualityBand.CLEAN), s)
    assert reads_strong_first(item("quote_form", PageKind.PHOTO, QualityBand.POOR), s)
    assert not reads_strong_first(item("quote_form", PageKind.SCAN, QualityBand.CLEAN), s)
    assert not reads_strong_first(item("drivers_license", PageKind.TEXT, QualityBand.CLEAN), s)
