"""Pipeline step: apply the underwriting guideline and recommend the next step."""

from sqlalchemy import select

from lane.audit import chain
from lane.db.models import RuleConfirmation, RuleOutcomeRecord, StepStatus
from lane.pipeline.runner import StepContext, StepOutcome
from lane.rules.service import decide_submission


def rules_step(ctx: StepContext) -> StepOutcome:
    before = {
        r.rule_id: (r.outcome, r.potential_decline)
        for r in ctx.session.scalars(
            select(RuleOutcomeRecord).where(RuleOutcomeRecord.submission_id == ctx.submission.id)
        )
    }
    decision = decide_submission(ctx.session, ctx.submission, run_id=ctx.run.id)
    changes: list[dict[str, object]] = []
    for r in ctx.session.scalars(
        select(RuleOutcomeRecord)
        .where(RuleOutcomeRecord.submission_id == ctx.submission.id)
        .order_by(RuleOutcomeRecord.ordinal)
    ):
        was = before.get(r.rule_id)
        if before and was != (r.outcome, r.potential_decline):
            changes.append(
                {
                    "rule_id": r.rule_id,
                    "title": r.title,
                    "before": was[0] if was else None,
                    "after": r.outcome,
                    "was_potential_decline": bool(was and was[1]),
                    "potential_decline": r.potential_decline,
                }
            )
        ctx.emit(
            "rule.evaluated",
            rule_id=r.rule_id,
            title=r.title,
            section=r.section,
            outcome=r.outcome,
            potential_decline=r.potential_decline,
        )
    pending = _confirmations(ctx, changes)
    ctx.emit(
        "decision.made",
        outcome=decision.outcome,
        headline=decision.headline,
        team=decision.team,
        potential_decline=decision.potential_decline,
        route_stable=decision.route_stable,
    )
    return StepOutcome(
        status=StepStatus.SUCCEEDED,
        summary={
            "decision": decision.outcome,
            "headline": decision.headline,
            "team": decision.team,
            "potential_decline": decision.potential_decline,
            "route": decision.route_reason,
            "counts": decision.counts,
            "anchor": f"{decision.anchor} {decision.anchor_date}",
            "changes": changes,  # rule outcomes that differ from the run before
            "pending_confirmation": pending,
        },
    )


def _severe(outcome: str | None, potential: bool) -> str | None:
    if outcome == "decline":
        return "decline"
    return "potential decline" if potential else None


def _confirmations(ctx: StepContext, changes: list[dict[str, object]]) -> list[str]:
    """A rule that moves away from decline or potential decline on a document the agent sent
    waits for an underwriter to confirm it. One that goes back is no longer pending."""
    session, sid = ctx.session, ctx.submission.id
    now = {
        r.rule_id: _severe(r.outcome, r.potential_decline)
        for r in session.scalars(
            select(RuleOutcomeRecord).where(RuleOutcomeRecord.submission_id == sid)
        )
    }
    for c in session.scalars(
        select(RuleConfirmation).where(
            RuleConfirmation.submission_id == sid, RuleConfirmation.status == "pending"
        )
    ):
        if now.get(c.rule_id):
            c.status = "void"  # severe again: nothing left to confirm
    if ctx.run.trigger == "follow_up":
        for ch in changes:
            was = _severe(
                str(ch["before"]) if ch["before"] else None, bool(ch["was_potential_decline"])
            )
            if was and not _severe(str(ch["after"]), bool(ch["potential_decline"])):
                c = RuleConfirmation(
                    submission_id=sid,
                    run_id=ctx.run.id,
                    rule_id=str(ch["rule_id"]),
                    title=str(ch["title"]),
                    before=was,
                    after=str(ch["after"]),
                )
                session.add(c)
                session.flush()
                chain.record(
                    session,
                    actor="system:rules",
                    action="rule_change.pending",
                    subject_type="submission",
                    subject_id=sid,
                    payload={
                        "confirmation_id": c.id,
                        "rule_id": c.rule_id,
                        "from": was,
                        "to": c.after,
                    },
                )
    session.flush()
    return [
        c.rule_id
        for c in session.scalars(
            select(RuleConfirmation).where(
                RuleConfirmation.submission_id == sid, RuleConfirmation.status == "pending"
            )
        )
    ]
