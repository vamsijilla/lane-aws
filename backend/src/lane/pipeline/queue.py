"""Work queue: jobs in the database, held by a worker under a lease.

The API (or the Mailpit watcher) enqueues a job; a worker claims the oldest one with
`SELECT ... FOR UPDATE SKIP LOCKED`, so two workers never take the same job, and keeps renewing
its lease while it works. If the worker dies, the lease runs out and another worker claims the
job again; the pipeline's steps skip work already done, so the run resumes rather than restarts.
Production maps this 1:1 onto Temporal or Step Functions (docs/DECISIONS.md).
"""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from lane.audit import chain
from lane.db.base import utcnow
from lane.db.models import Job, JobStatus

ACTOR = "system:worker"
GAVE_UP = "Stopped after {n} attempts: the worker running it stopped each time."
WORKER_STOPPED = "Interrupted: the worker running it stopped. Another worker took the job over."


@dataclass(frozen=True)
class Claimed:
    job: Job
    reclaimed_from: str | None  # the worker whose lease ran out, when this is a second attempt


def enqueue(
    session: Session,
    submission_id: str,
    *,
    kind: str,
    trigger: str,
    replay_run_id: str | None = None,
) -> Job:
    job = Job(submission_id=submission_id, kind=kind, trigger=trigger, replay_run_id=replay_run_id)
    session.add(job)
    session.flush()
    chain.record(
        session,
        actor=ACTOR,
        action="job.queued",
        subject_type="job",
        subject_id=job.id,
        payload={"submission_id": submission_id, "kind": kind, "trigger": trigger},
    )
    session.commit()
    return job


def claim(session: Session, owner: str, *, lease_s: float, max_attempts: int) -> Claimed | None:
    """The oldest job waiting, or one whose worker stopped renewing its lease."""
    while True:
        now = utcnow()
        job = session.scalars(
            select(Job)
            .where(
                or_(
                    Job.status == JobStatus.QUEUED,
                    and_(Job.status == JobStatus.RUNNING, Job.lease_until < now),
                )
            )
            .order_by(Job.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        if job is None:
            session.rollback()
            return None
        previous = job.lease_owner if job.status is JobStatus.RUNNING else None
        if previous is not None and job.attempts >= max_attempts:
            _close(session, job, JobStatus.FAILED, GAVE_UP.format(n=job.attempts))
            continue
        job.status = JobStatus.RUNNING
        job.attempts += 1
        job.lease_owner = owner
        job.lease_until = now + timedelta(seconds=lease_s)
        job.started_at = job.started_at or now
        chain.record(
            session,
            actor=ACTOR,
            action="job.claimed",
            subject_type="job",
            subject_id=job.id,
            payload={"worker": owner, "attempt": job.attempts, "lease_ran_out_for": previous},
        )
        session.commit()
        return Claimed(job, previous)


def renew(session: Session, job_id: str, owner: str, *, lease_s: float) -> bool:
    """Extend the lease; False when another worker has taken the job over."""
    result = session.execute(
        update(Job)
        .where(Job.id == job_id, Job.lease_owner == owner, Job.status == JobStatus.RUNNING)
        .values(lease_until=utcnow() + timedelta(seconds=lease_s))
    )
    session.commit()
    return bool(getattr(result, "rowcount", 0))


def finish(session: Session, job_id: str, status: JobStatus, error: str | None = None) -> None:
    job = session.get(Job, job_id)
    if job is not None:
        _close(session, job, status, error)


def _close(session: Session, job: Job, status: JobStatus, error: str | None) -> None:
    job.status = status
    job.error = error
    job.finished_at = utcnow()
    job.lease_until = None
    chain.record(
        session,
        actor=ACTOR,
        action=f"job.{status.value}",
        subject_type="job",
        subject_id=job.id,
        payload={"run_id": job.run_id, "attempts": job.attempts, "error": error},
    )
    session.commit()
