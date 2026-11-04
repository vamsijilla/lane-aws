"""Long-running work a person or an email starts: process a submission, replay a run, run an
approved expert review.

In inline mode (the default, and tests) the API runs it on a background thread of its own
process. In queue mode it becomes a job in the database and a worker (`lane.worker`) runs the
same functions, so a restart of either process loses nothing (docs/DECISIONS.md).
"""

from collections.abc import Callable

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from lane.config import get_settings
from lane.db.base import get_sessionmaker, utcnow
from lane.db.models import Run, RunStatus, Submission
from lane.pipeline import queue
from lane.pipeline.registry import PIPELINE
from lane.pipeline.runner import process_submission
from lane.storage.blobs import blob_store


def process(submission_id: str, trigger: str, on_run: Callable[[Run], None] | None = None) -> None:
    """Own session, never raises into the server."""
    settings = get_settings()
    with get_sessionmaker()() as session:
        process_submission(
            session,
            blob_store(settings),
            settings,
            submission_id,
            steps=PIPELINE,
            trigger=trigger,
            on_run=on_run,
        )


def replay(run_id: str) -> None:
    from lane.review import replay as replays

    settings = get_settings()
    with get_sessionmaker()() as session:
        run = session.get(Run, run_id)
        assert run is not None
        try:
            replays.replay(session, blob_store(settings), settings, run)
        except Exception as exc:  # a replay failure is a visible run state, not a crash
            session.rollback()
            run = session.get(Run, run_id)
            assert run is not None
            run.status = RunStatus.FAILED
            run.finished_at = utcnow()
            run.versions = {"error": f"{type(exc).__name__}: {exc}"}
            session.commit()


def analysis(submission_id: str) -> None:
    """Run the approved expert review, then refresh the agent request."""
    from lane.actions.service import build_actions
    from lane.analyze.service import analyze_submission
    from lane.llm.gateway import LlmGateway

    settings = get_settings()
    with get_sessionmaker()() as session:
        sub = session.get(Submission, submission_id)
        if sub is None:
            return
        analyze_submission(session, sub, settings, LlmGateway, run_id=None)
        build_actions(session, sub, settings, LlmGateway, run_id=None, redraft=False)


def start(
    background: BackgroundTasks,
    session: Session,
    kind: str,
    submission_id: str,
    *,
    trigger: str,
    replay_run_id: str | None = None,
) -> None:
    """Run now on a thread (inline), or queue it for a worker (queue)."""
    if get_settings().run_mode == "queue":
        queue.enqueue(
            session, submission_id, kind=kind, trigger=trigger, replay_run_id=replay_run_id
        )
    elif kind == "process":
        background.add_task(process, submission_id, trigger)
    elif kind == "replay":
        assert replay_run_id is not None
        background.add_task(replay, replay_run_id)
    elif kind == "analysis":
        background.add_task(analysis, submission_id)
    else:
        raise ValueError(f"Unknown kind of work: {kind}")
