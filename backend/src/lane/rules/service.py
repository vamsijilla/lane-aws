"""Run the ruleset for a submission and persist rule results and the decision."""

from dataclasses import asdict
from functools import cache
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from lane.audit import chain
from lane.db.models import Conflict, DecisionRecord, RuleOutcomeRecord, Submission
from lane.resolve.service import stable_id
from lane.rules.engine import Decision, evaluate
from lane.rules.model import Ruleset
from lane.rules.view import SubmissionView

ACTOR = "system:rules"


@cache
def ruleset() -> Ruleset:
    return Ruleset.load()


def decide_submission(session: Session, submission: Submission, *, run_id: str | None) -> Decision:
    rules = ruleset()
    view = SubmissionView.load(session, submission.id)
    rows = list(session.scalars(select(Conflict).where(Conflict.submission_id == submission.id)))
    conflicts = {
        c.field_id: c.title
        for c in rows
        # a person settled it, or code did (a VIN's own decode): rules read the chosen value
        if not c.resolution and c.type != "reference"
    }
    settled = [c.title for c in rows if not c.resolution and c.type == "reference"]
    results, decision = evaluate(
        view,
        rules,
        received=submission.email.received_at.date(),
        conflicts=conflicts,
        settled_by_code=settled,
    )

    session.execute(
        delete(RuleOutcomeRecord).where(RuleOutcomeRecord.submission_id == submission.id)
    )
    for i, r in enumerate(results, start=1):
        session.add(
            RuleOutcomeRecord(
                id=stable_id(submission.id, r.spec.id),
                submission_id=submission.id,
                run_id=run_id,
                ordinal=i,
                rule_id=r.spec.id,
                title=r.spec.title,
                section=r.spec.section,
                quote=r.spec.quote,
                decline_capable=r.spec.decline,
                outcome=r.outcome.value,
                reason=r.verdict.reason,
                items=r.verdict.items,
                request=r.spec.request,
                inputs=r.inputs,
                potential_decline=r.potential_decline,
                blocking_conflicts=r.blocking_conflicts,
                sensitivity=r.sensitivity,
                anchor_note=r.anchor_note,
                readings=r.readings,
                waiting_on=r.waiting_on,
                evidence=r.evidence,
                absent=r.absent,
                anchor_items=r.anchor_items,
                assumptions=sorted(
                    {
                        *r.spec.assumptions,
                        *(rd.assumption for rd in r.spec.readings if rd.assumption),
                    }
                ),
                ruleset_version=rules.version,
            )
        )
    blocking: dict[str, list[str]] = {}
    for r in results:
        for fid in r.blocking_conflicts:
            blocking.setdefault(fid, []).append(r.spec.id)
    for c in session.scalars(select(Conflict).where(Conflict.submission_id == submission.id)):
        c.blocking_rules = blocking.get(c.field_id, [])

    record = session.get(DecisionRecord, submission.id) or DecisionRecord(
        submission_id=submission.id
    )
    for key, value in asdict(decision).items():
        setattr(record, key, value)
    record.run_id = run_id
    session.add(record)
    payload: dict[str, Any] = {
        "outcome": decision.outcome,
        "headline": decision.headline,
        "team": decision.team,
        "potential_decline": decision.potential_decline,
        "ruleset": f"{rules.ruleset} {rules.version}",
        "ruleset_sha256": rules.sha256,
        "anchor": f"{decision.anchor} {decision.anchor_date}",
        "counts": decision.counts,
    }
    chain.record(
        session,
        actor=ACTOR,
        action="submission.decided",
        subject_type="submission",
        subject_id=submission.id,
        payload=payload,
    )
    session.commit()
    return decision


OUTCOME_BUCKETS = ("potential decline", "decline", "refer", "unknown", "flag", "pass", "n/a")


def outcome_counts(records: list[RuleOutcomeRecord]) -> dict[str, int]:
    """Every rule's outcome, a potential decline counted on its own: the one count every screen
    shows (Brief, Rules, live intake). Sums to the number of rules."""
    counts: dict[str, int] = dict.fromkeys(OUTCOME_BUCKETS, 0)
    for r in records:
        key = "potential decline" if r.potential_decline else r.outcome
        counts[key] = counts.get(key, 0) + 1
    return {k: v for k, v in counts.items() if v}
