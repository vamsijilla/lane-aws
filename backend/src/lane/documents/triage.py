"""Physical triage: what each page is made of and how readable it is.

Triage answers "how do we read this page" (text layer, scan, photo, worksheet) and "how much
should we trust what we read" (quality band). It does not decide what business document a
page belongs to; that is classification, which runs next and reads content.
"""

import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from PIL import Image

from lane.config import Settings
from lane.db.models import PageKind, QualityBand
from lane.documents import imaging, ocr, pdf, prep
from lane.documents.prep import PrepConfig
from lane.intake.sniff import IMAGE_KINDS, FileKind

TRIAGE_VERSION = "5"  # 5: shadow evening leaves photos and logos alone
# 4: pages are prepared (turned, straightened, flattened) before OCR
# 3  # 3: damaged PDFs are flagged  # 2: OCR word boxes for scans and photos


class TriageError(ValueError):
    """The file could not be opened (encrypted, corrupt, unsupported)."""


@dataclass(frozen=True)
class TriageConfig:
    render_dpi: int
    thumb_width: int
    text_min_chars: int
    scan_image_coverage: float
    photo_min_colorfulness: float
    photo_max_white: float
    sharp_clean: float
    sharp_poor: float
    skew_warn_deg: float
    ocr: bool
    prep: PrepConfig | None  # None: read pages as they came
    ocr_models: str = "PP-OCRv6/small"

    @classmethod
    def from_settings(cls, s: Settings) -> "TriageConfig":
        return cls(
            render_dpi=s.triage_render_dpi,
            thumb_width=s.triage_thumb_width,
            text_min_chars=s.triage_text_min_chars,
            scan_image_coverage=s.triage_scan_image_coverage,
            photo_min_colorfulness=s.triage_photo_min_colorfulness,
            photo_max_white=s.triage_photo_max_white,
            sharp_clean=s.triage_sharp_clean,
            sharp_poor=s.triage_sharp_poor,
            skew_warn_deg=s.triage_skew_warn_deg,
            ocr=s.triage_ocr,
            ocr_models=s.triage_ocr_models,
            prep=PrepConfig(
                deskew_min_deg=s.triage_deskew_min_deg,
                flatten_min_area=s.triage_flatten_min_area,
                shadow_min_range=s.triage_shadow_min_range,
                ocr_min_width=s.triage_ocr_min_width,
            )
            if s.triage_prep
            else None,
        )


@dataclass
class PageResult:
    page_no: int
    kind: PageKind
    quality: QualityBand = QualityBand.CLEAN
    width: float | None = None
    height: float | None = None
    rotation: int = 0
    has_text_layer: bool = False
    text_chars: int = 0
    image_coverage: float = 0.0
    sharpness: float | None = None
    contrast: float | None = None
    colorfulness: float | None = None
    skew_deg: float | None = None
    warnings: list[str] = field(default_factory=lambda: list[str]())
    sheet_name: str | None = None
    sheet_hidden: bool = False
    render_png: bytes | None = None
    thumb_png: bytes | None = None
    layout_json: bytes | None = None
    text_source: str | None = None  # layer | ocr | cells | email
    ocr_confidence: float | None = None
    ocr_cached: bool = False  # the words came from the OCR cache (same file read before)
    prep: list[str] = field(default_factory=lambda: list[str]())  # what preparation did
    hidden: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    original_png: bytes | None = None  # the page as it came, when preparation changed it


@dataclass(frozen=True)
class OcrScope:
    """Where OCR results are cached for one file: keyed by the file's sha256, the page, the OCR
    engine and models, and every setting that changes the image OCR reads."""

    cache: ocr.OcrCache
    file_sha256: str

    def key(self, page_no: int, cfg: "TriageConfig") -> str:
        material = [
            self.file_sha256,
            page_no,
            ocr.ENGINE,
            cfg.ocr_models,
            cfg.render_dpi,
            repr(cfg.prep),
            TRIAGE_VERSION,
        ]
        return hashlib.sha256(json.dumps(material).encode()).hexdigest()


def triage(
    kind: FileKind, data: bytes, cfg: TriageConfig, cache: ocr.OcrCache | None = None
) -> list[PageResult]:
    scope = OcrScope(cache, hashlib.sha256(data).hexdigest()) if cache is not None else None
    if kind is FileKind.PDF:
        return triage_pdf(data, cfg, scope)
    if kind is FileKind.XLSX:
        return triage_xlsx(data)
    if kind in IMAGE_KINDS:
        return [triage_image(data, cfg, scope)]
    if kind is FileKind.TEXT:
        return [triage_text(data)]
    raise TriageError(f"{kind.value} files are not read automatically yet")


# --- PDF ---------------------------------------------------------------------------------


DAMAGED = "The file is damaged; only the part that survived could be read"


def triage_pdf(data: bytes, cfg: TriageConfig, scope: OcrScope | None = None) -> list[PageResult]:
    try:
        results: list[PageResult] = []
        for page in pdf.iter_pages(data, dpi=cfg.render_dpi):
            result = _triage_pdf_page(page, cfg, scope)
            for h in page.hidden:
                result.hidden.append({"text": h.text, "bbox": list(h.bbox), "reason": h.reason})
                result.warnings.append(f"Hidden text removed ({h.reason}): {h.text[:80]!r}")
            if page.damaged:
                result.warnings.insert(0, DAMAGED)
                if result.quality is QualityBand.CLEAN:
                    result.quality = QualityBand.DEGRADED
            results.append(result)
        return results
    except pdf.PdfError as exc:
        raise TriageError(str(exc)) from exc


def _triage_pdf_page(page: pdf.PdfPage, cfg: TriageConfig, scope: OcrScope | None) -> PageResult:
    text_chars = sum(len(w.text) for w in page.words)
    img = Image.open(io.BytesIO(page.png)).convert("RGB")

    has_text = text_chars >= cfg.text_min_chars
    result = PageResult(
        page_no=page.number,
        kind=PageKind.TEXT,
        width=page.width,
        height=page.height,
        rotation=page.rotation,
        has_text_layer=has_text,
        text_chars=text_chars,
        image_coverage=round(page.image_coverage, 3),
        render_png=page.png,
        thumb_png=imaging.thumbnail_png(img, cfg.thumb_width),
    )
    if has_text:
        result.layout_json = _words_layout(page)
        result.text_source = "layer"
        if page.image_coverage < cfg.scan_image_coverage:
            return result
        # A scan carrying someone else's OCR text layer: keep the words, judge the image. The
        # page is not prepared, so those words keep lining up with it.
        result.warnings.append("Image page with an existing text layer; text not verified")
        _assess_image(result, img, cfg)
        return result
    img = _prepare(result, img, cfg)
    if cfg.ocr:
        if result.prep:  # boxes are in the prepared image's pixels
            result.width, result.height = img.width, img.height
            _ocr(result, img, unit="px", scale=1.0, cfg=cfg, scope=scope)
        else:
            _ocr(result, img, unit="pt", scale=page.width / img.width, cfg=cfg, scope=scope)
    return result


def _words_layout(page: pdf.PdfPage) -> bytes:
    """Word boxes in displayed-page points, for grounding and highlights."""
    words = [[w.x0, w.y0, w.x1, w.y1, w.text, w.block, w.line, w.word_no] for w in page.words]
    return _json({"unit": "pt", "width": page.width, "height": page.height, "words": words})


# --- Images ------------------------------------------------------------------------------


def triage_image(data: bytes, cfg: TriageConfig, scope: OcrScope | None = None) -> PageResult:
    try:
        img = imaging.open_image(data)
    except (OSError, ValueError) as exc:
        raise TriageError("Image is corrupt or in an unsupported format") from exc
    result = PageResult(
        page_no=1,
        kind=PageKind.PHOTO,
        width=img.width,
        height=img.height,
        image_coverage=1.0,
        render_png=imaging.to_png(img),
        thumb_png=imaging.thumbnail_png(img, cfg.thumb_width),
    )
    img = _prepare(result, img, cfg)
    result.width, result.height = img.width, img.height
    if cfg.ocr:
        _ocr(result, img, unit="px", scale=1.0, cfg=cfg, scope=scope)
    return result


def _prepare(result: PageResult, img: Image.Image, cfg: TriageConfig) -> Image.Image:
    """Decide scan or photo on the page as it came, prepare it, then judge the prepared page."""
    _kind(result, img, cfg)
    if cfg.prep is None:
        _assess_image(result, img, cfg)
        return img
    p = prep.prepare(img, photo=result.kind is PageKind.PHOTO, cfg=cfg.prep, orient=cfg.ocr)
    if p.changed:
        result.prep = p.steps
        result.original_png = result.render_png
        result.render_png = imaging.to_png(p.image)
        result.thumb_png = imaging.thumbnail_png(p.image, cfg.thumb_width)
    _assess_image(result, p.image, cfg)
    return p.image


def _ocr(
    result: PageResult,
    img: Image.Image,
    *,
    unit: str,
    scale: float,
    cfg: TriageConfig,
    scope: OcrScope | None = None,
) -> None:
    """Word boxes from OCR, in the same units as text-layer pages so grounding treats them alike.
    OCR reads a contrast-tuned, enlarged copy; boxes are scaled back to the page."""
    tuned, factor = prep.for_ocr(img, cfg.prep) if cfg.prep else (img, 1.0)
    scale = scale / factor
    key = scope.key(result.page_no, cfg) if scope else None
    cached = scope.cache.get(key) if scope and key else None
    if cached is not None:
        words = [ocr.OcrWord(*w) for w in cached]
        result.ocr_cached = True
    else:
        words = ocr.read_words(tuned, cfg.ocr_models)
        if scope and key:
            scope.cache.put(
                key, [[w.x0, w.y0, w.x1, w.y1, w.text, w.line, w.word_no, w.score] for w in words]
            )
    if not words:
        result.warnings.append("OCR found no text on this page")
        return
    rows = [
        [
            round(w.x0 * scale, 2),
            round(w.y0 * scale, 2),
            round(w.x1 * scale, 2),
            round(w.y1 * scale, 2),
            w.text,
            0,
            w.line,
            w.word_no,
            round(w.score, 3),
        ]
        for w in words
    ]
    result.layout_json = _json(
        {
            "unit": unit,
            "width": result.width,
            "height": result.height,
            "source": "ocr",
            "words": rows,
        }
    )
    result.text_source = "ocr"
    result.text_chars = sum(len(w.text) for w in words)
    result.ocr_confidence = round(sum(w.score for w in words) / len(words), 3)
    low = sum(1 for w in words if w.score < 0.8)
    if low / len(words) > 0.2:
        result.warnings.append(f"OCR was unsure of {low} of {len(words)} words")


def _kind(result: PageResult, img: Image.Image, cfg: TriageConfig) -> None:
    """A photo (color, or a page with its surroundings) or a scan (a flat page)."""
    g = imaging.gray(img)
    result.colorfulness = round(imaging.colorfulness(img), 1)
    white = imaging.white_ratio(g)
    is_photo = result.colorfulness >= cfg.photo_min_colorfulness or white <= cfg.photo_max_white
    result.kind = PageKind.PHOTO if is_photo else PageKind.SCAN


def _assess_image(result: PageResult, img: Image.Image, cfg: TriageConfig) -> None:
    """Judge the page as it will be read. After preparation, only what it could not fix is
    left to warn about."""
    if result.colorfulness is None:
        _kind(result, img, cfg)
    g = imaging.gray(img)
    result.sharpness = round(imaging.sharpness(g), 1)
    result.contrast = round(imaging.contrast(g), 1)

    if cfg.prep is None and imaging.looks_sideways(img):  # prepared pages were turned by OCR
        result.warnings.append("Text runs vertically; page is probably rotated 90 degrees")
        result.skew_deg = None
    else:
        result.skew_deg = imaging.estimate_skew(img)
        if abs(result.skew_deg) >= cfg.skew_warn_deg:
            result.warnings.append(f"Page is tilted about {abs(result.skew_deg):g} degrees")

    if result.sharpness < cfg.sharp_poor:
        result.quality = QualityBand.POOR
        result.warnings.append("Very blurry; values read from this page need review")
    elif result.sharpness < cfg.sharp_clean or result.warnings:
        result.quality = QualityBand.DEGRADED
        if result.sharpness < cfg.sharp_clean:
            result.warnings.append("Soft focus; small print may be misread")
    else:
        result.quality = QualityBand.CLEAN


# --- Text and spreadsheets ---------------------------------------------------------------


def triage_text(data: bytes) -> PageResult:
    text = data.decode("utf-8", errors="replace")
    return PageResult(
        page_no=1,
        kind=PageKind.EMAIL_BODY,
        has_text_layer=True,
        text_chars=len(text.strip()),
    )


def triage_xlsx(data: bytes) -> list[PageResult]:
    try:
        wb = load_workbook(io.BytesIO(data), data_only=True)
    except Exception as exc:  # openpyxl raises a wide range of errors on bad input
        raise TriageError("Spreadsheet is corrupt and cannot be opened") from exc
    results: list[PageResult] = []
    for index, ws in enumerate(wb.worksheets, start=1):
        grid = _sheet_grid(ws)
        result = PageResult(
            page_no=index,
            kind=PageKind.SHEET,
            has_text_layer=True,
            text_chars=sum(len(str(c["v"])) for c in grid["cells"]),
            sheet_name=ws.title,
            sheet_hidden=ws.sheet_state != "visible",
            layout_json=_json(grid),
        )
        if result.sheet_hidden:
            result.warnings.append(f"Sheet is {ws.sheet_state}; it was not visible to the sender")
        if grid["hidden_rows"] or grid["hidden_cols"]:
            result.warnings.append("Sheet has hidden rows or columns")
        results.append(result)
    return results


def _sheet_grid(ws: Worksheet) -> dict[str, Any]:
    cells: list[dict[str, Any]] = []
    for row in ws.iter_rows():
        for cell in row:
            value = cell.value
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            cells.append(
                {
                    "ref": cell.coordinate,
                    "r": cell.row,
                    "c": cell.column,
                    "v": _cell_value(value),
                    "t": _cell_type(value),
                    "fmt": cell.number_format if cell.number_format != "General" else None,
                }
            )
    return {
        "sheet": ws.title,
        "state": ws.sheet_state,
        "max_row": ws.max_row,
        "max_col": ws.max_column,
        "merged": [str(r) for r in ws.merged_cells.ranges],
        "hidden_rows": sorted(r for r, d in ws.row_dimensions.items() if d.hidden),
        "hidden_cols": sorted(
            k for k, d in ws.column_dimensions.items() if d.hidden and k.isalpha()
        ),
        "col_widths": {
            get_column_letter(i): ws.column_dimensions[get_column_letter(i)].width
            for i in range(1, ws.max_column + 1)
        },
        "images": len(getattr(ws, "_images", [])),
        "cells": cells,
    }


def _cell_value(value: object) -> str | int | float | bool:
    if isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    return str(value)


def _cell_type(value: object) -> str:
    if isinstance(value, bool):
        return "b"
    if isinstance(value, int | float):
        return "n"
    if isinstance(value, datetime | date | time):
        return "d"
    return "s"


def _json(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
