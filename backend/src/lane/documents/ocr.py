"""Typed adapter over RapidOCR (PP-OCR models on ONNX Runtime, fully local).

OCR gives scanned and photographed pages what digital PDFs already have: words with boxes. That
enables three things: highlight boxes on scans, keyword signals for classification, and an
independent second reading that the vision model's values are checked against.

RapidOCR's calls are confined to this module; production can swap in Textract or Azure Document
Intelligence behind the same `read_words` function.
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false

import logging
import threading
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any, Protocol

import numpy as np
from PIL import Image

_engines: dict[str, Any] = {}
_lock = threading.Lock()
# Model choices, "<PP-OCR version>/<size>": the triage setting `triage_ocr_models`.
# PP-OCRv6 comes in tiny, small and medium; PP-OCRv5 in mobile and server.
DEFAULT_MODELS = "PP-OCRv6/small"
ENGINE = f"rapidocr {version('rapidocr')}"


class OcrCache(Protocol):
    """Words already read, by file, page, engine and settings (documents/triage.py makes the
    key), so the same file sent again is not read again."""

    def get(self, key: str) -> list[list[Any]] | None: ...
    def put(self, key: str, words: list[list[Any]]) -> None: ...


@dataclass(frozen=True)
class OcrWord:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    line: int
    word_no: int
    score: float


def _params(models: str) -> dict[str, Any]:
    from rapidocr import ModelType, OCRVersion

    version, size = models.split("/")
    v, m = OCRVersion(version), ModelType(size)
    return {"Det.ocr_version": v, "Det.model_type": m, "Rec.ocr_version": v, "Rec.model_type": m}


def _get_engine(models: str = DEFAULT_MODELS) -> Any:
    with _lock:
        if models not in _engines:
            from rapidocr import RapidOCR

            logging.getLogger("RapidOCR").setLevel(logging.WARNING)
            _engines[models] = RapidOCR(
                params=None if models == DEFAULT_MODELS else _params(models)
            )
            logging.getLogger("RapidOCR").setLevel(logging.WARNING)
        return _engines[models]


def read_words(img: Image.Image, models: str = DEFAULT_MODELS) -> list[OcrWord]:
    """Words in reading order with boxes in image pixels and recognition confidence."""
    engine = _get_engine(models)
    with _lock:  # ONNX sessions are shared; keep calls serialized
        result = engine(np.asarray(img.convert("RGB")), return_word_box=True)
    words: list[OcrWord] = []
    texts = list(result.txts or [])
    line_boxes = list(result.boxes) if result.boxes is not None else []
    per_line = list(result.word_results or [])
    for line_no, text in enumerate(texts):
        entries = per_line[line_no] if line_no < len(per_line) else ()
        if entries:
            for word_no, (word, score, box) in enumerate(entries):
                xs = [float(p[0]) for p in box]
                ys = [float(p[1]) for p in box]
                if str(word).strip():
                    words.append(
                        OcrWord(
                            min(xs),
                            min(ys),
                            max(xs),
                            max(ys),
                            str(word),
                            line_no,
                            word_no,
                            float(score),
                        )
                    )
        elif line_no < len(line_boxes):
            box = line_boxes[line_no]
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            score = float(result.scores[line_no])
            for word_no, word in enumerate(str(text).split()):
                words.append(
                    OcrWord(min(xs), min(ys), max(xs), max(ys), word, line_no, word_no, score)
                )
    return words


def warm_up(models: str = DEFAULT_MODELS) -> float:
    """Load the models and read a blank image once; returns the seconds it took."""
    import time

    start = time.perf_counter()
    try:
        read_words(Image.new("RGB", (64, 32), "white"), models)
    except Exception:  # warming is best effort; the first real read will retry
        logging.getLogger(__name__).warning("ocr.warm_up_failed", exc_info=True)
    took = time.perf_counter() - start
    logging.getLogger(__name__).info("ocr.warmed models=%s seconds=%.1f", models, took)
    return took
