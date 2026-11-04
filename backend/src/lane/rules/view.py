"""What rules can see: resolved entities and fields, read-tracked and overridable.

Every field a rule reads is recorded, so the engine knows which inputs decided an outcome. For
sensitivity analysis a field can be overridden with one of its candidate values and the rule
re-evaluated: if the outcome changes, that conflict blocks the rule.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.db.models import (
    Document,
    DocumentExtraction,
    ExtractionStatus,
    Fact,
    Page,
    QualityBand,
    ResolvedEntity,
    ResolvedField,
)
from lane.domain.order import natural_key
from lane.resolve.unread import UnreadSource, unread_sources


@dataclass
class FieldView:
    id: str
    label: str
    state: str
    value: Any
    display: str | None
    candidates: list[dict[str, Any]]

    @property
    def known(self) -> bool:
        return self.state in ("known", "invalid") and self.value is not None


@dataclass
class EntityView:
    id: str
    kind: str
    key: str
    label: str
    details: dict[str, Any]
    found_in: list[str]
    _fields: dict[str, FieldView]
    _view: "SubmissionView"

    def field(self, attribute: str) -> FieldView | None:
        f = self._fields.get(attribute)
        if f is None:
            # Asked for and not in the submission: what the rule rests on includes the absence
            self._view.absent.add(f"{self.label}: {attribute_label(attribute)}")
            return None
        self._view.reads.add(f.id)
        override = self._view.overrides.get(f.id)
        if override is not None:
            return FieldView(f.id, f.label, "known", override[0], override[1], f.candidates)
        return f

    def value(self, attribute: str) -> Any:
        f = self.field(attribute)
        return f.value if f is not None and f.known else None

    def peek_field(self, attribute: str) -> FieldView | None:
        return self._fields.get(attribute)

    def peek(self, attribute: str) -> Any:
        """A known value read for scoping only: not recorded as an input of the rule."""
        f = self._fields.get(attribute)
        return f.value if f is not None and f.known else None

    def display(self, attribute: str) -> str | None:
        f = self.field(attribute)
        return f.display if f is not None and f.known else None

    @property
    def scheduled(self) -> bool:
        return bool(self.details.get("scheduled"))


_ACRONYMS = {"fein", "ssn", "mc", "dot", "vin", "cdl", "ifta", "mvr", "um", "uim"}


def attribute_label(attribute: str) -> str:
    """'fein' -> 'FEIN', 'stated_value' -> 'Stated value', for a field that was never found."""
    words = attribute.removeprefix("q_").split("_")
    out = [w.upper() if w in _ACRONYMS else w for w in words]
    text = " ".join(out)
    return text[0].upper() + text[1:] if text else attribute


def _fact_ids(node: Any) -> list[str]:
    """Every id under a 'fact_ids' key, anywhere in a stored detail."""
    if isinstance(node, dict):
        found: list[str] = []
        for k, v in cast(dict[str, Any], node).items():
            if k == "fact_ids" and isinstance(v, list):
                found += [str(x) for x in cast(list[Any], v)]
            else:
                found += _fact_ids(v)
        return found
    if isinstance(node, list):
        return [x for item in cast(list[Any], node) for x in _fact_ids(item)]
    return []


class TrackedDetails(dict[str, Any]):
    """An entity's stored details that note the facts behind whatever a rule reads from them
    (an MVR's events, a loss run's valuation), so a rule cites what it read, not only fields."""

    def __init__(self, data: dict[str, Any], view: "SubmissionView") -> None:
        super().__init__(data)
        self._view = view

    def __getitem__(self, key: str) -> Any:
        value = super().__getitem__(key)
        self._view.detail_facts.update(_fact_ids(value))
        return value

    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        value = super().get(key, default)
        self._view.detail_facts.update(_fact_ids(value))
        return value


@dataclass(frozen=True)
class UnverifiedRead:
    document: str
    path: str
    rules: frozenset[str]
    why: str = "read once, not verified"


_RULE_ID = re.compile(r"^[A-Z]+-\d{2}$")


def unverified_reads(session: Session, submission_id: str) -> list[UnverifiedRead]:
    """Decision values the model read but no second read confirmed (the document is in review),
    with the rules the field catalog says read them."""
    from lane.extract.catalog import entry_for

    out: list[UnverifiedRead] = []
    rows = session.execute(
        select(Fact.path, Document.title, Document.doc_type)
        .join(Document, Document.id == Fact.document_id)
        .where(
            Fact.submission_id == submission_id,
            Fact.tier == "decision",
            Fact.verified.is_(False),
            Document.superseded_by_id.is_(None),
        )
        .order_by(Document.ordinal, Fact.path)
    )
    for path, title, doc_type in rows:
        e = entry_for(doc_type, path)
        rules = frozenset(u for u in (e.spec.used_by if e else []) if _RULE_ID.match(u))
        if rules:
            out.append(UnverifiedRead(title, path, rules))
    return out + _weak_tables(session, submission_id)


def _weak_tables(session: Session, submission_id: str) -> list[UnverifiedRead]:
    """Every list or table of a document whose pages are degraded or poor, or whose extraction
    failed or awaits review: what it holds may be incomplete, so no rule may pass on it."""
    from lane.extract.catalog import entries

    out: list[UnverifiedRead] = []
    docs = session.execute(
        select(Document.id, Document.title, Document.doc_type)
        .where(Document.submission_id == submission_id, Document.superseded_by_id.is_(None))
        .order_by(Document.ordinal)
    ).all()
    weak_pages = {
        d
        for d, q in session.execute(
            select(Page.document_id, Page.quality).where(Page.submission_id == submission_id)
        )
        if q in (QualityBand.DEGRADED, QualityBand.POOR)
    }
    status = dict(
        session.execute(
            select(DocumentExtraction.document_id, DocumentExtraction.status).where(
                DocumentExtraction.submission_id == submission_id
            )
        ).all()
    )
    for doc_id, title, doc_type in docs:
        st = status.get(doc_id)
        if doc_id in weak_pages:
            why = "a degraded page"
        elif st in (ExtractionStatus.FAILED, ExtractionStatus.NEEDS_REVIEW):
            why = "extraction failed" if st is ExtractionStatus.FAILED else "extraction in review"
        else:
            continue
        tables: dict[str, set[str]] = {}
        for e in entries(doc_type):
            if "[]" not in e.path or not e.is_fact:
                continue
            table = e.path.split("[]", 1)[0]
            tables.setdefault(table, set()).update(u for u in e.spec.used_by if _RULE_ID.match(u))
        out += [
            UnverifiedRead(title, f"{t} (every row)", frozenset(r), why)
            for t, r in tables.items()
            if r
        ]
    return out


@dataclass
class SubmissionView:
    entities: list[EntityView] = field(default_factory=lambda: list[EntityView]())
    fields_by_id: dict[str, FieldView] = field(default_factory=lambda: dict[str, FieldView]())
    reads: set[str] = field(default_factory=lambda: set[str]())
    # Facts behind the stored details a rule read, and fields it asked for that do not exist
    detail_facts: set[str] = field(default_factory=lambda: set[str]())
    absent: set[str] = field(default_factory=lambda: set[str]())
    unread: list[UnreadSource] = field(default_factory=lambda: list[UnreadSource]())
    # Decision values read once and never verified (extraction v2), with the rules that read them
    unverified: list[UnverifiedRead] = field(default_factory=lambda: list[UnverifiedRead]())
    overrides: dict[str, tuple[Any, str]] = field(
        default_factory=lambda: dict[str, tuple[Any, str]]()
    )

    @classmethod
    def load(cls, session: Session, submission_id: str) -> "SubmissionView":
        view = cls()
        view.unread = unread_sources(session, submission_id)
        view.unverified = unverified_reads(session, submission_id)
        fields: dict[str, dict[str, FieldView]] = {}
        for f in session.scalars(
            select(ResolvedField)
            .where(ResolvedField.submission_id == submission_id)
            .order_by(ResolvedField.attribute, ResolvedField.id)
        ):
            fv = FieldView(f.id, f.label, f.state, f.value, f.value_display, f.candidates)
            fields.setdefault(f.entity_id, {})[f.attribute] = fv
            view.fields_by_id[f.id] = fv
        for e in session.scalars(
            select(ResolvedEntity)
            .where(ResolvedEntity.submission_id == submission_id)
            .order_by(ResolvedEntity.kind, ResolvedEntity.key)
        ):
            view.entities.append(
                EntityView(
                    e.id,
                    e.kind,
                    e.key,
                    e.label,
                    TrackedDetails(e.details, view),
                    e.found_in,
                    fields.get(e.id, {}),
                    view,
                )
            )
        return view

    def one(self, kind: str) -> EntityView | None:
        return next((e for e in self.entities if e.kind == kind), None)

    def all(self, kind: str) -> list[EntityView]:
        """Entities of a kind in a stable order (by key), however they were loaded."""
        return sorted(
            (e for e in self.entities if e.kind == kind), key=lambda e: (natural_key(e.key), e.id)
        )

    def date_of(self, kind: str, attribute: str) -> date | None:
        entity = self.one(kind)
        value = entity.value(attribute) if entity else None
        return date.fromisoformat(str(value)) if value else None
