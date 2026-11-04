"""Pipeline step: extract typed, grounded facts from every logical document."""

from sqlalchemy import func, select

from lane.db.models import Document, DocumentExtraction, Fact, StepStatus
from lane.documents.titles import refine_submission_titles
from lane.extract.service import extract_submission
from lane.pipeline.runner import StepContext, StepOutcome


def extract_step(ctx: StepContext) -> StepOutcome:
    summary = extract_submission(
        ctx.session,
        ctx.store,
        ctx.settings,
        ctx.submission.id,
        gateway_factory=ctx.llm_factory,
        run_id=ctx.run.id,
    )
    refine_submission_titles(ctx.session, ctx.submission.id)
    ctx.session.commit()
    counts = dict(
        ctx.session.execute(
            select(Fact.document_id, func.count())
            .where(Fact.submission_id == ctx.submission.id)
            .group_by(Fact.document_id)
        ).all()
    )
    for x, title in ctx.session.execute(
        select(DocumentExtraction, Document.title)
        .join(Document, Document.id == DocumentExtraction.document_id)
        .where(DocumentExtraction.submission_id == ctx.submission.id)
        .order_by(Document.ordinal)
    ):
        ctx.emit(
            "document.extracted",
            document_id=x.document_id,
            title=title,
            status=x.status.value,
            facts=counts.get(x.document_id, 0),
            checks_failed=sum(1 for c in x.checks if c.get("passed") is False),
        )
    incomplete = (
        summary.unavailable
        or summary.statuses.get("failed")
        or summary.statuses.get("needs_escalation")
        or summary.statuses.get("needs_review")
    )
    return StepOutcome(
        status=StepStatus.DEGRADED if incomplete else StepStatus.SUCCEEDED,
        summary=summary.as_dict(),
    )
