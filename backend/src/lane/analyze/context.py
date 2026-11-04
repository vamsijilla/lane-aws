"""The analyst's view of a submission: resolved facts, conflicts, missing items and rule results.

No raw documents go to the model. Every item carries a short citation id (F12 for a field, E3
for an entity, C2 for a conflict, M4 for a missing item, R:DRV-01 for a rule, X5 for a figure
computed here) that maps back to fact and document ids, so every finding can be traced to its
evidence. Direct identifiers are minimized before anything leaves the server: license numbers
become tokens, dates of birth become ages, street addresses and contact details are withheld,
and third-party claimant names become tokens. The token map never leaves the server.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from itertools import pairwise
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.config import Settings
from lane.db.models import (
    Conflict,
    DecisionRecord,
    MissingInfo,
    ResolvedEntity,
    ResolvedField,
    RuleOutcomeRecord,
)
from lane.domain import normalize as nz
from lane.domain.ages import age_on, application_date
from lane.rules.service import ruleset

_date = nz.as_date
_num = nz.as_decimal


@dataclass
class Ref:
    """What a citation id points at, for the evidence lens."""

    id: str
    kind: str  # field | entity | conflict | missing | rule | figure
    label: str
    target: str  # resolved field id, entity id, conflict id, missing id, rule id, or figure key
    fact_ids: list[str] = field(default_factory=lambda: list[str]())
    document_ids: list[str] = field(default_factory=lambda: list[str]())
    cites: list[str] = field(default_factory=lambda: list[str]())  # figure or rule inputs
    value: str | None = None  # a figure's result and how it was worked out


@dataclass
class Brief:
    data: dict[str, Any]
    refs: dict[str, Ref]
    tokens: dict[str, str]  # token -> original value, server-side only

    def text(self) -> str:
        return json.dumps(self.data, indent=1, ensure_ascii=False, default=str)


def _list(value: Any) -> list[Any]:
    return cast(list[Any], value) if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _origins(sources: Any) -> list[str]:
    """Where a value comes from. A value worked out by code says so and how, so it is never
    read as something a document states."""
    out: set[str] = set()
    for src in _list(sources):
        s = _dict(src)
        if s.get("doc_type") == "derived":
            out.add(f"derived by the system: {s.get('note') or s.get('title')}")
        else:
            out.add(str(s.get("title")))
    return sorted(out)


def _ids(node: Any, key: str) -> Iterable[str]:
    """Every value under `key` (a string or list of strings) anywhere inside a JSON tree."""
    if isinstance(node, dict):
        for k, v in cast(dict[str, Any], node).items():
            if k == key:
                if isinstance(v, str):
                    yield v
                else:
                    yield from (str(x) for x in _list(v))
            else:
                yield from _ids(v, key)
    elif isinstance(node, list):
        for item in cast(list[Any], node):
            yield from _ids(item, key)


class _Builder:
    def __init__(self, session: Session, submission_id: str, settings: Settings) -> None:
        self.session = session
        self.sid = submission_id
        self.settings = settings
        self.refs: dict[str, Ref] = {}
        self.tokens: dict[str, str] = {}
        self._token_of: dict[tuple[str, str], str] = {}
        self._counters: dict[str, int] = {}
        self.field_ref: dict[str, str] = {}  # resolved field id -> F ref
        self.entity_ref: dict[str, str] = {}
        self.ages_on = application_date(session, submission_id) or date.today()

    def next_id(self, prefix: str) -> str:
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"{prefix}{self._counters[prefix]}"

    def token(self, kind: str, raw: str) -> str:
        key = (kind, raw.strip().upper())
        if key not in self._token_of:
            self._counters[kind] = self._counters.get(kind, 0) + 1
            tok = f"{kind}_{self._counters[kind]}"
            self._token_of[key] = tok
            self.tokens[tok] = raw
        return self._token_of[key]

    # --- masking --------------------------------------------------------------------------

    def mask(self, attribute: str, display: Any, value: Any, anchor: date) -> Any:
        if display is None:
            return None
        if attribute in self.settings.analyze_withheld_attributes:
            return "[withheld]"
        if attribute in self.settings.analyze_tokenized_attributes:
            return self.token(attribute.upper(), str(display))
        if attribute == "date_of_birth":
            dob = _date(value) or _date(display)
            return f"age {age_on(dob, self.ages_on)}" if dob else "[withheld]"
        return display

    def clean(self, node: Any) -> Any:
        """Details as the model sees them: no internal ids or raw quotes, people tokenized."""
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for k, v in cast(dict[str, Any], node).items():
                if k in self.settings.analyze_dropped_detail_keys:
                    continue
                if k in self.settings.analyze_tokenized_detail_keys and isinstance(v, str):
                    out[k] = self.token(k.upper(), v)
                else:
                    out[k] = self.clean(v)
            return out
        if isinstance(node, list):
            return [self.clean(x) for x in cast(list[Any], node)]
        return node

    # --- sections -------------------------------------------------------------------------

    def build(self) -> Brief:
        decision = self.session.get(DecisionRecord, self.sid)
        anchor = _date(decision.anchor_date) if decision else None
        anchor = anchor or date.today()

        entities = list(
            self.session.scalars(
                select(ResolvedEntity)
                .where(ResolvedEntity.submission_id == self.sid)
                .order_by(ResolvedEntity.kind, ResolvedEntity.key)
            )
        )
        fields_by_entity: dict[str, list[ResolvedField]] = {}
        for f in self.session.scalars(
            select(ResolvedField)
            .where(ResolvedField.submission_id == self.sid)
            .order_by(ResolvedField.attribute)
        ):
            fields_by_entity.setdefault(f.entity_id, []).append(f)

        entity_rows = [self.entity(e, fields_by_entity.get(e.id, []), anchor) for e in entities]
        conflicts = self.conflicts()
        missing = self.missing()
        rules = self.rules()
        figures = _Figures(self, entities, fields_by_entity, anchor).all()

        data: dict[str, Any] = {
            "decision": (
                {
                    "recommendation": decision.headline,
                    "outcome": decision.outcome,
                    "potential_decline": decision.potential_decline,
                    "route": decision.route_reason,
                    "dates_counted_from": f"{decision.anchor} {decision.anchor_date}",
                }
                if decision
                else None
            ),
            "entities": entity_rows,
            "conflicts": conflicts,
            "missing": missing,
            "rules": rules,
            "figures": figures,
        }
        return Brief(data, self.refs, self.tokens)

    def entity(
        self, e: ResolvedEntity, fields: list[ResolvedField], anchor: date
    ) -> dict[str, Any]:
        ref = self.next_id("E")
        self.entity_ref[e.id] = ref
        row: dict[str, Any] = {"ref": ref, "kind": e.kind, "label": e.label}
        out_fields: list[dict[str, Any]] = []
        entity_facts: list[str] = list(_ids(e.details, "fact_ids"))
        entity_docs: list[str] = list(_ids(e.details, "document_id")) + list(e.found_in)
        for f in fields:
            fref = self.next_id("F")
            self.field_ref[f.id] = fref
            facts = list(_ids(f.candidates, "fact_ids"))
            docs = list(_ids(f.candidates, "document_id"))
            self.refs[fref] = Ref(fref, "field", f"{e.label}: {f.label}", f.id, facts, docs)
            entity_facts += facts
            entity_docs += docs
            item: dict[str, Any] = {"ref": fref, "name": f.label, "state": f.state}
            active = [c for c in f.candidates if not c.get("superseded")]
            if f.state == "conflict":
                item["values"] = [
                    {
                        "value": self.mask(f.attribute, c.get("display"), c.get("value"), anchor),
                        "from": _origins(c.get("sources")),
                    }
                    for c in active
                ]
            else:
                item["value"] = self.mask(f.attribute, f.value_display, f.value, anchor)
                item["from"] = _origins([s for c in active for s in _list(c.get("sources"))])
            if f.notes:
                item["notes"] = f.notes
            if f.invalid:
                item["problems"] = f.invalid
            out_fields.append(item)
        row["fields"] = out_fields
        if e.details:
            row["details"] = self.clean(e.details)
        self.refs[ref] = Ref(
            ref,
            "entity",
            e.label,
            e.id,
            list(dict.fromkeys(entity_facts)),
            list(dict.fromkeys(entity_docs)),
        )
        return row

    def conflicts(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for c in self.session.scalars(
            select(Conflict).where(Conflict.submission_id == self.sid).order_by(Conflict.title)
        ):
            ref = self.next_id("C")
            self.refs[ref] = Ref(
                ref,
                "conflict",
                c.title,
                c.id,
                list(_ids(c.candidates, "fact_ids")),
                list(_ids(c.candidates, "document_id")),
                [self.field_ref[c.field_id]] if c.field_id in self.field_ref else [],
            )
            rows.append(
                {
                    "ref": ref,
                    "field": self.field_ref.get(c.field_id),
                    "title": c.title,
                    "type": c.type,
                    "hint": c.hint,
                    "changes_rules": [f"R:{r}" for r in c.blocking_rules],
                }
            )
        return rows

    def missing(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for m in self.session.scalars(
            select(MissingInfo)
            .where(MissingInfo.submission_id == self.sid)
            .order_by(MissingInfo.code)
        ):
            ref = self.next_id("M")
            self.refs[ref] = Ref(ref, "missing", m.title, m.id, [], list(m.document_ids))
            rows.append({"ref": ref, "category": m.category, "title": m.title, "detail": m.detail})
        return rows

    def rules(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        # The final route sums the other rules up (it is the decision above): never a reason
        summaries = {s.id for s in ruleset().rules if s.predicate == "final_route"}
        for r in self.session.scalars(
            select(RuleOutcomeRecord)
            .where(RuleOutcomeRecord.submission_id == self.sid)
            .order_by(RuleOutcomeRecord.ordinal)
        ):
            if r.rule_id in summaries:
                continue
            ref = f"R:{r.rule_id}"
            # In ref order, not read order: the order fields are read follows database ids,
            # which differ per submission, and the brief must be the same for the same facts
            # (it is the cache key of the expert review)
            inputs = sorted(
                {self.field_ref[i] for i in r.inputs if i in self.field_ref},
                key=lambda ref: (ref[0], int(ref[1:]) if ref[1:].isdigit() else 0, ref),
            )
            self.refs[ref] = Ref(ref, "rule", f"{r.rule_id} {r.title}", r.rule_id, cites=inputs)
            row: dict[str, Any] = {
                "ref": ref,
                "title": r.title,
                "guideline": r.quote,
                "outcome": r.outcome,
                "reason": r.reason,
            }
            if r.items:
                row["items"] = r.items
            if r.potential_decline:
                row["potential_decline"] = True
            if r.anchor_note:
                row["other_date_basis"] = r.anchor_note
            if inputs:
                row["inputs"] = inputs
            rows.append(row)
        return rows


class _Figures:
    """Numbers an underwriter would work out by hand, computed here so the model does not."""

    def __init__(
        self,
        b: _Builder,
        entities: list[ResolvedEntity],
        fields: dict[str, list[ResolvedField]],
        anchor: date,
    ) -> None:
        self.b = b
        self.entities = entities
        self.fields = fields
        self.anchor = anchor
        self.rows: list[dict[str, Any]] = []

    def kind(self, kind: str) -> list[ResolvedEntity]:
        return [e for e in self.entities if e.kind == kind]

    def value(self, e: ResolvedEntity, attribute: str) -> Any:
        f = next((f for f in self.fields.get(e.id, []) if f.attribute == attribute), None)
        return f.value if f is not None and f.state in ("known", "invalid") else None

    def candidates(self, e: ResolvedEntity, attribute: str) -> list[Any]:
        f = next((f for f in self.fields.get(e.id, []) if f.attribute == attribute), None)
        if f is None:
            return []
        if f.state in ("known", "invalid"):
            return [f.value]
        return [c.get("value") for c in f.candidates if not c.get("superseded")]

    def add(
        self, label: str, value: Any, cites: list[str], key: str, working: str | None = None
    ) -> None:
        """A figure and its inputs. The working (how it was computed) is for the evidence lens
        only: the brief row is unchanged, since the brief is the expert review's cache key."""
        ref = self.b.next_id("X")
        facts: list[str] = []
        docs: list[str] = []
        for c in cites:
            r = self.b.refs.get(c)
            if r:
                facts += r.fact_ids
                docs += r.document_ids
        self.b.refs[ref] = Ref(
            ref,
            "figure",
            label,
            key,
            list(dict.fromkeys(facts)),
            list(dict.fromkeys(docs)),
            cites,
            f"{value} = {working}" if working else str(value),
        )
        self.rows.append({"ref": ref, "label": label, "value": value, "from": cites})

    def all(self) -> list[dict[str, Any]]:
        self.mileage()
        self.claims()
        self.cargo()
        self.terms()
        self.growth()
        self.claim_rates()
        return self.rows

    def _unit_terms(self) -> list[tuple[ResolvedEntity, date, int]]:
        """Liability terms with a stated unit count, oldest first."""
        out: list[tuple[ResolvedEntity, date, int]] = []
        for t in self.kind("coverage_term"):
            start, units = _date(t.details.get("start")), _num(self.value(t, "units"))
            if t.details.get("line") == "auto_liability" and start and units is not None:
                out.append((t, start, int(units)))
        return sorted(out, key=lambda x: x[1])

    def growth(self) -> None:
        """The largest growth in units between consecutive terms, against the application's own
        definition of fast growth."""
        terms = self._unit_terms()
        if len(terms) < 2:
            return
        steps = [(b[2] - a[2], a, b) for a, b in pairwise(terms)]
        grow, a, b = max(steps, key=lambda x: x[0])
        limit = self.b.settings.analyze_fast_growth_units
        self.add(
            "Largest growth in power units between consecutive terms",
            f"{grow:+d} units ({a[2]} to {b[2]}, terms starting {a[1].isoformat()} and "
            f"{b[1].isoformat()}); the application defines fast growth as more than {limit} "
            f"units a year, so this is {'fast' if grow > limit else 'not fast'} growth by that "
            f"definition",
            [self.b.entity_ref[a[0].id], self.b.entity_ref[b[0].id]],
            "units:growth:max",
        )

    def claim_rates(self) -> None:
        """Claims per power unit in each term, and the share of claims reported late."""
        terms = self._unit_terms()
        claims = self.kind("claim")
        for t, start, units in terms:
            mine = [c for c in claims if _date(c.details.get("term_start")) == start]
            if units:
                self.add(
                    f"Claims per power unit, term starting {start.isoformat()}",
                    f"{len(mine) / units:.2f} ({len(mine)} claims, {units} units)",
                    [self.b.entity_ref[t.id], *(self.b.entity_ref[c.id] for c in mine)],
                    f"claims:per_unit:{start.isoformat()}",
                )
        lag_limit = self.b.settings.claim_report_lag_days
        dated = [
            (c, (reported - loss).days)
            for c in claims
            if (loss := _date(self.value(c, "date_of_loss")))
            and (reported := _date(self.value(c, "date_reported")))
        ]
        if dated:
            late = sum(1 for _, d in dated if d > lag_limit)
            self.add(
                "Claims reported late",
                f"{late} of {len(dated)} ({late / len(dated):.0%}), late meaning more than "
                f"{lag_limit} days after the loss",
                [self.b.entity_ref[c.id] for c, _ in dated],
                "claims:late_rate",
            )

    def terms(self) -> None:
        """Policy terms that are not a full year often mean a cancellation or a rewrite."""
        low, high = self.b.settings.analyze_full_term_days
        for t in sorted(self.kind("coverage_term"), key=lambda e: e.key):
            start, end = _date(t.details.get("start")), _date(t.details.get("end"))
            if start and end and not low <= (end - start).days <= high:
                self.add(
                    f"{t.label} term length",
                    f"{(end - start).days} days ({start.isoformat()} to {end.isoformat()}), "
                    "not a full annual term",
                    [self.b.entity_ref[t.id]],
                    f"term:length:{t.key}",
                )

    def mileage(self) -> None:
        quarters = sorted(self.kind("ifta_quarter"), key=lambda e: e.key)
        if not quarters:
            return
        applicant = next(iter(self.kind("applicant")), None)
        home = str(self.value(applicant, "mailing_state") or "") if applicant else ""
        cites = [self.b.entity_ref[q.id] for q in quarters]
        total = Decimal(0)
        previous: Decimal | None = None
        for q in quarters:
            miles = _num(q.details.get("total_miles"))
            if miles is None:
                continue
            total += miles
            by_state = {
                k: _num(v) or Decimal(0) for k, v in _dict(q.details.get("miles_by_state")).items()
            }
            parts = [f"{miles:,.0f} miles"]
            if home and by_state and miles:
                outside = (miles - by_state.get(home, Decimal(0))) / miles * 100
                parts.append(f"{outside:.0f}% outside {home}")
            if previous:
                parts.append(f"{(miles - previous) / previous * 100:+.0f}% vs prior quarter")
            for charge in ("penalty", "interest"):
                amount = _num(q.details.get(charge))
                if amount:
                    parts.append(f"{charge} ${amount:,.2f}")
            self.add(f"IFTA {q.key}", ", ".join(parts), [self.b.entity_ref[q.id]], f"ifta:{q.key}")
            previous = miles
        annual = total * 4 / len(quarters)
        self.add(
            f"Annualized miles ({len(quarters)} quarters)",
            f"{annual:,.0f}",
            cites,
            "miles:annual",
            f"the total of {len(quarters)} IFTA quarters, a full year"
            if len(quarters) == 4
            else f"{total:,.0f} miles in {len(quarters)} IFTA quarters, scaled to a year",
        )
        fleet = next(iter(self.kind("fleet")), None)
        counts: set[int] = set()
        if fleet:
            for v in self.candidates(fleet, "power_units"):
                n = _num(v)
                if n:
                    counts.add(int(n))
        scheduled = sum(1 for v in self.kind("vehicle") if v.details.get("scheduled"))
        if scheduled:
            counts.add(scheduled)
        fleet_cites = cites + ([self.b.entity_ref[fleet.id]] if fleet else [])
        for n in sorted(counts):
            self.add(
                f"Annual miles per power unit at {n} units",
                f"{annual / n:,.0f}",
                fleet_cites,
                f"miles:per_unit:{n}",
                f"{annual:,.0f} annualized miles divided by {n} units",
            )

    def claims(self) -> None:
        by_term: dict[str, list[ResolvedEntity]] = {}
        for c in self.kind("claim"):
            ref = self.b.entity_ref[c.id]
            loss = _date(self.value(c, "date_of_loss"))
            reported = _date(self.value(c, "date_reported"))
            if loss and reported:
                self.add(
                    f"Claim {c.key} reported after loss",
                    f"{(reported - loss).days} days",
                    [ref],
                    f"claim:lag:{c.key}",
                    f"reported {reported.isoformat()}, loss {loss.isoformat()}",
                )
            by_term.setdefault(str(c.details.get("term_start") or "unknown"), []).append(c)
        for term, claims in sorted(by_term.items()):
            incurred = sum((_num(self.value(c, "incurred")) or Decimal(0)) for c in claims)
            reserved = sum((_num(self.value(c, "reserved")) or Decimal(0)) for c in claims)
            open_count = sum(
                1 for c in claims if "open" in str(self.value(c, "status") or "").lower()
            )
            self.add(
                f"Claims in the term starting {term}",
                f"{len(claims)} claims, ${incurred:,.0f} incurred, ${reserved:,.0f} reserved, "
                f"{open_count} open",
                [self.b.entity_ref[c.id] for c in claims],
                f"claims:term:{term}",
            )

    def cargo(self) -> None:
        ops = next(iter(self.kind("operations")), None)
        request = next(iter(self.kind("coverage_request")), None)
        if not ops or not request:
            return
        limit = _num(self.value(request, "cargo_limit"))
        highest: Decimal | None = None
        for key in ("commodities_quote", "commodities_application"):
            for item in _list(_dict(ops.details.get(key)).get("items")):
                v = _num(_dict(item).get("value"))
                if v is not None and (highest is None or v > highest):
                    highest = v
        if limit and highest:
            self.add(
                "Highest declared load value vs requested cargo limit",
                f"${highest:,.0f} vs ${limit:,.0f}",
                [self.b.entity_ref[ops.id], self.b.entity_ref[request.id]],
                "cargo:value_vs_limit",
            )


def build_brief(session: Session, submission_id: str, settings: Settings) -> Brief:
    return _Builder(session, submission_id, settings).build()
