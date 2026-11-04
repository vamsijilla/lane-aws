"""Tier 1 of classification: deterministic signals, checked before known layouts or any model.

Printed form identifiers, worksheet header synonyms (a header counts only when its column holds
data), strong key phrases, and lookup or dropdown-value sheets. The vocabulary lives in
config/classification.yaml and is industry wording, never anything from one packet. A signal
decides only when its evidence is clear; otherwise it returns nothing and the next tier decides.
"""

import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from lane.documents.signals import keyword_vote
from lane.documents.types import DOC_TYPE_LABELS, DocType

CONFIG_PATH = Path(__file__).resolve().parents[4] / "config" / "classification.yaml"


class FormIdentifier(BaseModel):
    pattern: str
    doc_type: DocType
    name: str


class Vocabulary(BaseModel):
    version: int
    form_identifiers: list[FormIdentifier]
    sheet_headers: dict[DocType, list[str]]
    sheet_min_headers: int = 3
    sheet_margin: int = 2
    placeholders: list[str] = Field(default_factory=lambda: list[str]())
    lookup_sheet_names: list[str] = Field(default_factory=lambda: list[str]())
    lookup_max_value_chars: int = 60
    key_phrases_min_score: float = 6
    key_phrases_margin: float = 3


@cache
def vocabulary(path: Path = CONFIG_PATH) -> Vocabulary:
    return Vocabulary.model_validate(yaml.safe_load(path.read_text()))


@dataclass(frozen=True)
class Decision:
    """What tier 1 concluded about a page, and from what."""

    doc_type: DocType | None  # None with ignored=True: a lookup sheet, no document
    method: str  # form_identifier | sheet_headers | key_phrases | lookup_sheet
    reason: str
    evidence: list[str] = field(default_factory=lambda: list[str]())
    ignored: bool = False

    @property
    def signals(self) -> dict[str, Any]:
        return {
            "tier1": {
                "method": self.method,
                "doc_type": self.doc_type.value if self.doc_type else None,
                "ignored": self.ignored,
                "evidence": self.evidence,
            }
        }


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9#$ ]+", " ", text.lower()).split())


def _is_header(cell: str, synonym: str) -> bool:
    """'CDL Number' is the header 'cdl number'; 'Driver Type (Company, Owner)' is 'driver type'.
    A synonym must be the whole header or lead it, so 'year' never matches 'years of
    experience'."""
    text = _norm(cell)
    syn = _norm(synonym)
    return text == syn or (text.startswith(syn + " ") and len(text.split()) <= len(syn.split()) + 6)


def decide(
    *,
    text: str,
    layout: dict[str, Any] | None,
    is_sheet: bool,
    sheet_name: str | None,
    sheet_hidden: bool,
    vocab: Vocabulary | None = None,
) -> Decision | None:
    v = vocab or vocabulary()
    for form in v.form_identifiers:
        if re.search(form.pattern, text, re.IGNORECASE):
            return Decision(
                form.doc_type,
                "form_identifier",
                f"Printed form identifier: {form.name}",
                [form.name],
            )
    if is_sheet and layout is not None:
        return _sheet(layout, sheet_name or "", sheet_hidden, v)
    if text:
        return _key_phrases(text, v)
    return None


def _sheet(layout: dict[str, Any], sheet_name: str, hidden: bool, v: Vocabulary) -> Decision | None:
    cells = [c for c in layout.get("cells", []) if str(c.get("v") or "").strip()]
    by_col: dict[int, list[tuple[int, str]]] = {}
    for c in cells:
        by_col.setdefault(int(c["c"]), []).append((int(c["r"]), str(c["v"]).strip()))
    placeholders = {_norm(p) for p in v.placeholders}
    all_synonyms = [s for syns in v.sheet_headers.values() for s in syns]

    def has_data(row: int, col: int) -> bool:
        """A real value below the header, or beside it (label and value forms)."""
        below = [t for r, t in by_col.get(col, []) if row < r <= row + 60]
        beside = [t for c in (col + 1, col + 2, col + 3) for r, t in by_col.get(c, []) if r == row]
        for value in below + beside:
            norm = _norm(value)
            if (
                norm
                and norm not in placeholders
                and not any(_is_header(value, s) for s in all_synonyms)
            ):
                return True
        return False

    hits: dict[DocType, list[str]] = {}
    for c in cells:
        value = str(c["v"])
        for doc_type, synonyms in v.sheet_headers.items():
            matched = next((s for s in synonyms if _is_header(value, s)), None)
            if matched and has_data(int(c["r"]), int(c["c"])):
                found = hits.setdefault(doc_type, [])
                if matched not in found:
                    found.append(matched)
    ranked = sorted(hits.items(), key=lambda kv: len(kv[1]), reverse=True)
    if ranked:
        best, found = ranked[0]
        runner = len(ranked[1][1]) if len(ranked) > 1 else 0
        if len(found) >= v.sheet_min_headers and len(found) - runner >= v.sheet_margin:
            return Decision(
                best,
                "sheet_headers",
                f"Worksheet headers with data read as {DOC_TYPE_LABELS[best].lower()}: "
                f"{', '.join(found[:6])}",
                found,
            )
        return None  # some headers, no clear winner: the next tier decides
    named = any(n in sheet_name.lower() for n in v.lookup_sheet_names)
    short = all(len(str(c.get("v"))) <= v.lookup_max_value_chars for c in cells)
    if (hidden or named) and short:
        why = "hidden" if hidden else f"named '{sheet_name}'"
        return Decision(
            None,
            "lookup_sheet",
            f"Lookup values ({why}, only short list values, no document headers)",
            [why],
            ignored=True,
        )
    return None


def _key_phrases(text: str, v: Vocabulary) -> Decision | None:
    vote = keyword_vote(text)
    if vote.doc_type is None or vote.score < v.key_phrases_min_score:
        return None
    runner = vote.scores.get(vote.runner_up.value, 0.0) if vote.runner_up else 0.0
    if vote.score - runner < v.key_phrases_margin:
        return None
    return Decision(
        vote.doc_type,
        "key_phrases",
        f"Key phrases of a {DOC_TYPE_LABELS[vote.doc_type].lower()} "
        f"(score {vote.score:g}, next {runner:g})",
        [f"{k}: {s:g}" for k, s in vote.scores.items()],
    )
