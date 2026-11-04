"""Evidence behind any value: a field with every candidate and the facts (page, box or cell,
quote, confidence) each one rests on. Personal data is masked; an underwriter can reveal one
field at a time, and each reveal is recorded in the audit chain."""

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.api.auth import ContentViewer, Revealer
from lane.api.deps import SessionDep, SettingsDep
from lane.api.extractions import FactOut, fact_out
from lane.api.privacy import Masker, masker_for
from lane.audit import chain
from lane.db.models import (
    Conflict,
    Document,
    Fact,
    FieldResolution,
    ResolvedEntity,
    ResolvedField,
)
from lane.resolve.compare import EXPECTED

router = APIRouter(prefix="/api/v1", tags=["evidence"])


class EvidenceFact(FactOut):
    document_id: str
    document_title: str
    doc_type: str


class SourceOut(BaseModel):
    document_id: str
    title: str
    doc_type: str
    note: str | None
    as_of: str | None
    facts: list[EvidenceFact]
    verified: bool  # False: not found on the page, so it decides nothing


class CandidateOut(BaseModel):
    value: Any
    display: str | None
    superseded: str | None
    sources: list[SourceOut]


class FieldConflict(BaseModel):
    id: str
    type: str
    title: str
    hint: str | None
    blocking_rules: list[str]


class CorrectionOut(BaseModel):
    """A person's correction: their value, what the field showed before, who and why."""

    display: str
    before: str | None
    reason: str
    actor: str
    at: str


class FieldDetail(BaseModel):
    id: str
    submission_id: str
    entity_id: str
    entity_kind: str
    entity_label: str
    attribute: str
    label: str
    state: str
    resolution: str | None
    value: Any
    value_display: str | None
    notes: list[str]
    invalid: list[str]
    personal_data: bool  # masked here; an underwriter can reveal it
    conflict: FieldConflict | None
    candidates: list[CandidateOut]
    kind: str  # how a typed correction is read
    expected: str  # what to type when correcting, e.g. "a date such as 2026-12-15"
    correction: CorrectionOut | None


class RevealOut(BaseModel):
    id: str
    value: Any
    value_display: str | None
    candidates: list[str | None]


def _facts(session: Session, ids: list[str], mask: Masker) -> dict[str, EvidenceFact]:
    if not ids:
        return {}
    rows = session.execute(
        select(Fact, Document.title, Document.doc_type)
        .join(Document, Document.id == Fact.document_id)
        .where(Fact.id.in_(ids))
    ).all()
    return {
        f.id: EvidenceFact(
            **fact_out(f, mask).model_dump(),
            document_id=f.document_id,
            document_title=title,
            doc_type=doc_type,
        )
        for f, title, doc_type in rows
    }


def _field(session: Session, field_id: str) -> ResolvedField:
    f = session.get(ResolvedField, field_id)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Field not found")
    return f


@router.get("/fields/{field_id}", operation_id="get_field")
def get_field(field_id: str, session: SessionDep, settings: SettingsDep) -> FieldDetail:
    f = _field(session, field_id)
    entity = session.get(ResolvedEntity, f.entity_id)
    assert entity is not None
    mask = masker_for(session, settings, f.submission_id)
    candidates = mask.candidates(f.attribute, f.candidates)
    fact_ids = [
        str(i) for c in f.candidates for s in c.get("sources", []) for i in s.get("fact_ids", [])
    ]
    facts = _facts(session, fact_ids, mask)
    value, display = mask.field_value(f.attribute, f.value, f.value_display)
    conflict = session.scalars(select(Conflict).where(Conflict.field_id == f.id)).first()
    fix = session.get(FieldResolution, f.id)
    correction = (
        CorrectionOut(
            display=str(mask.field_value(f.attribute, fix.value, fix.display)[1]),
            before=mask.field_value(f.attribute, fix.before, fix.before)[1]
            if fix.before not in (None, "missing", "conflict", "invalid")
            else fix.before,
            reason=fix.reason,
            actor=fix.actor,
            at=fix.created_at.isoformat(),
        )
        if fix and fix.kind == "override"
        else None
    )
    return FieldDetail(
        id=f.id,
        submission_id=f.submission_id,
        entity_id=entity.id,
        entity_kind=entity.kind,
        entity_label=entity.label,
        attribute=f.attribute,
        label=f.label,
        state=f.state,
        resolution=f.resolution,
        value=value,
        value_display=display,
        notes=f.notes,
        invalid=f.invalid,
        personal_data=mask.attribute(f.attribute) is not None,
        conflict=FieldConflict(
            id=conflict.id,
            type=conflict.type,
            title=conflict.title,
            hint=conflict.hint,
            blocking_rules=conflict.blocking_rules,
        )
        if conflict
        else None,
        candidates=[
            CandidateOut(
                value=c.get("value"),
                display=c.get("display"),
                superseded=c.get("superseded"),
                sources=[
                    SourceOut(
                        document_id=str(s.get("document_id", "")),
                        title=str(s.get("title", "")),
                        doc_type=str(s.get("doc_type", "")),
                        note=s.get("note"),
                        as_of=s.get("as_of"),
                        verified=bool(s.get("verified", True)),
                        facts=[facts[i] for i in s.get("fact_ids", []) if i in facts],
                    )
                    for s in c.get("sources", [])
                ],
            )
            for c in candidates
        ],
        kind=f.kind,
        expected=EXPECTED.get(f.kind, "a value"),
        correction=correction,
    )


@router.get("/facts", operation_id="list_facts_by_id")
def list_facts_by_id(
    session: SessionDep,
    settings: SettingsDep,
    ids: Annotated[list[str], Query(max_length=500)],
    _viewer: ContentViewer,
) -> list[EvidenceFact]:
    """Facts by id, for analyst footnotes and finding citations."""
    wanted = [i for raw in ids for i in raw.split(",") if i]
    # Any fact that still exists names the submission; one gone must not hide the rest
    first = session.scalars(select(Fact).where(Fact.id.in_(wanted)).limit(1)).first()
    if first is None:
        return []
    found = _facts(session, wanted, masker_for(session, settings, first.submission_id))
    return [found[i] for i in wanted if i in found]


@router.post("/fields/{field_id}/reveal", operation_id="reveal_field")
def reveal_field(field_id: str, session: SessionDep, viewer: Revealer) -> RevealOut:
    f = _field(session, field_id)
    chain.record(
        session,
        actor=viewer.actor,
        action="pii.revealed",
        subject_type="field",
        subject_id=f.id,
        payload={"submission_id": f.submission_id, "field": f.label},
    )
    session.commit()
    return RevealOut(
        id=f.id,
        value=f.value,
        value_display=f.value_display,
        candidates=[c.get("display") for c in f.candidates],
    )
