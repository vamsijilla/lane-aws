"""Typed adapter over PyMuPDF.

PyMuPDF ships without complete type information, so its calls are confined to this module
(the only place with relaxed checks) and everything else works with the typed values below.
Swapping the PDF engine (e.g. pypdfium2 for licensing) means rewriting only this file.
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportUnknownParameterType=false

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pymupdf


class PdfError(ValueError):
    pass


@dataclass(frozen=True)
class Word:
    """A word on the displayed page, in points from the top-left corner."""

    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    block: int
    line: int
    word_no: int


@dataclass(frozen=True)
class HiddenSpan:
    """Text a reader cannot see: never read, logged as a security finding."""

    text: str
    bbox: tuple[float, float, float, float]
    reason: str  # invisible render mode | fully transparent | tiny font | white on white


@dataclass(frozen=True)
class PdfPage:
    number: int  # 1-based
    width: float
    height: float
    rotation: int
    words: list[Word]
    image_coverage: float
    png: bytes  # rendered while the document is open; no live handle escapes this module
    damaged: bool = False  # the file was broken and MuPDF repaired what it could
    hidden: tuple[HiddenSpan, ...] = ()  # dropped from `words` before anything reads them


def iter_pages(data: bytes, *, dpi: int) -> Iterator[PdfPage]:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise PdfError("PDF is corrupt and cannot be opened") from exc
    with doc:
        if doc.needs_pass:
            raise PdfError("PDF is password protected")
        if doc.page_count == 0:
            raise PdfError("PDF has no pages")
        if doc.is_repaired and not _has_content(doc):
            # A damaged file can still "open": MuPDF repairs what it can. If nothing readable
            # survives, say the file is broken rather than showing a blank page.
            raise PdfError("PDF is damaged and nothing in it could be read")
        for index in range(doc.page_count):
            page = doc[index]
            hidden = _hidden(page)
            yield PdfPage(
                number=index + 1,
                width=float(page.rect.width),
                height=float(page.rect.height),
                rotation=int(page.rotation),
                words=_visible(_words(page), hidden),
                hidden=tuple(hidden),
                image_coverage=_image_coverage(page),
                png=bytes(page.get_pixmap(dpi=dpi).tobytes("png")),
                damaged=bool(doc.is_repaired),
            )


TINY_FONT_PT = 2.0
WHITE = 0.95


def _hidden(page: Any) -> list[HiddenSpan]:
    """Spans drawn so a reader cannot see them: invisible render mode, fully transparent,
    a tiny font, or white text on a white background. Instructions hidden this way are a known
    way to steer a model; Lane never reads them."""
    try:
        traces = page.get_texttrace()
    except Exception:  # an old or broken file: nothing is dropped
        return []
    matrix = page.rotation_matrix
    out: list[HiddenSpan] = []
    for t in traces:
        text = "".join(chr(c[0]) for c in t.get("chars", ()) if c[0] >= 32).strip()
        if not text:
            continue
        reason = None
        size = float(t.get("size") or 0)
        color = tuple(float(v) for v in (t.get("color") or ()))
        if t.get("type") == 3:
            reason = "invisible render mode"
        elif float(t.get("opacity", 1) or 0) <= 0.01:
            reason = "fully transparent"
        elif 0 < size < TINY_FONT_PT:
            reason = f"tiny font ({size:.1f} pt)"
        elif color and all(v >= WHITE for v in color) and _white_behind(page, t["bbox"]):
            reason = "white on white"
        if reason:
            r = pymupdf.Rect(t["bbox"]) * matrix
            out.append(
                HiddenSpan(
                    text[:300],
                    (round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2)),
                    reason,
                )
            )
    return out


def _white_behind(page: Any, bbox: Any) -> bool:
    """The page under the span renders white: the text is drawn white on white."""
    try:
        pix = page.get_pixmap(clip=pymupdf.Rect(bbox), dpi=36, alpha=False)
    except Exception:
        return False
    samples = bytes(pix.samples)
    if not samples:
        return True
    return sum(samples) / len(samples) / 255 >= WHITE


def _visible(words: list[Word], hidden: list[HiddenSpan]) -> list[Word]:
    """Words outside every hidden span."""
    if not hidden:
        return words

    def inside(w: Word, h: HiddenSpan) -> bool:
        cx, cy = (w.x0 + w.x1) / 2, (w.y0 + w.y1) / 2
        x0, y0, x1, y1 = h.bbox
        return (
            x0 - 0.5 <= cx <= x1 + 0.5 and y0 - 0.5 <= cy <= y1 + 0.5 and w.text.strip() in h.text
        )

    return [w for w in words if not any(inside(w, h) for h in hidden)]


def _has_content(doc: Any) -> bool:
    return any(page.get_text("text").strip() or page.get_images() for page in doc)


def _words(page: Any) -> list[Word]:
    matrix = page.rotation_matrix
    out: list[Word] = []
    for x0, y0, x1, y1, text, block, line, word_no in page.get_text("words"):
        r = pymupdf.Rect(x0, y0, x1, y1) * matrix
        out.append(
            Word(
                round(float(r.x0), 2),
                round(float(r.y0), 2),
                round(float(r.x1), 2),
                round(float(r.y1), 2),
                str(text),
                int(block),
                int(line),
                int(word_no),
            )
        )
    return out


def _image_coverage(page: Any) -> float:
    area = abs(page.rect)
    if not area:
        return 0.0
    covered = sum(abs(pymupdf.Rect(info["bbox"]) & page.rect) for info in page.get_image_info())
    return min(1.0, float(covered) / float(area))
