"""Find extracted values on the page they came from.

A value is grounded when its quote (or the value itself) is found in the page's word boxes.
Exact token matches give an exact box; otherwise the best matching line above a similarity
threshold gives a fuzzy box. Anything else is ungrounded: kept, flagged, never dropped.
"""

import re
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz

FUZZY_THRESHOLD = 90
TYPO_WORD_MIN = 85  # similarity of each word of a short value to the printed word


@dataclass(frozen=True)
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    line_key: tuple[int, int]
    norm: str


@dataclass(frozen=True)
class Grounding:
    kind: str  # exact | fuzzy | none
    bbox: tuple[float, float, float, float] | None = None
    score: float = 0.0


def _norm(token: str) -> str:
    return re.sub(r"^[^\w$%-]+|[^\w%]+$", "", token.lower())


_LOOKALIKES = str.maketrans({"l": "i", "1": "i", "|": "i", "0": "o"})


def _fold(token: str) -> str:
    return token.translate(_LOOKALIKES)


def tokens(text: str) -> list[str]:
    return [t for t in (_norm(x) for x in text.split()) if t]


class PageWords:
    def __init__(self, layout: dict[str, Any]) -> None:
        self.words = [
            Word(
                float(x0), float(y0), float(x1), float(y1), str(t), (int(b), int(ln)), _norm(str(t))
            )
            # OCR words carry a confidence score after the eight common fields.
            for x0, y0, x1, y1, t, b, ln in (w[:7] for w in layout.get("words", []))
        ]
        self.from_ocr = layout.get("source") == "ocr"
        self.words = [w for w in self.words if w.norm]
        self.lines: dict[tuple[int, int], list[Word]] = {}
        for w in self.words:
            self.lines.setdefault(w.line_key, []).append(w)

    def _find_sequence(self, seq: list[str], *, folded: bool = False) -> list[list[Word]]:
        if not seq:
            return []
        hits: list[list[Word]] = []
        norms = [_fold(w.norm) if folded else w.norm for w in self.words]
        n = len(seq)
        for i in range(len(norms) - n + 1):
            if norms[i : i + n] == seq:
                hits.append(self.words[i : i + n])
        return hits

    def locate(self, value: str | None, quote: str | None) -> Grounding:
        """Prefer the value inside the quote; fall back to the quote, then the value alone,
        then the most similar line."""
        value_seq = tokens(value or "")
        quote_seq = tokens(quote or "")

        quote_hits = self._find_sequence(quote_seq) if quote_seq else []
        value_hits = self._find_sequence(value_seq) if value_seq else []
        if quote_hits and value_hits:
            for qh in quote_hits:
                ids = {id(w) for w in qh}
                for vh in value_hits:
                    if all(id(w) in ids for w in vh):
                        return Grounding("exact", _union(vh), 100)
            # Value printed next to its quoted label: pick the value occurrence on that line.
            lines = {w.line_key for w in quote_hits[0]}
            for vh in value_hits:
                if vh[0].line_key in lines or _same_row(vh[0], quote_hits[0][0]):
                    return Grounding("exact", _union(vh), 100)
        if value_hits and len(value_hits) == 1:
            return Grounding("exact", _union(value_hits[0]), 100)
        # The quote alone grounds a value only if the value's words are there (on the quote or
        # its line): a label ("Agency Name:") does not ground a name the page does not show.
        words = _name_words(value_seq)
        if quote_hits and words:
            near = [w.norm for w in self.words if w.line_key in {x.line_key for x in quote_hits[0]}]
            if not _all_present(words, [*quote_seq, *near]):
                quote_hits, quote_seq = [], []
        if quote_hits:
            return Grounding("exact", _union(quote_hits[0]), 100)
        if value_hits:
            return Grounding("exact", _union(value_hits[0]), 95)  # ambiguous position
        # OCR confuses look-alike characters (I/l/1, O/0): a match after folding them is fuzzy.
        for seq in (value_seq, quote_seq):
            hits = self._find_sequence([_fold(t) for t in seq], folded=True) if seq else []
            if hits:
                return Grounding("fuzzy", _union(hits[0]), 90)
        short = self._typo_words(value_seq) if value_seq else None
        if short:
            return short
        found = self._fuzzy(quote or value or "")
        if found.kind != "none" and words and found.bbox is not None:
            line = [w.norm for w in self.words if _inside(w, found.bbox)]
            if not _all_present(words, line):
                return Grounding("none")
        return found

    def _typo_words(self, seq: list[str]) -> Grounding | None:
        """A short value whose words each match the next word on the page with a small
        misspelling ("Auto Liability" for the printed "AUTO LIABLITY")."""
        n = len(seq)
        if not 1 <= n <= 4 or any(len(t) < 4 or not t.isalpha() for t in seq):
            return None  # numbers and tiny words must match exactly
        norms = [w.norm for w in self.words]
        for i in range(len(norms) - n + 1):
            if all(fuzz.ratio(seq[k], norms[i + k]) >= TYPO_WORD_MIN for k in range(n)):
                return Grounding("fuzzy", _union(self.words[i : i + n]), float(TYPO_WORD_MIN))
        return None

    def _fuzzy(self, text: str) -> Grounding:
        target = " ".join(tokens(text))
        if not target:
            return Grounding("none")
        best: tuple[float, list[Word]] | None = None
        for words in self.lines.values():
            line = " ".join(w.norm for w in words)
            score = (
                fuzz.partial_ratio(target, line)
                if len(target) <= len(line)
                else fuzz.ratio(target, line)
            )
            if best is None or score > best[0]:
                best = (score, words)
        if best and best[0] >= FUZZY_THRESHOLD:
            return Grounding("fuzzy", _union(best[1]), float(best[0]))
        return Grounding("none", score=float(best[0]) if best else 0.0)


def _same_row(a: Word, b: Word) -> bool:
    mid_a = (a.y0 + a.y1) / 2
    return b.y0 - 2 <= mid_a <= b.y1 + 2


def _union(words: list[Word]) -> tuple[float, float, float, float]:
    return (
        round(min(w.x0 for w in words), 2),
        round(min(w.y0 for w in words), 2),
        round(max(w.x1 for w in words), 2),
        round(max(w.y1 for w in words), 2),
    )


def _name_words(seq: list[str]) -> list[str]:
    """The words that make a name what it is: alphabetic, three letters or more. A one-word
    value is usually a category the model inferred ("violation" from "VIOL", "Yes"), not a
    name, so it is not held to this."""
    words = [t for t in seq if t.isalpha() and len(t) >= 3]
    return words if len(words) >= 2 else []


def _all_present(words: list[str], context: list[str]) -> bool:
    """Every word is in the context, allowing an OCR-sized typo ('GRAN1TE' for 'GRANITE')."""
    return all(any(fuzz.ratio(w, c) >= TYPO_WORD_MIN for c in context) for w in words)


def _inside(w: "Word", box: tuple[float, float, float, float]) -> bool:
    x0, y0, x1, y1 = box
    return w.x0 >= x0 - 1 and w.y0 >= y0 - 1 and w.x1 <= x1 + 1 and w.y1 <= y1 + 1
