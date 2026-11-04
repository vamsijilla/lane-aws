"""The code read on scanned and photographed pages is OCR, so it is only as good as OCR's
confidence in the words it read.

- A value verifies only when the words code matched meet the threshold (every word for an
  identifier, their mean otherwise). Below it, the spot is unreadable: neither a confirmation
  nor a disagreement.
- A decision value that did not verify is read again at its anchor: the band of the page around
  it (or around its label or its record's row) is cropped, enlarged and read by OCR again, and
  code reads the value from that crop.
"""

import io
from dataclasses import dataclass, replace
from typing import Any

from PIL import Image

from lane.documents import ocr
from lane.extract.verify import (
    Anchor,
    Page,
    Want,
    _find_seq,  # pyright: ignore[reportPrivateUsage]
    _union,  # pyright: ignore[reportPrivateUsage]
    anchor_score,
    locate,
    quoted_label,
    record_rows,
    tokens,
)


@dataclass(frozen=True)
class OcrCheck:
    min_score: float = 0.85
    models: str = ocr.DEFAULT_MODELS
    reread_scale: float = 2.5


@dataclass(frozen=True)
class Spot:
    """Where to read again: the page image and its layout width (page units)."""

    image_png: bytes
    width: float


def gate(page: Page, anchor: Anchor, want: Want, cfg: OcrCheck) -> Anchor:
    """Apply OCR's word confidence to what code read at the anchor."""
    score = anchor_score(page, anchor, want)
    if score is None:
        return anchor
    if score >= cfg.min_score:
        return replace(anchor, score=score)
    if anchor.mismatch:  # a shaky read of something else: unreadable, not a disagreement
        return replace(anchor, mismatch=False, unsure=True, score=score)
    if anchor.verified:
        return replace(anchor, unsure=True, score=score)
    return replace(anchor, score=score)


def _region(
    page: Page,
    anchor: Anchor,
    want: Want,
    quote: str | None,
    row_quote: str | None,
    next_quote: str | None,
) -> tuple[float, float, float, float] | None:
    if anchor.bbox is not None:
        return anchor.bbox
    for label in (quoted_label(quote), *want.labels):
        if label and (hits := _find_seq(page, tokens(label))):
            return _union(hits[0])
    if row_quote and (rows := record_rows(page, row_quote, next_quote)) is not None:
        words = [w for r in rows for w in page.rows[r]]
        if words:
            return _union(words)
    return None


def _crop_page(spot: Spot, box: tuple[float, float, float, float], cfg: OcrCheck) -> Page | None:
    img = Image.open(io.BytesIO(spot.image_png)).convert("RGB")
    k = img.width / spot.width  # image pixels per page unit
    _, y0, _, y1 = box  # a full-width band: the label may sit far to the left
    h = max(y1 - y0, 10.0)
    top, bottom = max(0.0, y0 - 2 * h), min(img.height / k, y1 + 2 * h)
    crop = img.crop((0, round(top * k), img.width, round(bottom * k)))
    if crop.height < 4:
        return None
    s = cfg.reread_scale
    big = crop.resize((round(crop.width * s), round(crop.height * s)), Image.Resampling.LANCZOS)
    words = ocr.read_words(big, cfg.models)
    rows: list[list[Any]] = [
        [
            w.x0 / s / k,
            w.y0 / s / k + top,
            w.x1 / s / k,
            w.y1 / s / k + top,
            w.text,
            0,
            w.line,
            w.word_no,
            w.score,
        ]
        for w in words
    ]
    return Page.from_layout({"words": rows}, ocr=True)


def reread(
    page: Page,
    anchor: Anchor,
    want: Want,
    spot: Spot,
    cfg: OcrCheck,
    *,
    quote: str | None,
    row_quote: str | None,
    next_quote: str | None,
    column: str | None,
) -> Anchor:
    """Read the value again from an enlarged crop of its spot; keep it only if that read is sure."""
    box = _region(page, anchor, want, quote, row_quote, next_quote)
    if box is None:
        return anchor
    sub = _crop_page(spot, box, cfg)
    if sub is None:
        return anchor
    got = gate(
        sub,
        locate(sub, want, row_quote=row_quote, next_quote=next_quote, column=column, quote=quote),
        want,
        cfg,
    )
    if got.verified or (got.mismatch and not got.unsure):
        return replace(got, reread=True)
    return anchor
