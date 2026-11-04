"""Operational health: runs, step timings, model calls, cost and failures."""

from collections import Counter
from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from lane.api.auth import OpsViewer
from lane.api.deps import SessionDep, SettingsDep
from lane.config import Settings
from lane.db.base import utcnow
from lane.db.models import (
    EmailMessage,
    Job,
    JobStatus,
    LlmCall,
    Run,
    RunStatus,
    Step,
    StepStatus,
    Submission,
    WorkerHeartbeat,
)
from lane.intake.mailpit import MailpitClient
from lane.storage.blobs import S3BlobStore, blob_store

router = APIRouter(prefix="/api/v1/ops", tags=["ops"])


class StepTiming(BaseModel):
    name: str
    runs: int
    p50_ms: int
    p95_ms: int
    failed: int


class ModelUsage(BaseModel):
    calls: int
    cache_hits: int
    errors: int
    cost_usd: float


class FailedRun(BaseModel):
    submission_id: str
    subject: str
    run_id: str
    finished_at: datetime | None
    step: str | None
    error: str | None


class OpsHealth(BaseModel):
    submissions: int
    runs_by_status: dict[str, int]
    steps: list[StepTiming]
    models: ModelUsage
    cost_per_submission_usd: float
    failed_runs: list[FailedRun]


def _pct(values: list[int], q: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))] if ordered else 0


@router.get("/health", operation_id="get_ops_health")
def ops_health(session: SessionDep, _viewer: OpsViewer) -> OpsHealth:
    submissions = session.scalar(select(func.count()).select_from(Submission)) or 0
    runs = Counter(r.value for r in session.scalars(select(Run.status)))
    durations: dict[str, list[int]] = {}
    failed: Counter[str] = Counter()
    for name, ms, st in session.execute(select(Step.name, Step.duration_ms, Step.status)):
        if ms is not None:
            durations.setdefault(name, []).append(ms)
        if st is StepStatus.FAILED:
            failed[name] += 1
    calls, hits, cost = session.execute(
        select(
            func.count(),
            func.sum(case((LlmCall.cache_hit, 1), else_=0)),
            func.sum(LlmCall.cost_usd),
        )
    ).one()
    errors = (
        session.scalar(select(func.count()).select_from(LlmCall).where(LlmCall.status != "ok")) or 0
    )
    failures: list[FailedRun] = []
    for run, subject in session.execute(
        select(Run, EmailMessage.subject)
        .join(Submission, Submission.id == Run.submission_id)
        .join(EmailMessage, EmailMessage.id == Submission.email_id)
        .where(Run.status == RunStatus.FAILED)
        .order_by(Run.started_at.desc())
        .limit(20)
    ):
        bad = next((s for s in run.steps if s.status is StepStatus.FAILED), None)
        failures.append(
            FailedRun(
                submission_id=run.submission_id,
                subject=subject,
                run_id=run.id,
                finished_at=run.finished_at,
                step=bad.name if bad else None,
                error=bad.error if bad else None,
            )
        )
    total_cost = float(cost or 0)
    return OpsHealth(
        submissions=submissions,
        runs_by_status=dict(runs),
        steps=[
            StepTiming(
                name=name,
                runs=len(ms),
                p50_ms=_pct(ms, 0.5),
                p95_ms=_pct(ms, 0.95),
                failed=failed[name],
            )
            for name, ms in durations.items()
        ],
        models=ModelUsage(
            calls=calls or 0,
            cache_hits=int(hits or 0),
            errors=errors,
            cost_usd=round(total_cost, 4),
        ),
        cost_per_submission_usd=round(total_cost / submissions, 4) if submissions else 0.0,
        failed_runs=failures,
    )


class Service(BaseModel):
    name: str
    ok: bool
    detail: str


class WorkerOut(BaseModel):
    owner: str
    state: str  # working | idle | quiet (no heartbeat for a while: stopped or cut off)
    seen_seconds_ago: int
    started_at: datetime
    job_id: str | None
    jobs_done: int


class JobOut(BaseModel):
    id: str
    submission_id: str
    subject: str
    kind: str
    trigger: str
    status: str
    attempts: int
    lease_owner: str | None
    lease_seconds_left: int | None
    taken_over: bool  # a worker stopped holding it and another claimed it
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class OpsQueue(BaseModel):
    run_mode: str
    services: list[Service]
    workers: list[WorkerOut]
    jobs_by_status: dict[str, int]
    oldest_queued_seconds: int | None
    jobs: list[JobOut]
    today_cost_usd: float
    today_calls: int
    today_cache_rate: float


QUIET_AFTER_S = 15
FORGET_AFTER_S = 600


def _services(session: Session, settings: Settings) -> list[Service]:
    url = settings.resolved_database_url
    out = [
        Service(
            name="Database",
            ok=True,
            detail="Postgres" if url.startswith("postgresql") else "SQLite (local file)",
        )
    ]
    store = blob_store(settings)
    try:
        if isinstance(store, S3BlobStore):
            store.client.head_bucket(Bucket=store.bucket)
            out.append(Service(name="File storage", ok=True, detail=f"S3 bucket {store.bucket}"))
        else:
            out.append(
                Service(name="File storage", ok=settings.blob_dir.exists(), detail="Local folder")
            )
    except Exception as exc:  # shown, not raised: Operations must load when storage is down
        out.append(Service(name="File storage", ok=False, detail=type(exc).__name__))
    if settings.mailpit_url:
        reachable = MailpitClient(settings.mailpit_url, timeout=1).reachable()
        out.append(
            Service(
                name="Email intake",
                ok=reachable,
                detail="Mailpit (SMTP)" if reachable else "Mailpit not reachable",
            )
        )
    else:  # a choice, not a fault: emails arrive by upload
        out.append(Service(name="Email intake", ok=True, detail="Upload only"))
    return out


@router.get("/queue", operation_id="get_ops_queue")
def ops_queue(session: SessionDep, settings: SettingsDep, _viewer: OpsViewer) -> OpsQueue:
    """Live work: services, workers by heartbeat, jobs and their leases, and today's spend."""
    now = utcnow()
    workers: list[WorkerOut] = []
    for w in session.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.started_at)):
        ago = int((now - _aware(w.seen_at)).total_seconds())
        if ago > FORGET_AFTER_S:
            continue
        state = "quiet" if ago > QUIET_AFTER_S else "working" if w.job_id else "idle"
        workers.append(
            WorkerOut(
                owner=w.owner,
                state=state,
                seen_seconds_ago=ago,
                started_at=w.started_at,
                job_id=w.job_id,
                jobs_done=w.jobs_done,
            )
        )
    by_status = Counter(s.value for s in session.scalars(select(Job.status)))
    oldest = session.scalar(select(func.min(Job.created_at)).where(Job.status == JobStatus.QUEUED))
    jobs: list[JobOut] = []
    for job, subject in session.execute(
        select(Job, EmailMessage.subject)
        .join(Submission, Submission.id == Job.submission_id)
        .join(EmailMessage, EmailMessage.id == Submission.email_id)
        .order_by(Job.created_at.desc())
        .limit(25)
    ):
        left = (
            int((_aware(job.lease_until) - now).total_seconds())
            if job.lease_until and job.status is JobStatus.RUNNING
            else None
        )
        jobs.append(
            JobOut(
                id=job.id,
                submission_id=job.submission_id,
                subject=subject,
                kind=job.kind,
                trigger=job.trigger,
                status=job.status.value,
                attempts=job.attempts,
                lease_owner=job.lease_owner,
                lease_seconds_left=left,
                taken_over=job.attempts > 1,
                error=job.error,
                created_at=job.created_at,
                started_at=job.started_at,
                finished_at=job.finished_at,
            )
        )
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    calls, hits, cost = session.execute(
        select(
            func.count(),
            func.sum(case((LlmCall.cache_hit, 1), else_=0)),
            func.sum(LlmCall.cost_usd),
        ).where(LlmCall.created_at >= midnight)
    ).one()
    return OpsQueue(
        run_mode=settings.run_mode,
        services=_services(session, settings),
        workers=workers,
        jobs_by_status=dict(by_status),
        oldest_queued_seconds=int((now - _aware(oldest)).total_seconds()) if oldest else None,
        jobs=jobs,
        today_cost_usd=round(float(cost or 0), 4),
        today_calls=int(calls or 0),
        today_cache_rate=round(int(hits or 0) / calls, 3) if calls else 0.0,
    )


def _aware(at: datetime) -> datetime:
    """SQLite returns naive datetimes; every stored time is UTC."""
    return at if at.tzinfo else at.replace(tzinfo=UTC)
