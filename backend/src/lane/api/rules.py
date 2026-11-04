"""Guideline rule outcomes and the recommended next step."""

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from lane.actions.asks import asks_by_rule
from lane.api.deps import SessionDep
from lane.api.models import ReadingOut, SensitivityOut
from lane.db.models import (
    DecisionRecord,
    MissingInfo,
    ResolvedEntity,
    ResolvedField,
    RuleOutcomeRecord,
    Submission,
)
from lane.extract.catalog import catalog, entries
from lane.overview.service import headline, rule_kind
from lane.rules.service import outcome_counts, ruleset

router = APIRouter(prefix="/api/v1", tags=["rules"])


class MissingRefOut(BaseModel):
    id: str
    code: str
    title: str


# Entities a submission has one of: a field of theirs needs no owner named
SINGLE_ENTITIES = {
    "applicant",
    "agency",
    "operations",
    "fleet",
    "coverage_request",
    "coverage_history",
}


class AskOut(BaseModel):
    text: str  # the request as the agent reads it
    details: list[str]  # the lines under it that concern this rule


class WaitingOut(BaseModel):
    key: str
    label: str


class RuleOut(BaseModel):
    rule_id: str
    title: str
    section: str
    quote: str
    decline_capable: bool
    checked_at_quote: bool  # needs outside data; the underwriter checks it at quote (A07)
    outcome: str
    reason: str
    items: list[str]
    request: str | None
    inputs: list[str]
    potential_decline: bool
    blocking_conflicts: list[str]
    sensitivity: list[SensitivityOut]
    anchor_note: str | None
    reading: str | None  # how the primary check reads the guideline line
    readings: list[ReadingOut]  # every other reading and its outcome
    assumptions: list[str]
    missing: list[MissingRefOut]  # missing items this rule points to
    headline: str  # why it stands, in one sentence: the same line the brief shows
    kind: str  # what the result means for the submission, as on the brief (rule_kind)
    explain: str | None  # what the rule checks, in plain words
    name: str  # short name of what it checks, for lists; falls back to the title
    waiting_on: list[WaitingOut]  # the missing fact it waits on; shared keys are one question
    asked: list[AskOut]  # the agent request items (included) that ask for this rule
    evidence: list[str]  # facts behind stored details the rule read (an MVR, a loss run)
    absent: list[str]  # fields the rule asked for that the submission does not have


class DecisionOut(BaseModel):
    outcome: str
    headline: str
    team: str | None
    team_label: str | None
    route_reason: str
    route_stable: bool
    potential_decline: bool
    reasons: list[str]
    anchor: str
    anchor_date: str
    ruleset_version: str
    ruleset_sha256: str
    counts: dict[str, int]


class RulesOut(BaseModel):
    decision: DecisionOut | None
    rules: list[RuleOut]


@router.get("/submissions/{submission_id}/rules")
def get_rules(submission_id: str, session: SessionDep) -> RulesOut:
    if session.get(Submission, submission_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    d = session.get(DecisionRecord, submission_id)
    decision = (
        DecisionOut(
            outcome=d.outcome,
            headline=d.headline,
            team=d.team,
            team_label=d.team_label,
            route_reason=d.route_reason,
            route_stable=d.route_stable,
            potential_decline=d.potential_decline,
            reasons=d.reasons,
            anchor=d.anchor,
            anchor_date=d.anchor_date,
            ruleset_version=d.ruleset_version,
            ruleset_sha256=d.ruleset_sha256,
            counts=outcome_counts(
                list(
                    session.scalars(
                        select(RuleOutcomeRecord).where(
                            RuleOutcomeRecord.submission_id == d.submission_id
                        )
                    )
                )
            ),
        )
        if d
        else None
    )
    specs = {s.id: s for s in ruleset().rules}
    external = {i for i, s in specs.items() if s.external}
    asked = asks_by_rule(session, submission_id)
    # Whose field each sensitivity row is about; the one applicant or fleet goes unnamed
    owners = {
        fid: (kind, label)
        for fid, kind, label in session.execute(
            select(ResolvedField.id, ResolvedEntity.kind, ResolvedEntity.label)
            .join(ResolvedEntity, ResolvedEntity.id == ResolvedField.entity_id)
            .where(ResolvedField.submission_id == submission_id)
        )
    }

    def sensitivity(row: dict[str, Any]) -> SensitivityOut:
        kind, label = owners.get(str(row.get("field_id")), ("", ""))
        named = kind not in SINGLE_ENTITIES and bool(label)
        return SensitivityOut.model_validate(
            {**row, "subject": label if named else None, "subject_kind": kind if named else None}
        )

    missing_items = list(
        session.scalars(
            select(MissingInfo)
            .where(MissingInfo.submission_id == submission_id)
            .order_by(MissingInfo.code)
        )
    )

    # Facts a rule read, by entity, then in the field catalog's order (not by id)
    order = _catalog_order()
    placed = {
        fid: (label, order.get(attr, len(order)), attr)
        for fid, label, attr in session.execute(
            select(ResolvedField.id, ResolvedEntity.label, ResolvedField.attribute)
            .join(ResolvedEntity, ResolvedEntity.id == ResolvedField.entity_id)
            .where(ResolvedField.submission_id == submission_id)
        )
    }

    def in_order(ids: list[str]) -> list[str]:
        return sorted(ids, key=lambda f: placed.get(f, ("~", len(order), f)))

    def linked(rule_id: str) -> list[MissingRefOut]:
        prefixes = specs[rule_id].missing if rule_id in specs else []
        return [
            MissingRefOut(id=m.id, code=m.code, title=m.title)
            for m in missing_items
            if any(m.code.startswith(p) for p in prefixes)
        ]

    rules = [
        RuleOut(
            rule_id=r.rule_id,
            title=r.title,
            section=r.section,
            quote=r.quote,
            decline_capable=r.decline_capable,
            checked_at_quote=r.rule_id in external,
            outcome=r.outcome,
            reason=r.reason,
            items=r.items,
            request=r.request,
            inputs=in_order(r.inputs),
            potential_decline=r.potential_decline,
            blocking_conflicts=r.blocking_conflicts,
            sensitivity=[sensitivity(s) for s in r.sensitivity],
            anchor_note=r.anchor_note,
            reading=specs[r.rule_id].reading if r.rule_id in specs else None,
            readings=[ReadingOut.model_validate(x) for x in r.readings],
            assumptions=r.assumptions,
            missing=linked(r.rule_id),
            headline=headline(r),
            kind=rule_kind(r),
            explain=specs[r.rule_id].explain if r.rule_id in specs else None,
            name=(specs[r.rule_id].name if r.rule_id in specs else None) or r.title,
            waiting_on=[WaitingOut.model_validate(w) for w in r.waiting_on],
            asked=[AskOut(text=t, details=d) for t, d in asked.get(r.rule_id, [])],
            evidence=r.evidence,
            absent=r.absent,
        )
        for r in session.scalars(
            select(RuleOutcomeRecord)
            .where(RuleOutcomeRecord.submission_id == submission_id)
            .order_by(RuleOutcomeRecord.ordinal)
        )
    ]
    return RulesOut(decision=decision, rules=rules)


class RulesetRuleOut(BaseModel):
    id: str
    title: str
    section: str
    quote: str
    decline: bool
    request: str | None
    covers: list[str]
    assumptions: list[str]
    explain: str | None
    name: str


class RulesetOut(BaseModel):
    ruleset: str
    version: str
    guideline: str
    sha256: str
    params: dict[str, Any]
    teams: dict[str, str]
    request_groups: dict[str, str]
    assumptions: dict[str, str]
    rules: list[RulesetRuleOut]


@router.get("/ruleset", operation_id="get_ruleset")
def get_ruleset() -> RulesetOut:
    """The guideline as rules: parameters, teams and every rule with the line it enforces."""
    r = ruleset()
    return RulesetOut(
        ruleset=r.ruleset,
        version=r.version,
        guideline=r.guideline,
        sha256=r.sha256,
        params=r.params,
        teams=r.teams,
        request_groups=r.request_groups,
        assumptions=r.assumptions,
        rules=[
            RulesetRuleOut(
                id=s.id,
                title=s.title,
                section=s.section,
                quote=s.quote,
                decline=s.decline,
                request=s.request,
                covers=s.covers,
                explain=s.explain,
                name=s.name or s.title,
                assumptions=sorted(
                    {*s.assumptions, *(rd.assumption for rd in s.readings if rd.assumption)}
                ),
            )
            for s in r.rules
        ],
    )


def _catalog_order() -> dict[str, int]:
    """Each attribute's first position in the field catalog."""
    out: dict[str, int] = {}
    cat = catalog()
    for doc_type in cat.doc_types:
        for e in entries(doc_type):
            if e.spec.attribute:
                out.setdefault(e.spec.attribute, len(out))
    return out
