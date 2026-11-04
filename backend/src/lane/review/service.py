"""A person settles a conflict; the submission is re-resolved and the rules run again.

The choice and its reason are stored per field (field ids are stable), applied during
resolution, and recorded in the audit chain. The rerun is synchronous and cheap: resolution and
rules are code only. The agent email is not redrafted automatically; it is marked out of date.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.actions.service import build_actions
from lane.audit import chain
from lane.config import Settings
from lane.db.models import (
    Conflict,
    DecisionRecord,
    FieldResolution,
    ResolvedField,
    RuleOutcomeRecord,
    Submission,
)
from lane.llm.gateway import GatewayFactory
from lane.pipeline.registry import PIPELINE
from lane.pipeline.runner import process_submission
from lane.resolve.compare import EXPECTED, compare
from lane.storage.blobs import BlobStore

RERUN = ("resolve", "rules")


@dataclass
class Change:
    rule_id: str
    title: str
    before: str
    after: str
    potential_before: bool
    potential_after: bool


@dataclass
class Rerun:
    run_id: str
    headline_before: str | None
    headline_after: str | None
    potential_before: bool
    potential_after: bool
    changes: list[Change] = field(default_factory=lambda: list[Change]())


def _snapshot(
    session: Session, sid: str
) -> tuple[dict[str, tuple[str, str, bool]], DecisionRecord | None]:
    rules = {
        r.rule_id: (r.title, r.outcome, r.potential_decline)
        for r in session.scalars(
            select(RuleOutcomeRecord).where(RuleOutcomeRecord.submission_id == sid)
        )
    }
    d = session.get(DecisionRecord, sid)
    return rules, d


def rerun(
    session: Session,
    store: BlobStore,
    settings: Settings,
    submission: Submission,
    gateway_factory: GatewayFactory,
    *,
    trigger: str,
) -> Rerun:
    sid = submission.id
    before, d0 = _snapshot(session, sid)
    headline0, pd0 = (d0.headline, d0.potential_decline) if d0 else (None, False)
    run = process_submission(
        session,
        store,
        settings,
        sid,
        steps=[s for s in PIPELINE if s.name in RERUN],
        trigger=trigger,
        llm_factory=gateway_factory,
    )
    build_actions(session, submission, settings, gateway_factory, run_id=run.id, redraft=False)
    session.expire_all()
    after, d1 = _snapshot(session, sid)
    changes = [
        Change(rid, after[rid][0], before[rid][1], after[rid][1], before[rid][2], after[rid][2])
        for rid in after
        if rid in before and (before[rid][1], before[rid][2]) != (after[rid][1], after[rid][2])
    ]
    return Rerun(
        run.id,
        headline0,
        d1.headline if d1 else None,
        pd0,
        d1.potential_decline if d1 else False,
        changes,
    )


def resolve_conflict(
    session: Session,
    conflict: Conflict,
    *,
    candidate: int,
    reason: str,
    actor: str,
) -> dict[str, Any]:
    if not reason.strip():
        raise ValueError("Resolving a conflict needs a reason")
    if not 0 <= candidate < len(conflict.candidates):
        raise ValueError("No such value for this conflict")
    chosen = conflict.candidates[candidate]
    row = session.get(FieldResolution, conflict.field_id) or FieldResolution(
        field_id=conflict.field_id, submission_id=conflict.submission_id
    )
    row.kind = "choice"
    row.before = None
    row.display = str(chosen.get("display"))
    row.value = chosen.get("value")
    row.reason = reason.strip()
    row.actor = actor
    row.created_at = datetime.now(UTC)
    session.add(row)
    resolved = session.get(ResolvedField, conflict.field_id)
    chain.record(
        session,
        actor=actor,
        action="conflict.resolved",
        subject_type="field",
        subject_id=conflict.field_id,
        payload={
            "conflict": conflict.title,
            "attribute": resolved.attribute if resolved else None,  # lets the audit view mask PII
            "chosen": row.display,
            "reason": row.reason,
        },
    )
    session.commit()
    return {"display": row.display}


def reopen_conflict(session: Session, conflict: Conflict, *, actor: str) -> None:
    row = session.get(FieldResolution, conflict.field_id)
    if row is None:
        return
    session.delete(row)
    chain.record(
        session,
        actor=actor,
        action="conflict.reopened",
        subject_type="field",
        subject_id=conflict.field_id,
        payload={"conflict": conflict.title},
    )
    session.commit()


class OverrideError(ValueError):
    """The typed value cannot be read as the field's kind."""


def override_field(
    session: Session, field: ResolvedField, *, value: str, reason: str, actor: str
) -> dict[str, Any]:
    """A person corrects a value the documents got wrong (or never gave). The value is read with
    the field's own kind, so a corrected date is a date and a corrected count is a number."""
    if not reason.strip():
        raise OverrideError("Correcting a value needs a reason")
    parsed = compare(field.kind, value.strip()) if value.strip() else None
    if parsed is None:
        raise OverrideError(f"Enter {EXPECTED.get(field.kind, 'a value')}")
    row = session.get(FieldResolution, field.id) or FieldResolution(
        field_id=field.id, submission_id=field.submission_id
    )
    before = field.value_display if field.state == "known" else field.state
    row.kind = "override"
    row.before = before
    row.display = parsed.display
    # Stored the way the documents' values are (the comparable norm), so rules read it the same
    row.value = parsed.norm.isoformat() if isinstance(parsed.norm, date) else parsed.norm
    row.reason = reason.strip()
    row.actor = actor
    row.created_at = datetime.now(UTC)
    session.add(row)
    chain.record(
        session,
        actor=actor,
        action="field.overridden",
        subject_type="field",
        subject_id=field.id,
        payload={
            "field": field.label,
            "attribute": field.attribute,
            "before": before,
            "after": parsed.display,
            "reason": row.reason,
        },
    )
    session.commit()
    return {"display": parsed.display}


def clear_override(session: Session, field: ResolvedField, *, actor: str) -> bool:
    row = session.get(FieldResolution, field.id)
    if row is None or row.kind != "override":
        return False
    session.delete(row)
    chain.record(
        session,
        actor=actor,
        action="field.override_cleared",
        subject_type="field",
        subject_id=field.id,
        payload={"field": field.label, "attribute": field.attribute, "was": row.display},
    )
    session.commit()
    return True
