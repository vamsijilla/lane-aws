"""Pipeline step: the agent request and its draft email. Never sends anything."""

from lane.actions import points
from lane.actions.service import build_actions
from lane.db.models import StepStatus
from lane.pipeline.runner import StepContext, StepOutcome


def actions_step(ctx: StepContext) -> StepOutcome:
    sid = ctx.submission.id
    # What the agent was asked (the request marked sent, else the last draft), before rebuilding
    base = points.baseline(ctx.session, sid)
    result = build_actions(
        ctx.session, ctx.submission, ctx.settings, ctx.llm_factory, run_id=ctx.run.id
    )
    groups: dict[str, int] = {}
    for item in result.items:
        groups[item.group] = groups.get(item.group, 0) + 1
    diff = points.diff(base, points.item_rows(ctx.session, sid)) if base[4] else None
    ctx.emit("actions.ready", items=groups, draft_method=result.draft.method)
    return StepOutcome(
        status=StepStatus.SUCCEEDED,
        summary={
            "items": groups,
            "draft_version": result.draft.version,
            "draft_method": result.draft.method,
            "redrafted": result.redrafted,
            "cost_usd": result.cost_usd,
            "problems": result.problems,
            # Point by point, against what the agent was asked (lane/actions/points.py)
            "points": diff.as_dict() if diff else None,
        },
    )
