"""In-process step runner.

Each step is recorded with status, duration and a summary, committed as it finishes so the UI
can follow progress. Steps are idempotent: they skip work whose outputs already exist for the
current step version. Production maps each step 1:1 onto a Temporal activity.
"""

import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.audit import chain
from lane.config import Settings
from lane.db.base import utcnow
from lane.db.models import Run, RunStatus, Step, StepStatus, Submission, SubmissionStatus
from lane.events.bus import BUS
from lane.llm.gateway import LlmGateway
from lane.pipeline import fingerprint
from lane.storage.blobs import BlobStore

log = structlog.get_logger()
ACTOR = "system:pipeline"


class TransientStepError(RuntimeError):
    """A failure worth retrying (timeouts, rate limits)."""


@dataclass
class StepContext:
    session: Session
    store: BlobStore
    settings: Settings
    run: Run
    submission: Submission
    # Builds the model gateway on demand. Tests pass a factory with a fake transport.
    llm_factory: Callable[[Settings], LlmGateway] = LlmGateway
    # A replay recomputes every output instead of reusing what a step already stored
    replay: bool = False

    def emit(self, type: str, **data: Any) -> None:
        """Publish a progress event for anyone following this submission live."""
        BUS.publish(self.submission.id, type, {"run_id": self.run.id, **data})


@dataclass
class StepOutcome:
    status: StepStatus = StepStatus.SUCCEEDED
    summary: dict[str, Any] = field(default_factory=lambda: dict[str, Any]())


@dataclass(frozen=True)
class StepDef:
    name: str
    version: str
    fn: Callable[[StepContext], StepOutcome]
    required: bool = True  # a failed required step stops the run
    max_attempts: int = 1


def process_submission(
    session: Session,
    store: BlobStore,
    settings: Settings,
    submission_id: str,
    *,
    steps: list[StepDef],
    trigger: str,
    llm_factory: Callable[[Settings], LlmGateway] = LlmGateway,
    replay: bool = False,
    on_run: Callable[[Run], None] | None = None,
) -> Run:
    """Run the steps in order. `on_run` hears the new run as soon as it is recorded (the worker
    keeps its id on the job, so a worker taking over knows which run was cut off)."""
    submission = session.get(Submission, submission_id)
    if submission is None:
        raise LookupError(f"Submission {submission_id} not found")

    run = Run(
        submission_id=submission.id,
        trigger=trigger,
        email_id=_email_behind(session, submission, trigger),
    )
    session.add(run)
    submission.status = SubmissionStatus.PROCESSING
    session.flush()
    chain.record(
        session,
        actor=ACTOR,
        action="run.started",
        subject_type="run",
        subject_id=run.id,
        payload={
            "submission_id": submission.id,
            "trigger": trigger,
            "steps": [s.name for s in steps],
        },
    )
    session.commit()
    if on_run is not None:
        on_run(run)

    ctx = StepContext(session, store, settings, run, submission, llm_factory, replay)
    ctx.emit("run.started", trigger=trigger, steps=[s.name for s in steps])
    degraded = False
    failed = False
    for step_def in steps:
        record = _execute(ctx, step_def)
        if record.status is StepStatus.FAILED:
            if step_def.required:
                failed = True
                break
            degraded = True
        elif record.status is StepStatus.DEGRADED:
            degraded = True

    run.status = (
        RunStatus.FAILED if failed else RunStatus.DEGRADED if degraded else RunStatus.SUCCEEDED
    )
    run.finished_at = utcnow()
    submission.status = SubmissionStatus.FAILED if failed else SubmissionStatus.READY
    fingerprint.stamp(session, run)
    chain.record(
        session,
        actor=ACTOR,
        action="run.finished",
        subject_type="run",
        subject_id=run.id,
        payload={
            "status": run.status.value,
            "input_hash": run.input_hash,
            "output_hash": run.output_hash,
        },
    )
    session.commit()
    ctx.emit("run.finished", status=run.status.value)
    return run


def _email_behind(session: Session, submission: Submission, trigger: str) -> str | None:
    """The email that started a run: the original for intake, the newest attached reply for a
    follow-up; none for a rerun or replay a person started."""
    if trigger == "intake":
        return submission.email_id
    if trigger == "follow_up":
        return session.scalar(
            select(Submission.email_id)
            .where(Submission.related_to_id == submission.id, Submission.relation == "follow_up")
            .order_by(Submission.relation_at.desc())
            .limit(1)
        )
    return None


def _execute(ctx: StepContext, step_def: StepDef) -> Step:
    session = ctx.session
    record = Step(run_id=ctx.run.id, name=step_def.name, version=step_def.version)
    session.add(record)
    session.commit()
    ctx.emit("step.started", step=step_def.name)

    for attempt in range(1, step_def.max_attempts + 1):
        record.attempt = attempt
        started = time.perf_counter()
        try:
            outcome = step_def.fn(ctx)
        except TransientStepError as exc:
            session.rollback()
            if attempt < step_def.max_attempts:
                time.sleep(min(2 ** (attempt - 1), 8))
                continue
            _fail(record, exc, started)
        except Exception as exc:  # a step failure must never crash the run
            session.rollback()
            log.error("step.failed", step=step_def.name, error=str(exc), tb=traceback.format_exc())
            _fail(record, exc, started)
        else:
            record.status = outcome.status
            record.summary = outcome.summary
            _finish(record, started)
        break

    chain.record(
        session,
        actor=ACTOR,
        action=f"step.{record.status.value}",
        subject_type="step",
        subject_id=record.id,
        payload={
            "run_id": ctx.run.id,
            "name": record.name,
            "version": record.version,
            "duration_ms": record.duration_ms,
            "error": record.error,
        },
    )
    session.commit()
    ctx.emit(
        "step.finished",
        step=record.name,
        status=record.status.value,
        duration_ms=record.duration_ms,
        summary=_brief(record.summary),
        error=record.error,
    )
    return record


def _brief(summary: dict[str, Any] | None) -> dict[str, Any]:
    """Small scalar and count values only; full summaries stay on the run record."""
    out: dict[str, Any] = {}
    for k, v in (summary or {}).items():
        if isinstance(v, bool | int | float | str) or (
            isinstance(v, dict) and all(isinstance(x, int) for x in v.values())  # pyright: ignore[reportUnknownVariableType]
        ):
            out[k] = v
    return out


def _finish(record: Step, started: float) -> None:
    record.finished_at = utcnow()
    record.duration_ms = round((time.perf_counter() - started) * 1000)


def _fail(record: Step, exc: BaseException, started: float) -> None:
    record.status = StepStatus.FAILED
    record.error = f"{exc.__class__.__name__}: {exc}"
    _finish(record, started)


INTERRUPTED = "Interrupted: the service restarted while this step was running. Reprocess to retry."


def interrupt_run(session: Session, run: Run, *, reason: str = INTERRUPTED) -> None:
    """Mark a run cut off (and its step that was running) as failed with the reason, so no
    screen waits on it. The caller commits."""
    if run.status is not RunStatus.RUNNING:
        return
    for step in run.steps:
        if step.status is StepStatus.RUNNING:
            step.status = StepStatus.FAILED
            step.error = reason
            step.finished_at = utcnow()
    run.status = RunStatus.FAILED
    run.finished_at = utcnow()
    sub = session.get(Submission, run.submission_id)
    if sub is not None and sub.status is SubmissionStatus.PROCESSING:
        sub.status = SubmissionStatus.FAILED
    chain.record(
        session,
        actor=ACTOR,
        action="run.interrupted",
        subject_type="run",
        subject_id=run.id,
        payload={"submission_id": run.submission_id, "reason": reason},
    )


def recover_interrupted(session: Session) -> int:
    """Runs still marked running when the service starts were cut off by a restart: nothing is
    running them any more. Mark them failed with the reason, so no screen waits forever.
    (Inline mode only: in queue mode a worker's lease decides, see lane.pipeline.queue.)"""
    runs = list(session.scalars(select(Run).where(Run.status == RunStatus.RUNNING)))
    for run in runs:
        interrupt_run(session, run)
    session.commit()
    return len(runs)
