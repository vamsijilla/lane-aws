"""Pipeline step: resolve entities across documents, find conflicts and missing information."""

from sqlalchemy import select

from lane.db.models import Conflict, MissingInfo, StepStatus
from lane.pipeline.runner import StepContext, StepOutcome
from lane.resolve.service import resolve_submission


def resolve_step(ctx: StepContext) -> StepOutcome:
    summary = resolve_submission(ctx.session, ctx.submission)
    sid = ctx.submission.id
    for c in ctx.session.scalars(select(Conflict).where(Conflict.submission_id == sid)):
        ctx.emit("conflict.found", conflict_id=c.id, title=c.title, conflict_type=c.type)
    for m in ctx.session.scalars(select(MissingInfo).where(MissingInfo.submission_id == sid)):
        ctx.emit("missing.found", missing_id=m.id, title=m.title, category=m.category)
    return StepOutcome(status=StepStatus.SUCCEEDED, summary=summary)
