"""The evidence board: every value every document gives for every attribute of every entity.

Resolution never picks a winner silently. Agreeing candidates merge; a newer official record
supersedes an older copy (shown, not queued); anything else that disagrees is a conflict with
all candidates, their sources and an authority hint for the person who resolves it.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

from lane.domain import normalize as nz
from lane.resolve.compare import TYPED_KINDS, Compared, compare, explain_unparsed, same_person


@dataclass(frozen=True)
class Source:
    document_id: str
    doc_type: str
    title: str
    fact_ids: tuple[str, ...] = ()
    as_of: date | None = None  # date of the record, used for supersession
    note: str | None = None  # e.g. "derived from the driver schedule"
    # False when the value could not be found on the page it came from; it is shown but never
    # decides anything. Derived values (no facts) and inferred fields are verified.
    verified: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "doc_type": self.doc_type,
            "title": self.title,
            "fact_ids": list(self.fact_ids),
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "note": self.note,
            "verified": self.verified,
        }


def derived_from(sources: Iterable[Source], doc_type: str, note: str) -> list[Source]:
    """A derived value cites the documents it was derived from, each with the explanation as a
    note, so every candidate opens real evidence. One source per document, in a stable order."""
    by_doc: dict[str, Source] = {}
    for src in sources:
        by_doc.setdefault(src.document_id, src)
    return [
        Source(s.document_id, doc_type, s.title, s.fact_ids, s.as_of, note)
        for s in sorted(by_doc.values(), key=lambda s: (s.title, s.document_id))
    ]


@dataclass
class Candidate:
    compared: Compared
    sources: list[Source]
    superseded: str | None = None  # reason, when a newer record replaces this value

    @property
    def verified(self) -> bool:
        """Found on the page in at least one source."""
        return any(s.verified for s in self.sources)

    def as_dict(self) -> dict[str, Any]:
        return {
            "display": self.compared.display,
            "value": self.compared.norm
            if not isinstance(self.compared.norm, date)
            else self.compared.norm.isoformat(),
            "sources": [s.as_dict() for s in self.sources],
            "superseded": self.superseded,
        }


@dataclass
class Field:
    attribute: str
    label: str
    kind: str
    candidates: list[Candidate] = field(default_factory=lambda: list[Candidate]())
    # Resolution output
    # known | conflict | invalid | missing | unverified (read, but not found on the page) |
    # unread (a source that could hold it could not be read)
    state: str = "missing"
    notes: list[str] = field(default_factory=lambda: list[str]())
    resolution: str | None = None  # agreement | single | supersession | mapping
    conflict: dict[str, Any] | None = None  # type, title, hint
    invalid: list[str] = field(default_factory=lambda: list[str]())

    @property
    def active(self) -> list[Candidate]:
        """The candidates that can decide: not replaced by a newer record, and found on the
        page."""
        return [c for c in self.candidates if not c.superseded and c.verified]

    @property
    def value(self) -> Candidate | None:
        active = self.active
        if self.state not in ("known", "invalid") or not active:
            return None
        # Several valid values (phone numbers): the first stands for the field, all are kept
        return active[0] if len(active) == 1 or self.resolution == "multiple" else None


@dataclass
class Entity:
    kind: str
    key: str
    label: str
    fields: dict[str, Field] = field(default_factory=lambda: dict[str, Field]())
    details: dict[str, Any] = field(default_factory=lambda: dict[str, Any]())
    found_in: set[str] = field(default_factory=lambda: set[str]())  # doc types
    sources: dict[str, Source] = field(default_factory=lambda: dict[str, Source]())
    # The facts behind values kept in details (miles per state), named, so a value worked out
    # from them later can cite them. Kept in memory only: never stored, never shown to a model.
    evidence: dict[str, list[str]] = field(default_factory=lambda: dict[str, list[str]]())

    def field(self, attribute: str, label: str, kind: str) -> Field:
        existing = self.fields.get(attribute)
        if existing is None:
            existing = Field(attribute, label, kind)
            self.fields[attribute] = existing
        return existing


class Board:
    def __init__(self) -> None:
        self.entities: dict[tuple[str, str], Entity] = {}

    def entity(self, kind: str, key: str, label: str | None = None) -> Entity:
        found = self.entities.get((kind, key))
        if found is None:
            found = Entity(kind, key, label or key)
            self.entities[(kind, key)] = found
        elif label and found.label == found.key:
            found.label = label
        return found

    def of_kind(self, kind: str) -> list[Entity]:
        return [e for (k, _), e in self.entities.items() if k == kind]

    def add(
        self,
        entity: Entity,
        attribute: str,
        raw: Any,
        source: Source,
        *,
        kind: str = "text",
        label: str | None = None,
    ) -> Candidate | None:
        """Record one source's value. Empty or unparseable values are ignored."""
        compared = compare(kind, raw)
        f = entity.field(attribute, label or attribute.replace("_", " ").capitalize(), kind)
        entity.found_in.add(source.doc_type)
        entity.sources[source.document_id] = source
        if compared is None:
            return self._unparsed(f, kind, raw, source)
        for cand in f.candidates:
            if _agrees(kind, cand.compared, compared):
                _attach(cand, source)
                if kind == "person" and len(str(compared.norm)) > len(str(cand.compared.norm)):
                    cand.compared = _fuller_name(cand.compared, compared)
                elif kind == "company" and len(compared.display) > len(cand.compared.display):
                    # The same company: show the fullest form, with its legal suffix ("Inc")
                    cand.compared = compared
                return cand
        cand = Candidate(compared, [source])
        f.candidates.append(cand)
        return cand

    def _unparsed(self, f: Field, kind: str, raw: Any, source: Source) -> Candidate | None:
        """A printed value that cannot be read is kept as evidence, not dropped: alone it makes
        the field invalid, beside a readable value it is a conflict. Stated absence ("Not
        provided") and values of free-text kinds are not this."""
        text = " ".join(str(raw).split()) if raw is not None else ""
        if not text or kind not in TYPED_KINDS or nz.is_absent(text, kind):
            return None
        note = explain_unparsed(kind, text)
        if note not in f.invalid:
            f.invalid.append(note)
        shown = Compared(("unparsed", text.lower()), None, text)
        for cand in f.candidates:
            if cand.compared.key == shown.key:
                _attach(cand, source)
                return cand
        cand = Candidate(shown, [source])
        f.candidates.append(cand)
        return cand

    def add_compared(
        self,
        entity: Entity,
        attribute: str,
        compared: Compared,
        source: Source,
        *,
        kind: str,
        label: str,
    ) -> Candidate:
        f = entity.field(attribute, label, kind)
        entity.sources[source.document_id] = source
        for cand in f.candidates:
            if cand.compared.key == compared.key:
                _attach(cand, source)
                return cand
        cand = Candidate(compared, [source])
        f.candidates.append(cand)
        return cand


def _reading_order(display: str) -> list[str]:
    """'GARCIA, ANA M' as it is read: ['ANA', 'M', 'GARCIA']."""
    last, comma, rest = display.partition(",")
    words = (rest.split() + last.split()) if comma and rest.strip() else display.split()
    return [w.strip(".,") for w in words if w.strip(".,")]


def _fuller_name(have: Compared, more: Compared) -> Compared:
    """The fullest form of a name, in reading order: 'Ana Garcia', 'GARCIA, ANA M' and
    'GARCIA ANA MARIA' give 'Ana Maria Garcia'. A full word replaces its initial."""
    shown = _reading_order(str(have.display))
    if len(shown) < 2:
        return more
    lower = [w.lower() for w in shown]
    for w in _reading_order(str(more.display)):
        lw = w.lower()
        if lw in lower:
            continue
        initial = next((i for i, s in enumerate(lower) if len(s) == 1 and s == lw[0]), None)
        if initial is not None and len(lw) > 1:
            shown[initial], lower[initial] = w, lw
        elif len(lw) > 1 and not any(
            len(s) > 1 and s[0] == lw[0] and s.startswith(lw) for s in lower
        ):
            shown.insert(len(shown) - 1, w)
            lower.insert(len(lower) - 1, lw)
    upper = str(have.display) == str(have.display).upper()
    words = [w if upper else w.title() for w in shown]
    return Compared(more.key, more.norm, " ".join(words))


def _attach(cand: Candidate, source: Source) -> None:
    """One entry per document: a second reading from the same document adds its facts (its cells
    or pages) to that entry, and its note beside any note the entry has (each says why)."""
    for i, s in enumerate(cand.sources):
        if s.document_id == source.document_id:
            facts = tuple(dict.fromkeys((*s.fact_ids, *source.fact_ids)))
            cand.sources[i] = replace(
                s,
                fact_ids=facts,
                note=s.note
                if not source.note or source.note == s.note
                else (f"{s.note}; {source.note}" if s.note else source.note),
                verified=s.verified or source.verified,
            )
            return
    cand.sources.append(source)


def _agrees(kind: str, a: Compared, b: Compared) -> bool:
    if kind == "person":
        return same_person(a.key, b.key)
    return a.key == b.key


def resolve_field(
    f: Field,
    *,
    supersede_by_date: bool = False,
    conflict_type: str = "value",
    hint: str | None = None,
) -> None:
    """Decide the field's state from its candidates."""
    if supersede_by_date and len(f.candidates) > 1:
        dated = [c for c in f.candidates if any(s.as_of for s in c.sources)]
        if len(dated) == len(f.candidates):
            newest = max(dated, key=lambda c: max(s.as_of for s in c.sources if s.as_of))
            for c in f.candidates:
                if c is not newest:
                    newest_doc = newest.sources[0].title
                    c.superseded = f"Replaced by the newer {newest_doc}"
    active = f.active
    unverified = [c for c in f.candidates if not c.superseded and not c.verified]
    if unverified:
        names = ", ".join(sorted({s.title for c in unverified for s in c.sources}))
        if active:
            f.notes.append(f"A value in {names} was not found on the page and was not used")
        else:
            f.state = "unverified"
            f.notes.append(f"Read from {names} but not found on the page, so it decides nothing")
            return
    if not active:
        f.state = "missing"
        return
    if len(active) == 1:
        f.state = "invalid" if f.invalid else "known"
        if f.resolution is None:
            if any(c.superseded for c in f.candidates):
                f.resolution = "supersession"
            elif len(active[0].sources) > 1:
                f.resolution = "agreement"
            else:
                f.resolution = "single"
        return
    f.state = "conflict"
    f.conflict = {
        "type": conflict_type,
        "title": f"{f.label} differs between documents",
        "hint": hint,
    }
