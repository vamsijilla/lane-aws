"""Run the analyst for a submission and persist the summary, findings and security notes."""

from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.analyze.context import Brief, build_brief
from lane.analyze.engine import Analysis, analyze, estimate_usd, first_request
from lane.audit import chain
from lane.config import Settings
from lane.db.base import utcnow
from lane.db.models import AnalysisRecord, FindingRecord, Run, Submission
from lane.llm.gateway import GatewayFactory, LlmUnavailable
from lane.resolve.service import stable_id

ACTOR = "system:analyst"


def analyze_submission(
    session: Session,
    submission: Submission,
    settings: Settings,
    gateway_factory: GatewayFactory,
    *,
    run_id: str | None,
) -> Analysis:
    brief = build_brief(session, submission.id, settings)
    record = session.get(AnalysisRecord, submission.id) or AnalysisRecord(
        submission_id=submission.id
    )
    estimate = estimate_usd(brief, settings)
    if needs_approval(session, submission.id, brief, settings, gateway_factory, record, estimate):
        record.status = "awaiting_approval"
        record.model = settings.llm_analyze_model  # what the approval would pay for
        record.estimate_usd = estimate
        record.run_id = run_id
        session.add(record)
        session.commit()
        return Analysis(
            status="awaiting_approval",
            model=settings.llm_analyze_model,
            error=f"Waiting for approval to spend about ${estimate:.2f}",
        )
    paid_with_approval = record.approved_at is not None
    result = analyze(session, submission.id, brief, settings, gateway_factory, run_id=run_id)
    if paid_with_approval and result.attempts:
        record.approved_at = None  # an approval covers one run; a changed brief asks again
    record.estimate_usd = estimate
    record.run_id = run_id
    record.status = result.status
    record.model = result.model
    record.prompt_version = result.prompt.version if result.prompt else None
    record.prompt_sha256 = result.prompt.sha256 if result.prompt else None
    record.error = result.error
    record.attempts = result.attempts
    record.cost_usd = result.cost_usd
    session.add(record)

    if result.status != "ok":
        # Keep the last good analysis visible when this run could not reach the model; the
        # status and error say it is not from this run.
        session.commit()
        return result

    record.summary = result.summary
    record.analyzed_at = utcnow()
    record.footnotes = result.footnotes
    record.rejected = result.rejected
    record.security_notes = result.security_notes
    cited = {c for f in result.findings for c in f.citations} | {n["ref"] for n in result.footnotes}
    # And what those cite in turn (a figure's inputs, the fields a rule read), so each opens
    cited |= {c for ref in cited for c in brief.refs[ref].cites if c in brief.refs}
    record.refs = {
        ref: {k: v for k, v in asdict(brief.refs[ref]).items() if k != "id"}
        for ref in sorted(cited)
    }

    existing = {
        f.id: f
        for f in session.scalars(
            select(FindingRecord).where(FindingRecord.submission_id == submission.id)
        )
    }
    keep: set[str] = set()
    for i, f in enumerate(result.findings, start=1):
        fid = stable_id(submission.id, f.title.lower())
        keep.add(fid)
        row = existing.get(fid) or FindingRecord(id=fid, submission_id=submission.id)
        row.run_id = run_id
        row.ordinal = i
        row.title = f.title
        row.body = f.body
        row.kind = f.kind
        row.severity = f.severity
        row.confidence = f.confidence
        row.basis = f.basis
        row.citations = f.citations
        row.review_state = row.review_state or "open"  # reviews survive reruns
        session.add(row)
    for fid, row in existing.items():
        if fid not in keep:
            session.delete(row)

    for note in result.security_notes:
        chain.record(
            session,
            actor=ACTOR,
            action="security.injection_detected",
            subject_type="submission",
            subject_id=submission.id,
            payload=note,
        )
    chain.record(
        session,
        actor=ACTOR,
        action="submission.analyzed",
        subject_type="submission",
        subject_id=submission.id,
        payload={
            "model": result.model,
            "prompt": f"{result.prompt.name} v{result.prompt.version}" if result.prompt else None,
            "findings": len(result.findings),
            "rejected": len(result.rejected),
            "attempts": result.attempts,
            "cost_usd": result.cost_usd,
        },
    )
    session.commit()
    return result


def needs_approval(
    session: Session,
    submission_id: str,
    brief: Brief,
    settings: Settings,
    gateway_factory: GatewayFactory,
    record: AnalysisRecord,
    estimate: float,
) -> bool:
    """Paid model runs need a person's approval, unless cached or within the auto budget."""
    if record.approved_at is not None or estimate <= settings.analyze_auto_budget_usd:
        return False
    try:
        gateway = gateway_factory(settings)
    except LlmUnavailable:
        return False  # no model to pay for; the analyst reports itself unavailable
    return not gateway.cached(session, first_request(submission_id, brief, settings))


def approve_analysis(session: Session, record: AnalysisRecord, *, actor: str) -> None:
    record.approved_by = actor
    record.approved_at = datetime.now(UTC)
    record.status = "running"
    chain.record(
        session,
        actor=actor,
        action="analysis.approved",
        subject_type="submission",
        subject_id=record.submission_id,
        payload={"estimate_usd": record.estimate_usd},
    )
    session.commit()


def review_finding(
    session: Session, finding: FindingRecord, *, state: str, reason: str | None, actor: str
) -> FindingRecord:
    if state not in ("accepted", "dismissed", "open"):
        raise ValueError(f"Unknown review state {state!r}")
    if state == "dismissed" and not (reason and reason.strip()):
        raise ValueError("Dismissing a finding needs a reason")
    finding.review_state = state
    finding.review_reason = reason.strip() if reason else None
    finding.reviewed_by = actor
    finding.reviewed_at = datetime.now(UTC)
    chain.record(
        session,
        actor=actor,
        action=f"finding.{state}",
        subject_type="finding",
        subject_id=finding.id,
        payload={"title": finding.title, "reason": finding.review_reason},
    )
    session.commit()
    return finding


def review_is_stale(session: Session, submission_id: str) -> bool:
    """The expert review predates the agent's latest reply: its asks may be answered, so it is
    shown as an earlier review and its recommendations are not asked again."""
    reply = session.scalar(
        select(Run.started_at)
        .where(Run.submission_id == submission_id, Run.trigger == "follow_up")
        .order_by(Run.started_at.desc())
        .limit(1)
    )
    if reply is None:
        return False
    a = session.get(AnalysisRecord, submission_id)
    return a is None or a.analyzed_at is None or _aware(a.analyzed_at) < _aware(reply)


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=UTC)  # SQLite drops the zone; values are UTC
