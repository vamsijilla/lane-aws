"""Where a value sits on its page, and a second, independent read of it.

The model reads a document once. Code then reads each value again from the page, anchored to
where it must be:
  - a label from the field catalog ("DOB", "Policy Number"): the value printed right of it on
    the same row, under it, or (for a label that follows its value) left of it;
  - a table record: the value on the record's own row;
  - a worksheet cell: the cell itself is the read.

Matching is by type, never "close enough" for numbers:
  - identifier: equal after folding look-alikes (O/0, I/l/1, S/5, B/8); which folding was needed
    is recorded;
  - number and money: the same digits;
  - date: the same date after normalization; a day/month order that could be read both ways is
    marked ambiguous;
  - name: every word present; a word may differ by an OCR-sized typo (90 or above) only on the
    value's own line;
  - enum and text: the printed words, including statements such as "NONE TO REPORT".

A value only ever grounds "anywhere on the page" when it is long and distinctive: never a value
of three characters or fewer, never Yes or No, never a single number.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

from rapidfuzz import fuzz

from lane.domain import normalize as nz

NAME_WORD_MIN = 90

Kind = Literal["label", "row", "cell", "statement", "page", "none"]
_TOKEN = re.compile(r"[A-Za-z0-9]+(?:[.'][A-Za-z0-9]+)*")
_FOLD = str.maketrans(
    {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2"}
)
_YES_NO = {"yes", "no", "y", "n", "true", "false", "x"}


# --- words and rows ------------------------------------------------------------------------


@dataclass
class W:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    norm: str  # lower case, letters and digits only
    row: int = -1
    score: float = 1.0  # OCR's confidence in the word; text-layer words are certain


@dataclass
class Page:
    """A page's words grouped into rows by position (not by the PDF's own line breaks, which
    split a label and its value when they are drawn separately)."""

    words: list[W]
    rows: list[list[W]] = field(default_factory=lambda: list[list[W]]())
    ocr: bool = False

    @classmethod
    def from_layout(cls, layout: dict[str, Any] | None, *, ocr: bool = False) -> "Page | None":
        if not layout or "words" not in layout:
            return None
        words = [
            W(
                float(w[0]),
                float(w[1]),
                float(w[2]),
                float(w[3]),
                str(w[4]),
                _norm(str(w[4])),
                score=float(w[8]) if ocr and len(w) > 8 else 1.0,
            )
            for w in layout["words"]
            if str(w[4]).strip()
        ]
        page = cls(words, ocr=ocr)
        page._group()
        return page

    @classmethod
    def from_text(cls, text: str) -> "Page":
        """Plain text (an email body) as rows of words, one row per line, with positions by
        character so 'same line' and 'below' mean what they do on a page."""
        words: list[W] = []
        for y, line in enumerate((text or "").splitlines()):
            for m in re.finditer(r"\S+", line):
                x = m.start() * 6.0
                words.append(
                    W(
                        x,
                        y * 12.0,
                        x + len(m.group(0)) * 6.0,
                        y * 12.0 + 10.0,
                        m.group(0),
                        _norm(m.group(0)),
                    )
                )
        page = cls(words)
        page._group()
        return page

    def _group(self) -> None:
        ordered = sorted(self.words, key=lambda w: ((w.y0 + w.y1) / 2, w.x0))
        rows: list[list[W]] = []
        centre, height = 0.0, 1.0
        for w in ordered:
            mid = (w.y0 + w.y1) / 2
            # One row: centred within half a height of the row's first word (the larger of the
            # two heights, as OCR boxes vary). Rows never grow word by word, so two stacked
            # lines of a header (about 1.2 heights apart) stay two rows.
            if rows and abs(mid - centre) <= max(height, w.y1 - w.y0) * 0.5:
                rows[-1].append(w)
                continue
            rows.append([w])
            centre, height = mid, max(1.0, w.y1 - w.y0)
        for i, row in enumerate(rows):
            row.sort(key=lambda w: w.x0)
            for w in row:
                w.row = i
        self.rows = rows

    def text(self) -> str:
        return "\n".join(" ".join(w.text for w in row) for row in self.rows)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def tokens(text: str) -> list[str]:
    return [t for t in (_norm(x) for x in _TOKEN.findall(text or "")) if t]


# --- typed values --------------------------------------------------------------------------


def _number(text: str) -> Decimal | None:
    m = re.search(r"-?\(?\$?\s*\d[\d,]*(?:\.\d+)?\)?", text)
    if not m:
        return None
    raw = m.group(0)
    neg = raw.startswith("-") or raw.startswith("(")
    digits = re.sub(r"[^\d.]", "", raw)
    try:
        value = Decimal(digits)
    except InvalidOperation:
        return None
    return -value if neg else value


def _dates(text: str) -> list[tuple[date, bool]]:
    """Every date in the text, with whether its day and month could be swapped."""
    out: list[tuple[date, bool]] = []
    for m in re.finditer(
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b|\b[A-Za-z]{3,9}\.? \d{1,2},? ?\d{4}\b|\b\d{1,2} [A-Za-z]{3,9} \d{4}\b",
        text,
    ):
        d = nz.to_date(m.group(0))
        if d is None:
            continue
        parts = re.findall(r"\d+", m.group(0))
        ambiguous = (
            len(parts) == 3
            and len(parts[0]) <= 2
            and int(parts[0]) <= 12
            and int(parts[1]) <= 12
            and parts[0] != parts[1]
        )
        out.append((d, ambiguous))
    return out


def fold(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper()).translate(_FOLD)


@dataclass(frozen=True)
class Compared:
    equal: bool
    folding: str | None = None  # "O/0" when look-alikes had to be folded
    ambiguous: bool = False  # a day/month order that reads both ways
    read: str | None = None  # what code read there, for the side-by-side


def _tok_eq(a: str, b: str) -> str | None:
    """'' when equal, the folding when equal only after folding look-alikes, a typo note for a
    long word printed with one slip ("LIABLITY"), else None. Digits never get the typo leeway."""
    if a == b:
        return ""
    if len(a) >= 3 and fold(a) == fold(b):
        return "look-alikes"
    if len(a) >= 6 and a.isalpha() and b.isalpha() and fuzz.ratio(a, b) >= NAME_WORD_MIN:
        return f"printed '{b}'"
    return None


def compare(
    kind: str,
    value: str,
    text: str,
    *,
    line_ok: bool = True,
    forms: list[str] | None = None,
    state: bool = False,
) -> Compared:
    """Does `text` (what code read at the anchor) hold `value` (what the model read)?
    `forms` are the ways an enum value is printed ("VIOL" for violation); `state` compares
    state names and codes ("NEW JERSEY" is NJ)."""
    value = (value or "").strip()
    if not value or not text.strip():
        return Compared(False)
    text = re.sub(r",(?=\d{4}\b)", ", ", text)
    if state:
        from lane.domain import geo

        want = geo.to_code(value)
        if want:
            for n in range(1, 4):
                words = text.split()
                for i in range(len(words) - n + 1):
                    if geo.to_code(" ".join(words[i : i + n]).strip(",.:")) == want:
                        return Compared(True, read=want)
    if forms:
        for form in forms:
            if all(any(_tok_eq(t, h) is not None for h in tokens(text)) for t in tokens(form)):
                return Compared(True, read=form)
    match kind:
        case "identifier":
            want = re.sub(r"[^A-Z0-9]", "", value.upper())
            for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9-]*[A-Za-z0-9]|[A-Za-z0-9]", text):
                got = re.sub(r"[^A-Z0-9]", "", tok.upper())
                if got == want:
                    return Compared(True, read=tok)
                if fold(got) == fold(want) and got:
                    pairs = sorted({f"{a}/{b}" for a, b in zip(got, want, strict=False) if a != b})
                    return Compared(True, folding=", ".join(pairs) or "look-alikes", read=tok)
            # a long identifier split over words ("C447 1925")
            joined = re.sub(r"[^A-Z0-9]", "", text.upper())
            if len(want) >= 6 and want in joined:
                return Compared(True, read=value)
            first = re.search(r"[A-Za-z0-9]{3,}", text)
            return Compared(False, read=first.group(0) if first else None)
        case "number" | "money":
            want_n = _number(value)
            if want_n is None:
                return Compared(False)  # "-" or "N/A" is not a number to compare
            got_all = [
                _number(m.group(0)) for m in re.finditer(r"-?\(?\$?\s*\d[\d,]*(?:\.\d+)?\)?", text)
            ]
            got_n = [g for g in got_all if g is not None]
            if any(g == want_n for g in got_n):
                return Compared(True, read=str(want_n))
            return Compared(False, read=str(got_n[0]) if got_n else None)
        case "date":
            want_d = nz.to_date(re.sub(r",(?=\d{4}\b)", ", ", value))
            found = _dates(text)
            for d, amb in found:
                if want_d is not None and d == want_d:
                    return Compared(True, ambiguous=amb, read=d.isoformat())
            return Compared(False, read=found[0][0].isoformat() if found else None)
        case "name":
            want_t = tokens(value)
            have = tokens(text)
            if not want_t:
                return Compared(False)
            ok = all(
                any(_tok_eq(t, h) is not None for h in have)
                or (line_ok and any(fuzz.ratio(t, h) >= NAME_WORD_MIN for h in have))
                for t in want_t
            )
            return Compared(ok, read=" ".join(text.split())[:80])
        case _:  # enum, text, statements
            want_t = tokens(value)
            have = tokens(text)
            if not want_t:
                return Compared(False)
            folds = [
                next((f for h in have if (f := _tok_eq(t, h)) is not None), None) for t in want_t
            ]
            if all(f is not None for f in folds):
                used = next((f for f in folds if f), None)
                return Compared(True, folding=used, read=" ".join(text.split())[:80])
            joined_v, joined_h = " ".join(want_t), " ".join(have)
            if len(joined_v) >= 12 and fuzz.partial_ratio(joined_v, joined_h) >= NAME_WORD_MIN:
                return Compared(True, read=" ".join(text.split())[:80])
            return Compared(False, read=" ".join(text.split())[:80] or None)


def is_short(kind: str, value: str) -> bool:
    """Values that never ground anywhere on a page: 3 characters or fewer, yes/no, one number."""
    v = (value or "").strip()
    if len(re.sub(r"\W", "", v)) <= 3 or v.lower() in _YES_NO:
        return True
    return kind in ("number", "money") or bool(re.fullmatch(r"[\d,.$\s-]+", v))


# --- anchors -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Want:
    """What to look for: a value of a type, beside its labels, in its printed forms."""

    kind: str
    value: str
    labels: tuple[str, ...] = ()
    forms: tuple[str, ...] = ()
    state: bool = False

    def compare(self, text: str, *, line_ok: bool = True) -> Compared:
        return compare(
            self.kind, self.value, text, line_ok=line_ok, forms=list(self.forms), state=self.state
        )


@dataclass(frozen=True)
class Anchor:
    """Where code found the value: beside its `label`, in its record's `row` or column, as its
    own `line`, inside a printed `statement`, in a `cell`, only somewhere on the `page`, or
    `none`. `mismatch`: beside the label, code read a different value (`read`)."""

    kind: Kind | Literal["line", "column"]
    bbox: tuple[float, float, float, float] | None = None
    label: str | None = None
    where: str | None = None
    folding: str | None = None
    ambiguous: bool = False
    read: str | None = None
    mismatch: bool = False
    # OCR pages: the confidence of the words code read there (every word for an identifier,
    # their mean otherwise), and whether it was too low to count as a read at all
    score: float | None = None
    unsure: bool = False
    reread: bool = False  # read again from an enlarged crop of the spot

    @property
    def verified(self) -> bool:
        """A second, independent read agrees: anchored by label, record, line or statement."""
        return (
            not self.mismatch
            and not self.unsure
            and self.kind
            in (
                "label",
                "row",
                "column",
                "line",
                "statement",
                "cell",
            )
        )


def _union(words: list[W]) -> tuple[float, float, float, float]:
    return (
        min(w.x0 for w in words),
        min(w.y0 for w in words),
        max(w.x1 for w in words),
        max(w.y1 for w in words),
    )


def _rows(page: Page, rows: range | None) -> list[int]:
    return list(rows) if rows is not None else list(range(len(page.rows)))


def _find_seq(page: Page, seq: list[str], rows: range | None = None) -> list[list[W]]:
    """Every place the token sequence appears within one row (look-alikes folded)."""
    hits: list[list[W]] = []
    if not seq:
        return hits
    for r in _rows(page, rows):
        row = page.rows[r]
        flat: list[tuple[str, int]] = [(t, i) for i, w in enumerate(row) for t in tokens(w.text)]
        for start in range(len(flat) - len(seq) + 1):
            if all(_tok_eq(seq[k], flat[start + k][0]) is not None for k in range(len(seq))):
                first, last = flat[start][1], flat[start + len(seq) - 1][1]
                hits.append(row[first : last + 1])
    return hits


def _region(page: Page, label: list[W], where: str) -> list[W]:
    x0, y0, x1, y1 = _union(label)
    row = page.rows[label[0].row]
    if where == "same line":
        out: list[W] = []
        for w in row:
            if w.x0 < x1 - 0.5:
                continue
            if out and w.text.rstrip().endswith(":") and len(w.text) > 2:
                break  # the next label on the row
            if out and w.x0 - out[-1].x1 > max(60.0, (x1 - x0) * 2):
                break  # a wide gap: another column
            out.append(w)
            if len(out) >= 14:
                break
        return out
    if where == "before":
        return [w for w in row if w.x1 <= x0 + 0.5][-8:]
    height = max(1.0, y1 - y0)
    for r in page.rows[label[0].row + 1 : label[0].row + 3]:
        if not r or r[0].y0 - y1 > height * 2.2:
            break
        cand = [w for w in r if w.x1 >= x0 - height and w.x0 <= x1 + max(150.0, (x1 - x0) * 3)]
        if cand:
            return cand[:14]
    return []


def _value_words(region: list[W], value: str) -> list[W]:
    want = tokens(value)
    picked = [w for w in region if w.norm and any(t in w.norm or w.norm in t for t in want)]
    return picked or region


def by_label(page: Page, want: Want, rows: range | None = None) -> Anchor:
    """The value beside one of its labels: right of it, under it, or before it."""
    mismatch: Anchor | None = None
    name_tokens: set[str] = set()
    name_words: list[W] = []
    for label in sorted(want.labels, key=len, reverse=True):
        seq = tokens(label)
        for hit in _find_seq(page, seq, rows):
            value_tokens = tokens(want.value)
            if value_tokens[: len(seq)] == seq and len(value_tokens) > len(seq):
                bare = Want(
                    want.kind,
                    " ".join(value_tokens[len(seq) :]),
                    want.labels,
                    want.forms,
                    want.state,
                )
            else:
                bare = want
            for where in ("same line", "below", "before"):
                region = _region(page, hit, where)
                if not region:
                    continue
                text = " ".join(w.text for w in region)
                got = bare.compare(text)
                if got.equal:
                    words = _value_words(region, want.value)
                    return Anchor(
                        "label", _union(words), label, where, got.folding, got.ambiguous, got.read
                    )
                if want.kind == "name":  # a name split over labels (LN, FN): pool the words
                    name_tokens |= set(tokens(text))
                    name_words += region
                elif mismatch is None and got.read and where == "same line":
                    mismatch = Anchor(
                        "label", _union(region), label, where, read=got.read, mismatch=True
                    )
    if (
        want.kind == "name"
        and name_words
        and all(any(_tok_eq(t, h) is not None for h in name_tokens) for t in tokens(want.value))
    ):
        return Anchor("label", _union(name_words), "/".join(want.labels), "split", read=want.value)
    return mismatch or Anchor("none")


def record_rows(page: Page, quote: str | None, next_quote: str | None) -> range | None:
    """The rows of one table record: from the row its quote starts on to the next record's."""
    seq = tokens(quote or "")[:5]
    if not seq:
        return None
    hits = _find_seq(page, seq)
    if not hits:
        return None
    start = hits[0][0].row
    end = min(start + 14, len(page.rows))
    nxt = tokens(next_quote or "")[:5]
    if nxt and nxt != seq:
        later = [h[0].row for h in _find_seq(page, nxt) if h[0].row > start]
        if later:
            end = min(end, later[0])
    return range(start, max(end, start + 1))


def in_block(page: Page, want: Want, rows: range) -> Anchor:
    """A record's value anywhere within the record's own rows."""
    for r in rows:
        row = page.rows[r]
        text = " ".join(w.text for w in row)
        got = want.compare(text)
        if got.equal:
            return Anchor(
                "row",
                _union(_value_words(row, want.value)),
                None,
                "in its record",
                got.folding,
                got.ambiguous,
                got.read,
            )
    return Anchor("none")


def in_column(page: Page, want: Want, header: str | None) -> Anchor:
    """A value under its column header (a totals block laid out in columns)."""
    seq = tokens(header or "")[:4]
    for hit in _find_seq(page, seq) if seq else []:
        x0, _y0, x1, _y1 = _union(hit)
        for r in page.rows[hit[0].row + 1 : hit[0].row + 16]:
            cell = [w for w in r if x0 - 8 <= (w.x0 + w.x1) / 2 <= x1 + 8]
            if not cell:
                continue
            got = want.compare(" ".join(w.text for w in cell))
            if got.equal:
                return Anchor(
                    "column",
                    _union(cell),
                    header,
                    "under its column",
                    got.folding,
                    got.ambiguous,
                    got.read,
                )
    return Anchor("none")


def own_line(page: Page, want: Want, rows: range | None = None) -> Anchor:
    """A value printed as a whole line of its own (a letterhead, a date line, a name line)."""
    n = len(tokens(want.value))
    if is_short(want.kind, want.value):
        return Anchor("none")  # "Yes" alone on a line says nothing about which question
    slack = 1 if n >= 3 else 0
    for r in _rows(page, rows):
        row = page.rows[r]
        if abs(len(tokens(" ".join(w.text for w in row))) - n) > slack:
            continue
        got = want.compare(" ".join(w.text for w in row))
        if got.equal:
            return Anchor(
                "line", _union(row), None, "its own line", got.folding, got.ambiguous, got.read
            )
    return Anchor("none")


def in_statement(page: Page, want: Want, rows: range | None = None) -> Anchor:
    """Three or more words printed verbatim in one line ("There have been no paid losses")."""
    seq = tokens(want.value)
    distinctive = len(seq) >= 3 or (len(seq) == 2 and len(want.value) >= 8)
    if not distinctive or want.kind not in ("text", "enum", "name"):
        return Anchor("none")
    hits = _find_seq(page, seq[: min(len(seq), 8)], rows)
    if hits:
        return Anchor("statement", _union(hits[0]), None, "printed verbatim", read=want.value)
    return Anchor("none")


def anywhere(page: Page, want: Want, rows: range | None = None) -> Anchor:
    """Long, distinctive values only: on the page, but not beside a label."""
    if is_short(want.kind, want.value):
        return Anchor("none")
    for r in _rows(page, rows):
        row = page.rows[r]
        got = want.compare(" ".join(w.text for w in row), line_ok=False)
        if got.equal:
            return Anchor(
                "page",
                _union(_value_words(row, want.value)),
                None,
                None,
                got.folding,
                got.ambiguous,
                got.read,
            )
    return Anchor("none")


_QUOTED_LABEL = re.compile(r"^\s*([A-Za-z][A-Za-z #/&().'-]{1,40}?)\s*:")


def quoted_label(quote: str | None) -> str | None:
    """The label the model quoted ('Owner drives: yes' -> 'Owner drives'), if it quoted one."""
    m = _QUOTED_LABEL.match(quote or "")
    return m.group(1).strip() if m and tokens(m.group(1)) else None


def locate(
    page: Page,
    want: Want,
    *,
    row_quote: str | None = None,
    next_quote: str | None = None,
    column: str | None = None,
    quote: str | None = None,
) -> Anchor:
    """Where a value sits, and whether code read the same value there.

    Scalars: beside a label, as their own line, or inside a printed statement. Table records:
    within the record's rows, beside a label there, or under the record's column header.
    Only long values ever fall back to "somewhere on the page"."""
    block = record_rows(page, row_quote, next_quote) if row_quote else None
    mismatch: Anchor | None = None
    label = quoted_label(quote)
    if label and block is None:
        # The label the model quoted, found on the page by code, with the value beside it: a
        # layout the catalog's labels do not know still verifies
        got = by_label(page, Want(want.kind, want.value, (label,), want.forms, want.state))
        if got.verified:
            return got
        mismatch = got if got.mismatch else None
    if want.labels:
        got = by_label(page, want, block)
        if got.verified:
            return got
        mismatch = mismatch or (got if got.mismatch else None)
    if block is not None:
        got = in_block(page, want, block)
        if got.verified:
            return got
    if column and tokens(want.value) == tokens(column):
        # This field is the column's own header (a coverage line's name above its amounts)
        hits = _find_seq(page, tokens(want.value)[:4])
        if hits:
            return Anchor("column", _union(hits[0]), None, "its column header", read=want.value)
    for header in (column, row_quote):
        if header:
            got = in_column(page, want, header)
            if got.verified:
                return got
    for step in (own_line, in_statement):
        got = step(page, want, block)
        if got.verified:
            return got
    if mismatch is not None:
        return mismatch
    return anywhere(page, want)


def want_for(spec: Any, value: str) -> Want:
    """What to look for, from a catalog field (FieldSpec) and the value read."""
    values: dict[str, list[str]] = getattr(spec, "values", {}) or {}
    forms = [*values.get(value.strip().lower(), []), *values.get(value.strip(), [])]
    return Want(
        kind=str(spec.type),
        value=value,
        labels=tuple(spec.labels),
        forms=tuple(forms),
        state="state" in (spec.validators or []),
    )


_TABLE_INDEX = re.compile(r"^(.*?)\[(\d+)\](\.[^\[\]]+)$")


def next_quote(data: dict[str, Any], path: str) -> str | None:
    """The quote of the record after this one in its table ('claims[0].vin' -> claims[1]),
    so a record's block ends where the next record starts."""
    m = _TABLE_INDEX.match(path)
    if not m:
        return None
    node: Any = data
    for part in re.split(r"\.(?![^\[]*\])", m.group(1)):
        key, _, idx = part.partition("[")
        node = cast(dict[str, Any], node).get(key) if isinstance(node, dict) else None
        if idx and isinstance(node, list):
            items = cast(list[Any], node)
            i = int(idx.rstrip("]"))
            node = items[i] if i < len(items) else None
    if not isinstance(node, list):
        return None
    records = cast(list[Any], node)
    i = int(m.group(2)) + 1
    nxt: Any = records[i] if i < len(records) else None
    if isinstance(nxt, dict):
        quote = cast(dict[str, Any], nxt).get("quote")
        return str(quote) if quote else None
    return None


def record_value(data: dict[str, Any], path: str, key: str | None) -> str | None:
    """Another field of the same record ('coverage_totals[1].covered' -> its 'coverage')."""
    if not key:
        return None
    m = re.match(r"^([a-z_]+)\[(\d+)\]\.", path)
    if not m:
        return None
    rows: Any = data.get(m.group(1))
    if not isinstance(rows, list):
        return None
    records = cast(list[Any], rows)
    n = int(m.group(2))
    rec: Any = records[n] if n < len(records) else None
    if not isinstance(rec, dict):
        return None
    value: Any = cast(dict[str, Any], rec).get(key)
    return str(value) if value else None


def anchor_score(page: Page, anchor: Anchor, want: Want) -> float | None:
    """OCR's confidence in what code read at the anchor: the words inside its box that make up
    the value (or, for a disagreement, what was read instead). Every word must be sure for an
    identifier; otherwise their mean. None on text-layer pages or without a box."""
    if not page.ocr or anchor.bbox is None:
        return None
    x0, y0, x1, y1 = anchor.bbox
    inside = [
        w
        for w in page.words
        if x0 - 1 <= (w.x0 + w.x1) / 2 <= x1 + 1 and y0 - 1 <= (w.y0 + w.y1) / 2 <= y1 + 1
    ]
    if not inside:
        return None
    target = {
        fold(t) for t in tokens(anchor.read if anchor.mismatch and anchor.read else want.value)
    }
    matched = [w for w in inside if fold(w.text) and fold(w.text) in target] or inside
    scores = [w.score for w in matched]
    return min(scores) if want.kind == "identifier" else sum(scores) / len(scores)
