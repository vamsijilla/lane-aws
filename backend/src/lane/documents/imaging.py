"""Image quality measurements for scanned and photographed pages.

All measures are computed on a grayscale copy resized to a fixed width, so values are
comparable across pages regardless of source resolution.
"""

import io

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageOps

type Gray = NDArray[np.float32]

ANALYSIS_WIDTH = 1200
SKEW_WIDTH = 800
SKEW_RANGE_DEG = 5.0
SKEW_STEP_DEG = 0.25
SIDEWAYS_RATIO = 0.5


def open_image(data: bytes) -> Image.Image:
    """Decode and apply EXIF orientation, so phone photos come out upright."""
    img = Image.open(io.BytesIO(data))
    img.load()
    return ImageOps.exif_transpose(img).convert("RGB")


def to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def thumbnail_png(img: Image.Image, width: int) -> bytes:
    thumb = img.copy()
    thumb.thumbnail((width, width * 4), Image.Resampling.LANCZOS)
    return to_png(thumb)


def gray(img: Image.Image, width: int = ANALYSIS_WIDTH) -> Gray:
    height = max(1, round(width * img.height / img.width))
    resized = img.convert("L").resize((width, height), Image.Resampling.BILINEAR)
    return np.asarray(resized, dtype=np.float32)


def sharpness(g: Gray) -> float:
    """99th percentile of Laplacian response on ink edges.

    Variance of the Laplacian is the usual blur metric, but it falls on sparse pages that
    are perfectly sharp. Looking only at the strongest edges measures focus, not ink density.
    """
    lap = np.abs(-4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:])
    edges = lap[lap > 8]
    if edges.size < 100:
        return 0.0
    return float(np.percentile(edges, 99))


def contrast(g: Gray) -> float:
    return float(g.std())


def white_ratio(g: Gray) -> float:
    return float((g > 235).mean())


def colorfulness(img: Image.Image) -> float:
    """Hasler and Suesstrunk colorfulness. Scans of forms sit near 0, camera photos well above."""
    small = img.convert("RGB").resize((400, max(1, round(400 * img.height / img.width))))
    rgb = np.asarray(small, dtype=np.float32)
    rg = rgb[..., 0] - rgb[..., 1]
    yb = 0.5 * (rgb[..., 0] + rgb[..., 1]) - rgb[..., 2]
    std = float(np.hypot(rg.std(), yb.std()))
    mean = float(np.hypot(rg.mean(), yb.mean()))
    return std + 0.3 * mean


def _row_profile_score(ink: Image.Image, angle: float) -> float:
    rotated = ink.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=0)
    profile = np.asarray(rotated, dtype=np.float32).sum(axis=1)
    return float(np.square(np.diff(profile)).sum())


def _ink(img: Image.Image) -> Image.Image:
    g = gray(img, SKEW_WIDTH)
    return Image.fromarray(((g < 160) * 255).astype(np.uint8))


def estimate_skew(img: Image.Image) -> float:
    """Angle in degrees that makes text lines horizontal (projection profile search).

    Coarse pass in 1 degree steps, then a fine pass in SKEW_STEP_DEG around the best angle.
    """
    ink = _ink(img)

    def best(angles: NDArray[np.float64]) -> float:
        scores = [_row_profile_score(ink, float(a)) for a in angles]
        return float(angles[int(np.argmax(scores))])

    coarse = best(np.arange(-SKEW_RANGE_DEG, SKEW_RANGE_DEG + 1e-9, 1.0))
    fine = best(np.arange(coarse - 0.75, coarse + 0.75 + 1e-9, SKEW_STEP_DEG))
    return round(fine, 2)


def looks_sideways(img: Image.Image) -> bool:
    """True when text lines run vertically, i.e. the page is probably rotated 90 degrees.

    Text lines make the row profile of ink alternate strongly (line, gap, line) while the
    column profile stays flat. Compare their normalized variances. Upright documents score
    well above 1; a ratio under SIDEWAYS_RATIO means the lines run the other way. Photos sit
    near 1 and are never flagged.
    """
    ink = np.asarray(_ink(img), dtype=np.float32)
    rows, cols = ink.sum(axis=1), ink.sum(axis=0)
    if not rows.any():
        return False
    return _cv2(rows) / max(_cv2(cols), 1e-9) < SIDEWAYS_RATIO


def _cv2(profile: NDArray[np.float32]) -> float:
    mean = float(profile.mean())
    return float(profile.var()) / (mean * mean) if mean else 0.0
