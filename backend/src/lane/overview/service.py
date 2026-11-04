"""The decision brief for one submission: route, analyst summary, what blocks a quote, and the
numbers an underwriter checks first. Every item links to what it rests on."""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lane.actions.service import included_sha
from lane.db.models import (
    AgentRequestItem,
    AnalysisRecord,
    Conflict,
    DecisionRecord,
    EmailDraft,
    LlmCall,
    ResolvedEntity,
    ResolvedField,
    RuleOutcomeRecord,
    Run,
)
from lane.domain.normalize import as_date as _date
from lane.domain.normalize import as_decimal as _num
from lane.rules.predicates import power_unit_counts
from lane.rules.service import ruleset
from lane.rules.view import SubmissionView

REQUIREMENTS_SECTION = "Submission requirements"


@dataclass
class Blocker:
    kind: str  # potential_decline | decline | missing_document | blocking_conflict | refer | unknown | flag
    title: str
    detail: str
    rule_id: str | None = None
    conflict_id: str | None = None
    field_id: str | None = None
    headline: str | None = None  # the short reason, for one-line summaries


_SENTENCE = re.compile(r"(?<=[a-z0-9)%])\. (?=[A-Z])")


def headline(r: RuleOutcomeRecord) -> str:
    """Why a rule blocks, in one sentence. A potential decline that comes from another reading
    of the guideline is explained by that reading, not by the reading that passed."""
    reason = r.reason
    if r.potential_decline:
        alt = next((x for x in r.readings if x.get("outcome") == "decline"), None)
        if alt is not None:
            reason = str(alt["reason"])
    # The first sentence, whole: a trailing list (the counts behind a route) is the substance
    return _SENTENCE.split(reason, maxsplit=1)[0].rstrip(".")


@dataclass
class KeyNumber:
    key: str
    label: str
    value: str
    note: str | None = None
    conflict: bool = False
    field_id: str | None = None  # opens the evidence lens
    entity_ids: list[str] = field(default_factory=lambda: list[str]())


_ORDER = [
    "potential_decline",
    "decline",
    "missing_document",
    "blocking_conflict",
    "refer",
    "unknown",
    "flag",
    "check",  # needs outside data; the underwriter checks it at quote (A07)
]


def checks_at_quote(session: Session, sid: str) -> list[str]:
    """External checks still open (A07): what the underwriter has to look up at quote."""
    specs = {s.id: s for s in ruleset().rules if s.external}
    return [
        specs[r.rule_id].check or specs[r.rule_id].title
        for r in session.scalars(
            select(RuleOutcomeRecord)
            .where(RuleOutcomeRecord.submission_id == sid, RuleOutcomeRecord.outcome == "unknown")
            .order_by(RuleOutcomeRecord.ordinal)
        )
        if r.rule_id in specs
    ]


def rule_name(r: RuleOutcomeRecord) -> str:
    """The short name of what a rule checks ("Driver CDLs"), or its title when it has none."""
    spec = next((s for s in ruleset().rules if s.id == r.rule_id), None)
    return (spec.name if spec else None) or r.title


def rule_kind(r: RuleOutcomeRecord) -> str:
    """What a rule's result means for the submission, the same on the Brief and the Rules tab:
    summary (the final route), potential_decline, decline, missing_document, check (needs outside
    data, at quote), refer, unknown, flag, pass or n/a."""
    spec = next((s for s in ruleset().rules if s.id == r.rule_id), None)
    if spec is not None and spec.predicate == "final_route":
        return "summary"  # sums up the rules: never a blocker or a reason of its own
    if r.potential_decline:
        return "potential_decline"
    if r.outcome == "refer" and r.section == REQUIREMENTS_SECTION:
        return "missing_document"
    if r.outcome == "unknown" and spec is not None and spec.external:
        return "check"
    return r.outcome


# The Rules page's groups, in its words: what needs a person, what cannot be checked yet, notes
_GROUPS = (
    ({"decline", "potential_decline", "missing_document", "refer"}, "need you", "needs you"),
    ({"unknown", "check"}, "can't check yet", "can't check yet"),
    ({"flag"}, "notes", "note"),
    ({"pass"}, "passed", "passed"),
    ({"n/a"}, "not applicable", "not applicable"),
)


def group_counts(records: list[RuleOutcomeRecord]) -> str:
    """'7 need you, 7 can't check yet, 5 notes, 5 passed, 4 not applicable'; the final route
    sums the others up and is not counted."""
    kinds = [rule_kind(r) for r in records]
    out: list[str] = []
    for members, many, one in _GROUPS:
        n = sum(1 for k in kinds if k in members)
        if n:
            out.append(f"{n} {one if n == 1 else many}")
    return ", ".join(out)


def blockers(session: Session, sid: str) -> list[Blocker]:
    out: list[Blocker] = []
    for r in session.scalars(
        select(RuleOutcomeRecord)
        .where(RuleOutcomeRecord.submission_id == sid)
        .order_by(RuleOutcomeRecord.ordinal)
    ):
        kind = rule_kind(r)
        if kind not in _ORDER:
            continue
        out.append(
            Blocker(
                kind,
                f"{r.rule_id} {rule_name(r)}",
                r.reason,
                rule_id=r.rule_id,
                headline=headline(r),
            )
        )
    for c in session.scalars(select(Conflict).where(Conflict.submission_id == sid)):
        if c.blocking_rules:
            out.append(
                Blocker(
                    "blocking_conflict",
                    c.title,
                    f"Changes {', '.join(c.blocking_rules)} depending on which value is right",
                    conflict_id=c.id,
                    field_id=c.field_id,
                    headline=c.title,
                )
            )
    return sorted(out, key=lambda b: _ORDER.index(b.kind))


def _unit_source(note: str) -> str:
    """'5 (quick quote form; vehicle schedule plus 1 found only in loss history)' -> 'stated'."""
    first = note.split("(", 1)[-1].split(";")[0].lower()
    if "plus" in first:
        return "with units found only in claims"
    if "schedule" in first:
        return "scheduled"
    return "stated"


def key_numbers(session: Session, sid: str, anchor: date) -> list[KeyNumber]:
    entities = list(
        session.scalars(select(ResolvedEntity).where(ResolvedEntity.submission_id == sid))
    )
    fields: dict[tuple[str, str], ResolvedField] = {
        (f.entity_id, f.attribute): f
        for f in session.scalars(select(ResolvedField).where(ResolvedField.submission_id == sid))
    }

    def of(kind: str) -> list[ResolvedEntity]:
        return [e for e in entities if e.kind == kind]

    def value(e: ResolvedEntity, attribute: str) -> Any:
        f = fields.get((e.id, attribute))
        return f.value if f is not None and f.state in ("known", "invalid") else None

    out: list[KeyNumber] = []
    fleet = next(iter(of("fleet")), None)
    units = fields.get((fleet.id, "power_units")) if fleet else None
    scheduled = [v for v in of("vehicle") if v.details.get("scheduled")]
    if units is not None:
        # The counts the route uses (ROUTE-01), so the header and the rules never disagree;
        # shown compactly by where each comes from ("4 scheduled, 5 stated")
        counted = power_unit_counts(SubmissionView.load(session, sid))
        counts = [n for n, _ in counted]
        named = [f"{n} {_unit_source(note)}" for n, note in counted]
        sources = [_unit_source(note) for _, note in counted]
        if len(counts) > 1 and len(set(sources)) == len(sources):
            shown, extra = ", ".join(named[:2]), "; ".join(named[2:])
        else:  # sources that do not tell the counts apart: just the counts
            shown = (
                ", ".join(map(str, counts[:-1])) + f" or {counts[-1]}"
                if len(counts) > 1
                else str(units.value_display)
            )
            named, extra = named[:2], ""
        out.append(
            KeyNumber(
                "power_units",
                "Power units",
                shown,
                f"Also {extra}" if extra else f"{len(scheduled)} on the vehicle schedule",
                conflict=units.state == "conflict" or len(counts) > 1,
                field_id=units.id,
            )
        )
    listed = [d for d in of("driver") if d.details.get("scheduled")]
    # A driver excluded by name is listed but does not drive (pending until confirmed)
    excluded = [d for d in listed if d.details.get("excluded")]
    drivers = [d for d in listed if not d.details.get("excluded")]
    unlisted = [d for d in of("driver") if not d.details.get("scheduled")]
    notes: list[str] = []
    if excluded:
        pending = any(
            cast(dict[str, Any], d.details["excluded"]).get("status") != "confirmed"
            for d in excluded
        )
        notes.append(
            f"{len(excluded)} excluded by name" + (", pending confirmation" if pending else "")
        )
    if unlisted:
        notes.append(f"plus {len(unlisted)} found only in loss history")
    out.append(
        KeyNumber(
            "drivers",
            "Drivers",
            str(len(drivers)),
            "; ".join(notes) or None,
            entity_ids=[d.id for d in drivers + excluded + unlisted],
        )
    )

    since = anchor.replace(year=anchor.year - 3)
    claims = [
        c for c in of("claim") if (d := _date(value(c, "date_of_loss"))) is None or d >= since
    ]
    incurred = sum((_num(value(c, "incurred")) or Decimal(0)) for c in claims)
    open_claims = [c for c in claims if "open" in str(value(c, "status") or "").lower()]
    out.append(
        KeyNumber(
            "incurred_3y",
            "Incurred, last 3 years",
            f"${incurred:,.0f}",
            f"{len(claims)} claims",
            entity_ids=[c.id for c in claims],
        )
    )
    out.append(
        KeyNumber(
            "open_claims",
            "Open claims",
            str(len(open_claims)),
            f"${sum((_num(value(c, 'reserved')) or Decimal(0)) for c in open_claims):,.0f} reserved"
            if open_claims
            else None,
            entity_ids=[c.id for c in open_claims],
        )
    )

    quarters = of("ifta_quarter")
    applicant = next(iter(of("applicant")), None)
    home = str(value(applicant, "mailing_state") or "") if applicant else ""
    total = Decimal(0)
    home_miles = Decimal(0)
    for q in quarters:
        total += _num(q.details.get("total_miles")) or Decimal(0)
        by_state = cast(dict[str, Any], q.details.get("miles_by_state") or {})
        home_miles += _num(by_state.get(home)) or Decimal(0)
    if quarters and total:
        out.append(
            KeyNumber(
                "annual_miles",
                "Annual miles",
                f"{total * 4 / len(quarters):,.0f}",
                f"from {len(quarters)} IFTA quarters",
                entity_ids=[q.id for q in quarters],
            )
        )
        if home:
            out.append(
                KeyNumber(
                    "outside_home",
                    f"Miles outside {home}",
                    f"{(total - home_miles) / total * 100:.0f}%",
                    entity_ids=[q.id for q in quarters],
                )
            )
    return out


def run_cost(session: Session, sid: str) -> float:
    pipeline = session.scalar(
        select(func.sum(LlmCall.cost_usd))
        .join(Run, Run.id == LlmCall.run_id)
        .where(Run.submission_id == sid)
    )
    analysis = session.get(AnalysisRecord, sid)
    # Analyst runs from the CLI have no run id; count the latest analysis once.
    return round(
        float(pipeline or 0) + (analysis.cost_usd if analysis and not analysis.run_id else 0), 4
    )


def action_counts(session: Session, sid: str) -> tuple[dict[str, int], bool | None]:
    counts: dict[str, int] = {}
    for group, n in session.execute(
        select(AgentRequestItem.group, func.count())
        .where(AgentRequestItem.submission_id == sid, AgentRequestItem.included)
        .group_by(AgentRequestItem.group)
    ):
        counts[group] = n
    draft = session.scalars(
        select(EmailDraft)
        .where(EmailDraft.submission_id == sid)
        .order_by(EmailDraft.version.desc())
    ).first()
    return counts, (draft.items_sha256 == included_sha(session, sid)) if draft else None


def anchor_of(session: Session, sid: str) -> date:
    decision = session.get(DecisionRecord, sid)
    return (_date(decision.anchor_date) if decision else None) or date.today()


def known_values(session: Session, sid: str) -> dict[tuple[str, str], str]:
    """Display values of known fields by (entity kind, attribute). A conflicted field shows its
    first value; `conflicted_fields` says which are in conflict, so a screen can mark them."""
    out: dict[tuple[str, str], str] = {}
    for kind, f in session.execute(
        select(ResolvedEntity.kind, ResolvedField)
        .join(ResolvedEntity, ResolvedEntity.id == ResolvedField.entity_id)
        .where(ResolvedField.submission_id == sid, ResolvedEntity.kind.in_(("applicant", "agency")))
    ):
        if f.state in ("known", "invalid") and f.value_display:
            out[(kind, f.attribute)] = f.value_display
        elif f.state == "conflict":
            first = next((c for c in f.candidates if not c.get("superseded")), None)
            if first is not None:
                out[(kind, f.attribute)] = str(first.get("display"))
    return out


def conflicted_fields(session: Session, sid: str) -> list[str]:
    """'agency.name', 'applicant.entity_form': applicant and agency fields in conflict."""
    return sorted(
        f"{kind}.{attribute}"
        for kind, attribute in session.execute(
            select(ResolvedEntity.kind, ResolvedField.attribute)
            .join(ResolvedEntity, ResolvedEntity.id == ResolvedField.entity_id)
            .where(
                ResolvedField.submission_id == sid,
                ResolvedEntity.kind.in_(("applicant", "agency")),
                ResolvedField.state == "conflict",
            )
        )
    )
