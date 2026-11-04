"""Run resolution for a submission and persist entities, fields, conflicts and missing items."""

import hashlib
from collections import Counter
from datetime import date
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from lane.audit import chain
from lane.db.models import (
    Conflict,
    Document,
    DocumentExtraction,
    Fact,
    FieldResolution,
    MissingInfo,
    ResolvedEntity,
    ResolvedField,
    Submission,
)
from lane.resolve.board import Board, Entity, Field
from lane.resolve.build import Builder
from lane.resolve.derive import finalize
from lane.resolve.missing import find_missing
from lane.resolve.unread import unread_sources

ACTOR = "system:resolve"


def stable_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


def decides(f: Fact) -> bool:
    """Whether a fact may decide anything. v1 facts: found on the page. v2 facts: a decision
    field needs a verified second read (else it is in review or escalated); crosscheck and
    context fields need grounding."""
    if f.tier is None:
        return f.grounding != "none"
    if f.tier == "decision":
        return f.verified
    return f.grounding not in ("none", "mismatch", "ocr_mismatch")


def build_board(session: Session, submission: Submission) -> tuple[Board, date]:
    docs = list(
        session.scalars(
            select(Document)
            .where(Document.submission_id == submission.id)
            .order_by(Document.ordinal)
        )
    )
    data = {
        x.document_id: x.data
        for x in session.scalars(
            select(DocumentExtraction).where(DocumentExtraction.submission_id == submission.id)
        )
        if x.data is not None
    }
    stored = list(session.scalars(select(Fact).where(Fact.submission_id == submission.id)))
    facts = {(f.document_id, f.path): f.id for f in stored}
    ungrounded = frozenset(f.id for f in stored if not decides(f))
    received = submission.email.received_at.date()
    board = Builder(docs, data, facts, today=received, ungrounded=ungrounded).build()
    finalize(board, today=received)
    return board, received


def resolve_submission(session: Session, submission: Submission) -> dict[str, Any]:
    board, _today = build_board(session, submission)
    missing = find_missing(board, unread_sources(session, submission.id))
    sub_id = submission.id

    for model in (Conflict, MissingInfo, ResolvedField):
        session.execute(delete(model).where(model.submission_id == sub_id))
    session.execute(delete(ResolvedEntity).where(ResolvedEntity.submission_id == sub_id))
    session.flush()

    chosen = {
        r.field_id: r
        for r in session.scalars(
            select(FieldResolution).where(FieldResolution.submission_id == sub_id)
        )
    }
    _exclusion_status(board, sub_id, chosen)
    entity_ids: dict[tuple[str, str], str] = {}
    pending_conflicts: list[Conflict] = []
    states: Counter[str] = Counter()
    conflicts = 0
    for (kind, key), e in board.entities.items():
        eid = stable_id(sub_id, kind, key)
        entity_ids[(kind, key)] = eid
        session.add(
            ResolvedEntity(
                id=eid,
                submission_id=sub_id,
                kind=kind,
                key=key,
                label=e.label,
                found_in=sorted(e.found_in),
                details=_jsonable(e.details),
            )
        )
        for attr, f in e.fields.items():
            fid = stable_id(eid, attr)
            value = f.value
            human = _apply_resolution(f, chosen.get(fid))
            by_document = None if human else _settled_by_document(e, attr, f)
            by_reference = None if human or by_document else _settled_by_reference(f)
            states[f.state] += 1
            session.add(
                ResolvedField(
                    id=fid,
                    submission_id=sub_id,
                    entity_id=eid,
                    attribute=attr,
                    label=f.label,
                    kind=f.kind,
                    state=f.state,
                    resolution=f.resolution,
                    value_display=human.display
                    if human
                    else "No"
                    if by_document
                    else by_reference.compared.display
                    if by_reference
                    else (value.compared.display if value else None),
                    value=_jsonable(human.value)
                    if human
                    else False
                    if by_document
                    else _jsonable(by_reference.compared.norm)
                    if by_reference
                    else (_jsonable(value.compared.norm) if value else None),
                    candidates=[c.as_dict() for c in f.candidates],
                    notes=f.notes,
                    invalid=f.invalid,
                )
            )
            if (f.state == "conflict" or human or by_document or by_reference) and f.conflict:
                conflicts += 1
                pending_conflicts.append(
                    Conflict(
                        id=stable_id(fid, "conflict"),
                        submission_id=sub_id,
                        field_id=fid,
                        entity_id=eid,
                        type=str(f.conflict["type"]),
                        title=str(f.conflict["card_title"])
                        if f.conflict.get("card_title")
                        else f"{f.label}: {e.label}"
                        if e.kind not in _SINGLETONS
                        else f"{f.label}",
                        notes=list(cast(list[str], f.conflict.get("notes") or [])),
                        hint=cast(str | None, f.conflict.get("hint")),
                        candidates=[c.as_dict() for c in f.active],
                        resolution={
                            "display": human.display,
                            "reason": human.reason,
                            "actor": human.actor,
                            "at": human.created_at.isoformat(),
                        }
                        if human
                        else by_document,
                    )
                )
    session.flush()  # entities and fields first; conflicts and missing items reference them
    session.add_all(pending_conflicts)
    for m in missing:
        session.add(
            MissingInfo(
                id=stable_id(sub_id, m.code),
                submission_id=sub_id,
                code=m.code,
                title=m.title,
                detail=m.detail,
                category=m.category,
                entity_id=entity_ids.get((m.entity_kind, m.entity_key))
                if m.entity_kind and m.entity_key
                else None,
                document_ids=m.document_ids,
            )
        )
    summary = {
        "entities": dict(Counter(k for k, _ in board.entities)),
        "fields": dict(states),
        "conflicts": conflicts,
        "missing": len(missing),
        "superseded_values": sum(
            1
            for e in board.entities.values()
            for f in e.fields.values()
            for c in f.candidates
            if c.superseded
        ),
    }
    chain.record(
        session,
        actor=ACTOR,
        action="submission.resolved",
        subject_type="submission",
        subject_id=sub_id,
        payload=summary,
    )
    session.commit()
    return summary


def _exclusion_status(board: Board, sub_id: str, chosen: dict[str, FieldResolution]) -> None:
    """One state for a driver excluded by name, used by the rules, the counts and the card:
    pending until a person confirms it (on the driver, or by settling whether the owner drives)."""
    ops_field = stable_id(stable_id(sub_id, "operations", "operations"), "owner_drives")
    owner_said = chosen.get(ops_field)
    for (kind, key), e in board.entities.items():
        excluded = cast(dict[str, Any] | None, e.details.get("excluded"))
        if kind != "driver" or not excluded:
            continue
        own = chosen.get(stable_id(stable_id(sub_id, kind, key), "excluded"))
        owner_no = (
            bool(e.details.get("owner_marker"))
            and owner_said is not None
            and owner_said.display == "No"
        )
        excluded["status"] = "confirmed" if own or owner_no else "pending"


def _settled_by_reference(f: Field) -> Any:
    """A conflict a deterministic reference settles (the VIN decodes to one make): the field
    reads the value the reference backs, marked as settled by it; the conflict stays listed."""
    if f.state != "conflict" or not f.conflict or f.conflict.get("type") != "reference":
        return None
    backed = [c for c in f.active if any(s.doc_type == "vin_decode" for s in c.sources)]
    if len(backed) != 1:
        return None
    f.state = "known"
    f.resolution = "reference"
    f.notes.append("Settled by VIN decode")
    return backed[0]


def _settled_by_document(e: Entity, attr: str, f: Field) -> dict[str, Any] | None:
    """Whether the owner drives, settled by a named driver exclusion of the owner: shown as
    settled by the letter, pending an underwriter's confirmation (a resolution confirms it)."""
    letter = cast(dict[str, Any] | None, e.details.get("owner_excluded"))
    if e.kind != "operations" or attr != "owner_drives" or f.state != "conflict" or not letter:
        return None
    f.state = "known"
    f.resolution = "document"
    f.notes.append(f"Settled by exclusion letter ({letter['title']}), pending confirmation")
    return {
        "display": "No",
        "reason": f"Settled by exclusion letter, pending confirmation: {letter['name']} is excluded by name",
        "actor": f"document:{letter['title']}",
        "at": "",  # derived on every run: no time, so the same inputs give the same output
        "pending": True,
    }


def _apply_resolution(f: Field, r: FieldResolution | None) -> FieldResolution | None:
    """Apply a person's decision. A correction (override) stands whatever the documents say,
    until the person clears it. A choice settles a conflict, if the chosen value is still one of
    the candidates; if the documents changed and it is not, the conflict stays open with a note."""
    if r is not None and r.kind == "override":
        f.state = "known"
        f.resolution = "override"
        f.notes.append(f"Corrected by {r.actor.removeprefix('user:')}: {r.reason}")
        return r
    if r is None or f.state != "conflict":
        return None
    if not any(c.compared.display == r.display for c in f.active):
        f.notes.append(
            f"An earlier resolution ({r.display}) no longer matches the documents; it needs a new look"
        )
        return None
    f.state = "known"
    f.resolution = "human"
    f.notes.append(f"Resolved by {r.actor.removeprefix('user:')}: {r.reason}")
    return r


_SINGLETONS = {"applicant", "agency", "coverage_request", "operations", "fleet", "coverage_history"}


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in cast(dict[Any, Any], value).items()}
    if isinstance(value, (list, tuple, set)):
        items: list[Any] = list(cast(list[Any], value))
        return [_jsonable(v) for v in items]
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
