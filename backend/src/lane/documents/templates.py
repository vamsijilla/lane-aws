"""Known layouts: recognise a page by its landmarks before asking a model.

A template is data (data/templates/*.yaml): landmarks a page of that layout always shows, each
expected in a region of the page, plus patterns that read the page's subject, period and date.
A page that shows enough landmarks in the right places is that layout. Matching is
deterministic, free and explainable: the landmarks that matched can be drawn on the page.

Three kinds:
- layout: landmarks with positions (a carrier's loss run, a state MVR or tax return).
- marker: a printed form identifier anywhere on the page ("ACORD 125").
- sheet: header labels a worksheet shows, and optionally its sheet name.

Templates only recognise; they never decide outcomes. A template that cannot read a field its
document type needs hands the page to the model, which must agree on the type.
"""

import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from lane.documents.types import DocType
from lane.domain import normalize as nz

TEMPLATE_DIR = Path(__file__).resolve().parents[4] / "data" / "templates"
FUZZ_MIN = 90  # a landmark matches a line at this similarity (tolerates OCR slips)

type Box = tuple[float, float, float, float]


class Anchor(BaseModel):
    text: str
    box: Box | None = None  # normalized x0, y0, x1, y1; None means anywhere on the page
    tol: float = 0.08  # how far outside its box a landmark may sit
    weight: float = 1.0
    required: bool = False


class Template(BaseModel):
    id: str
    name: str
    doc_type: DocType
    kind: Literal["layout", "marker", "sheet"] = "layout"
    status: Literal["active", "draft"] = "draft"
    page: Literal["first", "continuation", "any"] = "any"
    threshold: float = 0.8
    anchors: list[Anchor] = Field(default_factory=lambda: list[Anchor]())
    absent: list[str] = Field(default_factory=lambda: list[str]())  # must not appear
    markers: list[str] = Field(default_factory=lambda: list[str]())  # regexes (marker kind)
    sheet_name: str | None = None  # regex (sheet kind)
    fields: dict[str, list[str]] = Field(default_factory=lambda: dict[str, list[str]]())
    required: list[str] = Field(default_factory=lambda: list[str]())
    title: str | None = None  # e.g. "Loss run, {carrier|company}, {period|years}"
    own_document: bool = False  # a worksheet that is its own document, not a tab of the last one
    # Types this layout is known to be read as by keywords or the model, and why it is still the
    # template's type: a drivers-and-vehicles tab reads as an application or a vehicle schedule
    also_reads_as: list[DocType] = Field(default_factory=lambda: list[DocType]())
    ignore: bool = False  # a page that is no document (a workbook's hidden lookup sheet)
    source: str | None = None  # how it was made: which confirmed document it came from


@dataclass(frozen=True)
class Landmark:
    text: str
    box: Box | None  # where it was found, in page units (for the lens), if known


@dataclass(frozen=True)
class Match:
    template: Template
    score: float
    hits: int
    of: int
    landmarks: tuple[Landmark, ...]
    fields: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    missing: tuple[str, ...] = ()  # required fields it could not read

    @property
    def first_page(self) -> bool | None:
        return {"first": True, "continuation": False}.get(self.template.page)

    @property
    def title(self) -> str | None:
        return render_title(self.template.title, self.fields) if self.template.title else None

    @property
    def reason(self) -> str:
        if self.template.kind == "marker":
            return f"Printed form identifier of {self.template.name}"
        return (
            f"Matches the known layout '{self.template.name}': {self.hits} of {self.of} landmarks"
        )


# --- loading -------------------------------------------------------------------------------


def load(directory: Path = TEMPLATE_DIR, *, active_only: bool = True) -> list[Template]:
    return list(_load(str(directory), _stamp(directory), active_only))


def _stamp(directory: Path) -> tuple[tuple[str, float], ...]:
    """Reload when a template file changes."""
    if not directory.exists():
        return ()
    return tuple(sorted((p.name, p.stat().st_mtime) for p in directory.glob("*.yaml")))


@cache
def _load(
    directory: str, _stamp: tuple[tuple[str, float], ...], active_only: bool
) -> tuple[Template, ...]:
    out: list[Template] = []
    for path in sorted(Path(directory).glob("*.yaml")):
        for raw in yaml.safe_load_all(path.read_text()):
            if raw:
                t = Template.model_validate(raw)
                if t.status == "active" or not active_only:
                    out.append(t)
    return tuple(out)


# --- page text -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    text: str
    box: Box  # page units
    nbox: Box  # normalized


def lines_of(layout: dict[str, Any]) -> list[Line]:
    """Words grouped into lines, with each line's box."""
    width = float(layout.get("width") or 1) or 1.0
    height = float(layout.get("height") or 1) or 1.0
    groups: dict[tuple[int, int], list[list[Any]]] = {}
    for w in layout.get("words", []):
        groups.setdefault((int(w[5]), int(w[6])), []).append(w)
    out: list[Line] = []
    for words in groups.values():
        words.sort(key=lambda w: float(w[0]))
        x0, y0 = min(float(w[0]) for w in words), min(float(w[1]) for w in words)
        x1, y1 = max(float(w[2]) for w in words), max(float(w[3]) for w in words)
        out.append(
            Line(
                " ".join(str(w[4]) for w in words),
                (x0, y0, x1, y1),
                (x0 / width, y0 / height, x1 / width, y1 / height),
            )
        )
    out.sort(key=lambda ln: (round(ln.nbox[1], 3), ln.nbox[0]))
    return out


def sheet_cells(layout: dict[str, Any]) -> list[str]:
    return [str(c.get("v")) for c in layout.get("cells", []) if isinstance(c.get("v"), str)]


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _near(nbox: Box, want: Box, tol: float) -> bool:
    cx, cy = (nbox[0] + nbox[2]) / 2, (nbox[1] + nbox[3]) / 2
    return want[0] - tol <= cx <= want[2] + tol and want[1] - tol <= cy <= want[3] + tol


def _find(phrase: str, lines: list[Line], box: Box | None, tol: float) -> Line | None:
    target = _norm(phrase)
    best: tuple[float, Line] | None = None
    for ln in lines:
        if box is not None and not _near(ln.nbox, box, tol):
            continue
        text = _norm(ln.text)
        score = 100.0 if target in text else fuzz.partial_ratio(target, text)
        if score >= FUZZ_MIN and (best is None or score > best[0]):
            best = (score, ln)
    return best[1] if best else None


# --- matching ------------------------------------------------------------------------------


def match_page(
    templates: list[Template],
    *,
    text: str,
    layout: dict[str, Any] | None,
    sheet_name: str | None = None,
) -> Match | None:
    """The best template for a page, if one matches; None sends the page to the model."""
    lines = lines_of(layout) if layout and "words" in layout else []
    cells = sheet_cells(layout) if layout and "cells" in layout else []
    found: list[Match] = []
    for t in templates:
        m = _match(t, text, lines, cells, sheet_name)
        if m is not None:
            found.append(m)
    found.sort(key=lambda m: (not m.missing, m.score, m.hits), reverse=True)
    return found[0] if found else None


def _match(
    t: Template, text: str, lines: list[Line], cells: list[str], sheet_name: str | None
) -> Match | None:
    low = _norm(text)
    if any(_norm(a) in low for a in t.absent):
        return None
    if t.kind == "marker":
        for pattern in t.markers:
            if m := re.search(pattern, text, re.IGNORECASE):
                return _finish(t, 1.0, 1, 1, (Landmark(m.group(0), None),), text)
        return None
    if t.kind == "sheet":
        if t.sheet_name and not (sheet_name and re.search(t.sheet_name, sheet_name, re.I)):
            return None
        have = {_norm(c) for c in cells}
        hit = [a for a in t.anchors if _norm(a.text) in have]
        return _score(t, hit, [Landmark(a.text, None) for a in hit], text)
    hits: list[Anchor] = []
    marks: list[Landmark] = []
    for a in t.anchors:
        ln = _find(a.text, lines, a.box, a.tol) if lines else None
        if ln is None and not lines and _norm(a.text) in low:  # no positions known: text only
            hits.append(a)
            marks.append(Landmark(a.text, None))
        elif ln is not None:
            hits.append(a)
            marks.append(Landmark(a.text, ln.box))
    return _score(t, hits, marks, text)


def _score(t: Template, hits: list[Anchor], marks: list[Landmark], text: str) -> Match | None:
    total = sum(a.weight for a in t.anchors) or 1.0
    score = sum(a.weight for a in hits) / total
    if score < t.threshold or any(a.required and a not in hits for a in t.anchors):
        return None
    return _finish(t, round(score, 3), len(hits), len(t.anchors), tuple(marks), text)


def _finish(
    t: Template, score: float, hits: int, of: int, marks: tuple[Landmark, ...], text: str
) -> Match:
    values = read_fields(t, text)
    missing = tuple(f for f in t.required if not values.get(f))
    return Match(t, score, hits, of, marks, values, missing)


# --- fields and titles ---------------------------------------------------------------------


def read_fields(t: Template, text: str) -> dict[str, str]:
    """Each field is a list of patterns tried in order; the first `value` group found wins."""
    out: dict[str, str] = {}
    for name, patterns in t.fields.items():
        for pattern in patterns:
            if m := re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
                # OCR drops the space after a comma ("September 30,2025")
                value = re.sub(r",(?=\d)", ", ", " ".join(m.group("value").split()))
                if name == "document_date":
                    d = nz.to_date(value)
                    value = d.isoformat() if d else value
                out[name] = value
                break
    return out


_COMPANY_WORDS = re.compile(r"\b(insurance|company|co|inc|corp|corporation|llc)\b\.?", re.I)


def _filter(value: str, name: str) -> str:
    match name:
        case "title":
            return value.title()
        case "company":
            return " ".join(_COMPANY_WORDS.sub("", value).split()).title()
        case "years":
            years = re.findall(r"(?:19|20)\d{2}", value)
            return "-".join(dict.fromkeys(years)) if years else value
        case "quarter":
            d = nz.to_date(value)
            return f"Q{(d.month - 1) // 3 + 1} {d.year}" if d else value
        case "person":
            last, _, first = value.partition(",")
            return f"{last.strip().title()}, {first.strip().title()}" if first else value.title()
        case _:
            return value


def render_title(pattern: str, fields: dict[str, str]) -> str:
    """'Loss run, {carrier|company}, {period|years}' with the fields read from the page.
    Parts whose field is missing are dropped, with their separator."""

    def part(m: re.Match[str]) -> str:
        name, _, filters = m.group(1).partition("|")
        value = fields.get(name.strip(), "")
        for f in filter(None, filters.split("|")):
            value = _filter(value, f.strip()) if value else value
        return value

    rendered = re.sub(r"\{([^}]+)\}", part, pattern)
    return re.sub(r"(,\s*)+(?=,|$)", "", re.sub(r",\s*,", ",", rendered)).strip(" ,")


def layout_audits(signals: list[dict[str, Any]]) -> dict[str, list[int]]:
    """Per layout: pages it typed, pages the model also read (the audit), and how many of those
    the model agreed on. Agreeing means the model read the layout's type, or a type the layout
    is known to be read as. The one count the Rules library and the Evals page both show."""
    known = {t.id: t for t in load(active_only=False)}
    out: dict[str, list[int]] = {}
    for sig in signals:
        if sig.get("method") != "layout":
            continue
        tid = str(sig.get("template"))
        row = out.setdefault(tid, [0, 0, 0])
        row[0] += 1
        audit = sig.get("audit")
        if audit:
            row[1] += 1
            t = known.get(tid)
            model = str(audit.get("model_type"))
            row[2] += bool(
                t and (model == t.doc_type.value or model in {d.value for d in t.also_reads_as})
            )
    return out
