"""Resolved entities, conflicts and missing information for a submission."""

from dataclasses import asdict
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from lane.api import display as display_
from lane.api.auth import Reviewer
from lane.api.deps import BlobStoreDep, SessionDep, SettingsDep
from lane.api.models import CandidateOut, ResolutionOut, candidates, fact_locations
from lane.api.privacy import masker_for
from lane.api.summaries import Summaries, summarize, unit_names
from lane.db.models import (
    Conflict,
    DecisionRecord,
    Fact,
    MissingInfo,
    ResolvedEntity,
    ResolvedField,
    Submission,
)
from lane.llm.gateway import LlmGateway
from lane.resolve.severity import conflict_order, decline_capable, severity
from lane.resolve.tiers import tier_of
from lane.review.service import (
    OverrideError,
    Rerun,
    clear_override,
    override_field,
    reopen_conflict,
    rerun,
    resolve_conflict,
)
from lane.rules.service import ruleset

router = APIRouter(prefix="/api/v1", tags=["resolution"])


class FieldOut(BaseModel):
    id: str
    attribute: str
    label: str
    state: str
    resolution: str | None
    value: Any
    value_display: str | None
    candidates: list[CandidateOut]
    notes: list[str]
    invalid: list[str]


class EntityOut(BaseModel):
    id: str
    kind: str
    key: str
    label: str
    found_in: list[str]
    details: dict[str, Any]  # raw, for the audit view; screens read summary
    fields: list[FieldOut]
    summary: Summaries


class ConflictOut(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    id: str
    field_id: str
    entity_id: str
    type: str
    title: str
    hint: str | None
    # For masked values: what differs, without revealing it ("Month and day differ: ...")
    difference: str | None = None
    candidates: list[CandidateOut]
    blocking_rules: list[str]
    resolution: ResolutionOut | None
    severity: str = "medium"  # high | medium | low (settings.conflict_severity)
    notes: list[str] = Field(default_factory=lambda: list[str]())  # labelled inferences


class MissingOut(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    id: str
    code: str
    title: str
    detail: str
    category: str
    entity_id: str | None
    document_ids: list[str]
    tier: str = "required"  # required | bind | helpful (settings.missing_tiers)


def _check(session: SessionDep, submission_id: str) -> None:
    if session.get(Submission, submission_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")


@router.get("/submissions/{submission_id}/entities")
def list_entities(
    submission_id: str, session: SessionDep, settings: SettingsDep, kind: str | None = None
) -> list[EntityOut]:
    _check(session, submission_id)
    mask = masker_for(session, settings, submission_id)
    query = select(ResolvedEntity).where(ResolvedEntity.submission_id == submission_id)
    if kind:
        query = query.where(ResolvedEntity.kind == kind)
    entities = session.scalars(query).all()
    kind_of = {e.id: e.kind for e in entities}
    raw: dict[str, list[ResolvedField]] = {}
    fields: dict[str, list[FieldOut]] = {}
    shown: dict[str, dict[str, list[CandidateOut]]] = {}
    for f in session.scalars(
        select(ResolvedField).where(ResolvedField.submission_id == submission_id)
    ):
        value, display = mask.field_value(f.attribute, f.value, f.value_display)
        kind = kind_of.get(f.entity_id, "")
        display = display_.field(kind, f.attribute, display)
        cands = candidates(
            display_.candidates(kind, f.attribute, mask.candidates(f.attribute, f.candidates))
        )
        raw.setdefault(f.entity_id, []).append(f)
        shown.setdefault(f.entity_id, {})[f.attribute] = cands
        fields.setdefault(f.entity_id, []).append(
            FieldOut(
                id=f.id,
                attribute=f.attribute,
                label=f.label,
                state=f.state,
                resolution=f.resolution,
                value=value,
                value_display=display,
                candidates=cands,
                notes=f.notes,
                invalid=f.invalid,
            )
        )
    decision = session.get(DecisionRecord, submission_id)
    # A claim names its unit by VIN; the unit's settled make comes from the vehicle, which a
    # kind-filtered list does not include
    vehicles = list(
        session.scalars(
            select(ResolvedEntity).where(
                ResolvedEntity.submission_id == submission_id, ResolvedEntity.kind == "vehicle"
            )
        )
    )
    vehicle_fields: dict[str, list[ResolvedField]] = {}
    for f in session.scalars(
        select(ResolvedField).where(ResolvedField.entity_id.in_([v.id for v in vehicles]))
    ):
        vehicle_fields.setdefault(f.entity_id, []).append(f)
    summaries = summarize(
        list(entities),
        raw,
        shown,
        params=ruleset().params,
        anchor=date.fromisoformat(decision.anchor_date) if decision else date.today(),
        ages_on=mask.on,
        settings=settings,
        units=unit_names(vehicles, vehicle_fields),
    )
    return [
        EntityOut(
            id=e.id,
            kind=e.kind,
            key=mask.key(e.key),
            label=e.label,
            found_in=e.found_in,
            details=mask.tree(e.details),
            fields=fields.get(e.id, []),
            summary=summaries[e.id],
        )
        for e in entities
    ]


@router.get("/submissions/{submission_id}/conflicts")
def list_conflicts(
    submission_id: str, session: SessionDep, settings: SettingsDep
) -> list[ConflictOut]:
    _check(session, submission_id)
    mask = masker_for(session, settings, submission_id)
    attributes = {
        fid: attribute
        for fid, attribute in session.execute(
            select(ResolvedField.id, ResolvedField.attribute).where(
                ResolvedField.submission_id == submission_id
            )
        )
    }
    kinds = {
        eid: kind
        for eid, kind in session.execute(
            select(ResolvedEntity.id, ResolvedEntity.kind).where(
                ResolvedEntity.submission_id == submission_id
            )
        )
    }
    rows = list(session.scalars(select(Conflict).where(Conflict.submission_id == submission_id)))
    wanted = {
        f
        for c in rows
        for cand in c.candidates
        for s in cand.get("sources", [])
        for f in s.get("fact_ids", [])
    }
    facts = list(
        session.execute(
            select(
                Fact.id, Fact.page_no, Fact.cell_ref, Fact.confidence, Fact.confidence_reasons
            ).where(Fact.id.in_(wanted))
        )
    )
    places = fact_locations([(fid, page, cell) for fid, page, cell, _, _ in facts])
    bands = {
        fid: (band.value, [str(mask.text(r)) for r in reasons])
        for fid, _, _, band, reasons in facts
    }
    declines = decline_capable()
    out = [
        ConflictOut(
            id=c.id,
            field_id=c.field_id,
            entity_id=c.entity_id,
            type=c.type,
            title=c.title,
            hint=c.hint,
            difference=mask.difference(attributes.get(c.field_id, ""), c.candidates),
            candidates=candidates(
                display_.candidates(
                    kinds.get(c.entity_id, ""),
                    attributes.get(c.field_id, ""),
                    mask.candidates(attributes.get(c.field_id, ""), c.candidates),
                ),
                places,
                bands,
            ),
            blocking_rules=c.blocking_rules,
            resolution=ResolutionOut.model_validate(c.resolution) if c.resolution else None,
            notes=list(c.notes or []),
            severity=severity(
                settings,
                kinds.get(c.entity_id, ""),
                attributes.get(c.field_id, ""),
                bool(set(c.blocking_rules) & declines),
            ),
        )
        for c in rows
    ]
    return sorted(out, key=lambda c: conflict_order(bool(c.blocking_rules), c.severity, c.title))


@router.get("/submissions/{submission_id}/missing")
def list_missing(
    submission_id: str, session: SessionDep, settings: SettingsDep
) -> list[MissingOut]:
    _check(session, submission_id)
    return [
        MissingOut(
            id=m.id,
            code=m.code,
            title=m.title,
            detail=m.detail,
            category=m.category,
            entity_id=m.entity_id,
            document_ids=m.document_ids,
            tier=tier_of(settings, m.code),
        )
        for m in session.scalars(
            select(MissingInfo).where(MissingInfo.submission_id == submission_id)
        )
    ]


class ResolveIn(BaseModel):
    candidate: int  # index into the conflict's candidates
    reason: str


class RuleChangeOut(BaseModel):
    rule_id: str
    title: str
    before: str
    after: str
    potential_before: bool
    potential_after: bool


class RerunOut(BaseModel):
    run_id: str
    headline_before: str | None
    headline_after: str | None
    potential_before: bool
    potential_after: bool
    changes: list[RuleChangeOut]


def _rerun_out(r: Rerun) -> RerunOut:
    return RerunOut(
        run_id=r.run_id,
        headline_before=r.headline_before,
        headline_after=r.headline_after,
        potential_before=r.potential_before,
        potential_after=r.potential_after,
        changes=[RuleChangeOut(**asdict(c)) for c in r.changes],
    )


def _conflict(session: SessionDep, conflict_id: str) -> Conflict:
    c = session.get(Conflict, conflict_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conflict not found")
    return c


@router.post("/conflicts/{conflict_id}/resolve", operation_id="resolve_conflict")
def post_resolve(
    conflict_id: str,
    body: ResolveIn,
    session: SessionDep,
    settings: SettingsDep,
    store: BlobStoreDep,
    viewer: Reviewer,
) -> RerunOut:
    """Pick one of the conflicting values, with a reason; resolution and rules run again."""
    c = _conflict(session, conflict_id)
    try:
        resolve_conflict(
            session, c, candidate=body.candidate, reason=body.reason, actor=viewer.actor
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    sub = session.get(Submission, c.submission_id)
    assert sub is not None
    return _rerun_out(rerun(session, store, settings, sub, LlmGateway, trigger="resolution"))


@router.delete("/conflicts/{conflict_id}/resolution", operation_id="reopen_conflict")
def delete_resolution(
    conflict_id: str,
    session: SessionDep,
    settings: SettingsDep,
    store: BlobStoreDep,
    viewer: Reviewer,
) -> RerunOut:
    """Undo a resolution; the documents' disagreement is back in front of the underwriter."""
    c = _conflict(session, conflict_id)
    sub_id = c.submission_id
    reopen_conflict(session, c, actor=viewer.actor)
    sub = session.get(Submission, sub_id)
    assert sub is not None
    return _rerun_out(rerun(session, store, settings, sub, LlmGateway, trigger="reopen"))


class OverrideIn(BaseModel):
    value: str
    reason: str


def _field(session: SessionDep, field_id: str) -> ResolvedField:
    f = session.get(ResolvedField, field_id)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Field not found")
    return f


@router.post("/fields/{field_id}/override", operation_id="override_field")
def post_override(
    field_id: str,
    body: OverrideIn,
    session: SessionDep,
    settings: SettingsDep,
    store: BlobStoreDep,
    viewer: Reviewer,
) -> RerunOut:
    """Correct a value with a reason; the correction stands until cleared, and the rules run
    again."""
    f = _field(session, field_id)
    try:
        override_field(session, f, value=body.value, reason=body.reason, actor=viewer.actor)
    except OverrideError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    sub = session.get(Submission, f.submission_id)
    assert sub is not None
    return _rerun_out(rerun(session, store, settings, sub, LlmGateway, trigger="override"))


@router.delete("/fields/{field_id}/override", operation_id="clear_override")
def delete_override(
    field_id: str,
    session: SessionDep,
    settings: SettingsDep,
    store: BlobStoreDep,
    viewer: Reviewer,
) -> RerunOut:
    """Remove a correction; the documents' value is back."""
    f = _field(session, field_id)
    sub_id = f.submission_id
    if not clear_override(session, f, actor=viewer.actor):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This value has no correction")
    sub = session.get(Submission, sub_id)
    assert sub is not None
    return _rerun_out(rerun(session, store, settings, sub, LlmGateway, trigger="override"))
