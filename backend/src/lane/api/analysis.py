"""The analyst's summary and findings, and the underwriter's review of each finding."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from lane.actions.service import build_actions
from lane.analyze.engine import DEGRADED_MESSAGE
from lane.analyze.service import approve_analysis, review_finding, review_is_stale
from lane.api.auth import Reviewer, SpendApprover
from lane.api.deps import SessionDep, SettingsDep
from lane.api.display import model_label
from lane.api.models import (
    AnalystNoteOut,
    FootnoteOut,
    RefOut,
    RejectedOut,
    SentenceOut,
    footnotes,
    sentences,
)
from lane.config import get_settings
from lane.db.models import AnalysisRecord, FindingRecord, Submission
from lane.llm.gateway import LlmGateway
from lane.pipeline import background as work

router = APIRouter(prefix="/api/v1", tags=["analysis"])


class FindingOut(BaseModel):
    id: str
    ordinal: int
    title: str
    body: str
    kind: str
    severity: str
    confidence: str
    basis: str
    citations: list[str]
    review_state: str
    review_reason: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None


class AnalysisOut(BaseModel):
    status: str  # ok | degraded | not_run | awaiting_approval | running
    message: str | None
    model: str | None
    prompt_version: str | None
    summary: list[SentenceOut]
    footnotes: list[FootnoteOut]
    refs: dict[str, RefOut]
    findings: list[FindingOut]
    rejected: list[RejectedOut]
    security_notes: list[AnalystNoteOut]
    attempts: int
    cost_usd: float
    estimate_usd: float | None = None
    approved_by: str | None = None
    stale: bool = False  # written before the agent's latest reply


class ReviewIn(BaseModel):
    state: Literal["accepted", "dismissed", "open"]
    reason: str | None = None


def analysis_message(a: AnalysisRecord) -> str | None:
    """What the underwriter is told about the analysis, in one place for every screen."""
    if a.status == "awaiting_approval":
        return (
            f"Expert review is ready to run on "
            f"{model_label(a.model or get_settings().llm_analyze_model)}, "
            f"for about ${a.estimate_usd or 0:.2f}. "
            "It runs when an underwriter approves the cost."
        )
    if a.status == "running":
        return "Expert review is running."
    if a.status != "ok":
        return DEGRADED_MESSAGE + (" Showing the last completed analysis." if a.summary else "")
    return None


def _finding(f: FindingRecord) -> FindingOut:
    return FindingOut(
        id=f.id,
        ordinal=f.ordinal,
        title=f.title,
        body=f.body,
        kind=f.kind,
        severity=f.severity,
        confidence=f.confidence,
        basis=f.basis,
        citations=f.citations,
        review_state=f.review_state,
        review_reason=f.review_reason,
        reviewed_by=f.reviewed_by,
        reviewed_at=f.reviewed_at,
    )


@router.get("/submissions/{submission_id}/analysis")
def get_analysis(submission_id: str, session: SessionDep) -> AnalysisOut:
    if session.get(Submission, submission_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    a = session.get(AnalysisRecord, submission_id)
    findings = [
        _finding(f)
        for f in session.scalars(
            select(FindingRecord)
            .where(FindingRecord.submission_id == submission_id)
            .order_by(FindingRecord.ordinal)
        )
    ]
    if a is None:
        return AnalysisOut(
            status="not_run",
            message=None,
            model=None,
            prompt_version=None,
            summary=[],
            footnotes=[],
            refs={},
            findings=[],
            rejected=[],
            security_notes=[],
            attempts=0,
            cost_usd=0.0,
        )
    message = analysis_message(a)
    return AnalysisOut(
        stale=review_is_stale(session, submission_id),
        status=a.status,
        message=message,
        model=a.model,
        prompt_version=a.prompt_version,
        summary=sentences(a.summary),
        footnotes=footnotes(a.footnotes),
        refs={k: RefOut.model_validate(v) for k, v in a.refs.items()},
        findings=findings,
        rejected=[RejectedOut.model_validate(r) for r in a.rejected],
        security_notes=[AnalystNoteOut.model_validate(n) for n in a.security_notes],
        attempts=a.attempts,
        cost_usd=a.cost_usd,
        estimate_usd=a.estimate_usd,
        approved_by=a.approved_by,
    )


@router.post("/findings/{finding_id}/review", operation_id="review_finding")
def post_review(
    finding_id: str,
    body: ReviewIn,
    session: SessionDep,
    settings: SettingsDep,
    viewer: Reviewer,
) -> FindingOut:
    f = session.get(FindingRecord, finding_id)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Finding not found")
    try:
        review_finding(session, f, state=body.state, reason=body.reason, actor=viewer.actor)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if f.kind == "recommendation":
        # Accepted recommendations join the agent request; the draft is refreshed on request.
        sub = session.get(Submission, f.submission_id)
        assert sub is not None
        build_actions(session, sub, settings, LlmGateway, run_id=None, redraft=False)
    return _finding(f)


@router.get("/submissions/{submission_id}/findings")
def list_findings(submission_id: str, session: SessionDep) -> list[FindingOut]:
    """The analyst's findings alone (also part of GET /analysis)."""
    if session.get(Submission, submission_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    return [
        _finding(f)
        for f in session.scalars(
            select(FindingRecord)
            .where(FindingRecord.submission_id == submission_id)
            .order_by(FindingRecord.ordinal)
        )
    ]


class ApprovalOut(BaseModel):
    status: str
    estimate_usd: float | None


@router.post("/submissions/{submission_id}/analysis/approve", status_code=202)
def approve_analysis_run(
    submission_id: str,
    session: SessionDep,
    background: BackgroundTasks,
    viewer: SpendApprover,
) -> ApprovalOut:
    """An underwriter approves the analyst's estimated cost; it runs in the background."""
    a = session.get(AnalysisRecord, submission_id)
    if a is None or a.status != "awaiting_approval":
        raise HTTPException(status.HTTP_409_CONFLICT, "Expert review is not waiting for approval")
    approve_analysis(session, a, actor=viewer.actor)
    work.start(background, session, "analysis", submission_id, trigger="approval")
    return ApprovalOut(status="running", estimate_usd=a.estimate_usd)
