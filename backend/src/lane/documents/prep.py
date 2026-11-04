"""Page preparation: make a scan or a phone photo readable before anyone reads it.

Triage measures a page; preparation fixes what it can, and only when it is sure:

1. A photo of a page is flattened: the page outline is found and warped to a flat rectangle,
   dropping the desk around it.
2. A page turned 90 or 180 degrees is turned upright, decided by which way OCR reads best.
3. A small tilt is straightened.
4. Uneven light (a phone's shadow) is evened out.

Each step taken is recorded in words, the original image is kept, and the prepared image is
what OCR, the model and the evidence lens all see, so their boxes line up. OCR also gets a
contrast-boosted, upscaled copy (`for_ocr`), which nobody else sees.
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

from dataclasses import dataclass, field

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image

from lane.documents import imaging, ocr

type Quad = NDArray[np.float32]

TURNS = {
    90: "Turned a quarter turn clockwise",
    180: "Turned the right way up",
    270: "Turned a quarter turn counterclockwise",
}


@dataclass(frozen=True)
class PrepConfig:
    deskew_min_deg: float = 0.5  # straighten tilts at least this large
    flatten_min_area: float = 0.35  # a page outline must cover this share of a photo
    shadow_min_range: float = 40.0  # even out light when the background varies this much
    ocr_min_width: int = 1700  # upscale narrower images for OCR (small print)
    orientation_margin: float = 1.25  # another way up must read this much better to turn


@dataclass
class Prepared:
    image: Image.Image
    steps: list[str] = field(default_factory=lambda: list[str]())
    turned: int = 0  # degrees, clockwise
    straightened: float = 0.0
    flattened: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.steps)


def prepare(img: Image.Image, *, photo: bool, cfg: PrepConfig, orient: bool = True) -> Prepared:
    """`orient=False` skips turning, which needs OCR (off when OCR is unavailable)."""
    out = Prepared(img.convert("RGB"))
    if photo:
        flat = flatten(out.image, cfg)
        if flat is not None:
            out.image, out.flattened = flat, True
            out.steps.append("Flattened a photo of the page")
    turn = upright_turn(out.image, cfg) if orient else 0
    if turn:
        # PIL rotates counterclockwise; a clockwise turn of `turn` degrees is -turn.
        out.image = out.image.rotate(-turn, expand=True)
        out.turned = turn
        out.steps.append(TURNS[turn])
    skew = imaging.estimate_skew(out.image)
    if abs(skew) >= cfg.deskew_min_deg:
        out.image = out.image.rotate(
            skew, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=_paper(out.image)
        )
        out.straightened = skew
        out.steps.append(f"Straightened a {abs(skew):g} degree tilt")
    if photo or out.flattened:
        even = even_light(out.image, cfg)
        if even is not None:
            out.image = even
            out.steps.append("Evened out a shadow")
    return out


# --- flattening ----------------------------------------------------------------------------


def find_page(img: Image.Image, cfg: PrepConfig) -> Quad | None:
    """The page's four corners in a photo, or None when no clear outline is found."""
    scale = 1000 / max(img.size)
    small = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))))
    g = cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2GRAY)
    g = cv2.GaussianBlur(g, (5, 5), 0)
    # The page is the large bright region: threshold it, then take its outline.
    _, mask = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    frame = float(g.shape[0] * g.shape[1])
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:3]:
        area = cv2.contourArea(c)
        if area < cfg.flatten_min_area * frame or area > 0.97 * frame:
            continue  # too small to be the page, or the page already fills the frame
        approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            return _order(approx.reshape(4, 2).astype(np.float32) / scale)
    return None


def flatten(img: Image.Image, cfg: PrepConfig) -> Image.Image | None:
    quad = find_page(img, cfg)
    if quad is None:
        return None
    tl, tr, br, bl = quad
    w = round(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    h = round(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    if w < 200 or h < 200:
        return None
    target = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(quad, target)
    warped = cv2.warpPerspective(np.asarray(img), m, (w, h), flags=cv2.INTER_CUBIC)
    return Image.fromarray(warped)


def _order(pts: Quad) -> Quad:
    """Corners as top-left, top-right, bottom-right, bottom-left."""
    s, d = pts.sum(axis=1), np.diff(pts, axis=1).ravel()
    return np.array(
        [pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]],
        dtype=np.float32,
    )


# --- orientation ---------------------------------------------------------------------------


def _score(words: list[ocr.OcrWord]) -> float:
    """How well OCR read a page: the confidence of every real word it found."""
    return sum(w.score for w in words if len(w.text) > 1)


def _read(img: Image.Image) -> list[ocr.OcrWord]:
    small = img.copy()
    small.thumbnail((1200, 1200))
    return ocr.read_words(small)


def _sideways(words: list[ocr.OcrWord]) -> bool:
    """Text lines taller than they are wide: the page lies on its side. Geometry, not reading,
    decides this, because OCR reads vertical lines about as well as horizontal ones."""
    lines: dict[int, list[ocr.OcrWord]] = {}
    for w in words:
        if len(w.text) > 2:
            lines.setdefault(w.line, []).append(w)
    if len(lines) < 3:
        return False
    tall = 0
    for line in lines.values():
        width = max(w.x1 for w in line) - min(w.x0 for w in line)
        height = max(w.y1 for w in line) - min(w.y0 for w in line)
        tall += height > 1.5 * width
    return tall / len(lines) > 0.6


def upright_turn(img: Image.Image, cfg: PrepConfig) -> int:
    """Clockwise degrees (0, 90, 180, 270) that make the text read upright.

    A sideways page is turned 90 one way or the other, whichever reads better. An upright or
    upside-down page turns 180 only when that reads clearly better than as it is."""
    words = _read(img)
    if _sideways(words):
        cw, ccw = (_score(_read(img.rotate(-t, expand=True))) for t in (90, 270))
        return 90 if cw >= ccw else 270
    base = _score(words)
    return 180 if _score(_read(img.rotate(180))) >= base * cfg.orientation_margin else 0


# --- light and OCR input -------------------------------------------------------------------


def even_light(img: Image.Image, cfg: PrepConfig) -> Image.Image | None:
    """Divide out uneven light (a phone's shadow) so it does not hide print.

    Light is estimated at the scale shadows have: a wide max filter fills photos and logos in
    with the bright paper around them, and a heavy blur keeps only the smooth gradient. So a
    portrait on a license card is light-independent detail, not shadow, and is left alone."""
    rgb = np.asarray(img).astype(np.float32)
    g = cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    k = max(15, (min(g.shape) // 8) | 1)
    paper = cv2.dilate(g, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    light = cv2.GaussianBlur(paper, (0, 0), sigmaX=min(g.shape) / 6).astype(np.float32)
    if float(np.percentile(light, 98) - np.percentile(light, 2)) < cfg.shadow_min_range:
        return None
    out = np.clip(rgb / np.maximum(light, 1.0)[..., None] * 245.0, 0, 255).astype(np.uint8)
    return Image.fromarray(out)


def for_ocr(img: Image.Image, cfg: PrepConfig) -> tuple[Image.Image, float]:
    """A copy tuned for OCR (local contrast, small print enlarged) and its scale factor."""
    factor = min(2.0, cfg.ocr_min_width / img.width) if img.width < cfg.ocr_min_width else 1.0
    if factor > 1.0:
        img = img.resize(
            (round(img.width * factor), round(img.height * factor)), Image.Resampling.LANCZOS
        )
    lab = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2LAB)
    lab[..., 0] = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lab[..., 0])
    return Image.fromarray(cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)), factor


def _paper(img: Image.Image) -> tuple[int, int, int]:
    """The page's background color, to fill corners a rotation exposes."""
    arr = np.asarray(img.convert("RGB")).reshape(-1, 3)
    r, g, b = (int(v) for v in np.percentile(arr, 90, axis=0))
    return (r, g, b)
