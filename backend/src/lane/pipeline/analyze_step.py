"""Pipeline step: expert findings and a cited summary. Optional: without a model the run is
degraded, and rules, facts and actions still complete."""

from lane.analyze.service import analyze_submission
from lane.db.models import StepStatus
from lane.pipeline.runner import StepContext, StepOutcome


def analyze_step(ctx: StepContext) -> StepOutcome:
    result = analyze_submission(
        ctx.session, ctx.submission, ctx.settings, ctx.llm_factory, run_id=ctx.run.id
    )
    ctx.emit("analysis.done", status=result.status, findings=len(result.findings))
    return StepOutcome(
        status=StepStatus.SUCCEEDED
        if result.status == "ok"
        else StepStatus.SKIPPED
        if result.status == "awaiting_approval"
        else StepStatus.DEGRADED,
        summary={
            "status": result.status,
            "findings": len(result.findings),
            "rejected": len(result.rejected),
            "security_notes": len(result.security_notes),
            "attempts": result.attempts,
            "cost_usd": result.cost_usd,
            "error": result.error,
        },
    )
