"""The worker: claims jobs and runs them, holding each under a lease it keeps renewing.

    uv run python -m lane.worker        (make worker; make dev starts it in queue mode)

Kill it at any point: the lease runs out after `worker_lease_s`, another worker (or this one,
restarted) claims the job, marks the cut-off run as interrupted and runs the pipeline again.
Steps skip work already done, so only the unfinished part is redone. In queue mode the worker
also watches Mailpit for agents' emails, and its progress events reach the API's live view
through Postgres NOTIFY.
"""

import contextlib
import os
import signal
import socket
import threading
from collections.abc import Callable
from types import FrameType

import structlog

from lane.config import Settings, get_settings
from lane.db.base import get_sessionmaker, utcnow
from lane.db.models import Job, JobStatus, Run, WorkerHeartbeat
from lane.events import notify
from lane.events.bus import BUS
from lane.log import configure_logging
from lane.pipeline import background, queue
from lane.pipeline.runner import interrupt_run

log = structlog.get_logger()


HEARTBEAT_S = 5.0


class Presence:
    """What this worker tells Operations: alive, and which job it is on."""

    def __init__(self, owner: str) -> None:
        self.owner = owner
        self.job_id: str | None = None
        self.jobs_done = 0

    def beat(self) -> None:
        with get_sessionmaker()() as session:
            row = session.get(WorkerHeartbeat, self.owner) or WorkerHeartbeat(owner=self.owner)
            row.seen_at = utcnow()
            row.job_id = self.job_id
            row.jobs_done = self.jobs_done
            session.add(row)
            session.commit()

    def keep(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.beat()
            except Exception as exc:  # the database may be briefly away; keep working
                log.warning("worker.heartbeat_failed", error=str(exc))
            stop.wait(HEARTBEAT_S)

    def leave(self) -> None:
        with get_sessionmaker()() as session:
            row = session.get(WorkerHeartbeat, self.owner)
            if row is not None:
                session.delete(row)
                session.commit()


def _keep_lease(job_id: str, owner: str, settings: Settings, done: threading.Event) -> None:
    while not done.wait(settings.worker_renew_s):
        with get_sessionmaker()() as session:
            if not queue.renew(session, job_id, owner, lease_s=settings.worker_lease_s):
                log.warning("job.lease_lost", job=job_id)
                return


def _remember_run(job_id: str) -> Callable[[Run], None]:
    def on_run(run: Run) -> None:
        with get_sessionmaker()() as session:
            job = session.get(Job, job_id)
            if job is not None:
                job.run_id = run.id
                session.commit()

    return on_run


def run_job(
    claimed: queue.Claimed, owner: str, settings: Settings, presence: Presence | None = None
) -> None:
    job = claimed.job
    if presence is not None:
        presence.job_id = job.id
    done = threading.Event()
    keeper = threading.Thread(target=_keep_lease, args=(job.id, owner, settings, done), daemon=True)
    keeper.start()
    log.info("job.started", job=job.id, kind=job.kind, attempt=job.attempts)
    try:
        if claimed.reclaimed_from and job.run_id:
            with get_sessionmaker()() as session:
                run = session.get(Run, job.run_id)
                if run is not None:
                    interrupt_run(session, run, reason=queue.WORKER_STOPPED)
                    session.commit()
        if job.kind == "process":
            background.process(job.submission_id, job.trigger, on_run=_remember_run(job.id))
        elif job.kind == "replay":
            assert job.replay_run_id is not None
            background.replay(job.replay_run_id)
        elif job.kind == "analysis":
            background.analysis(job.submission_id)
        else:
            raise ValueError(f"Unknown kind of work: {job.kind}")
    except Exception as exc:  # one failed job never stops the worker
        log.error("job.failed", job=job.id, error=str(exc))
        with get_sessionmaker()() as session:
            queue.finish(session, job.id, JobStatus.FAILED, f"{type(exc).__name__}: {exc}")
    else:
        with get_sessionmaker()() as session:
            queue.finish(session, job.id, JobStatus.DONE)
        log.info("job.done", job=job.id)
    finally:
        done.set()
        if presence is not None:
            presence.job_id = None
            presence.jobs_done += 1


def main() -> int:
    settings = get_settings()
    # The same logging as the API: JSON in production (alarms read it), personal data masked
    configure_logging(json=settings.log_json, pii=settings.pii_attributes)
    if settings.run_mode != "queue":
        log.info("worker.idle", reason="run_mode is inline: the API runs work itself")
        return 0
    owner = f"{socket.gethostname()}:{os.getpid()}"
    stop = threading.Event()

    def _stop(_sig: int, _frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    publisher = notify.Publisher(settings) if notify.available(settings) else None
    if publisher is not None:
        BUS.forward = publisher.send
    if settings.triage_ocr:
        from lane.documents import ocr

        threading.Thread(
            target=ocr.warm_up, args=(settings.triage_ocr_models,), daemon=True
        ).start()

    def enqueue(sid: str, trigger: str) -> None:
        with get_sessionmaker()() as session:
            queue.enqueue(session, sid, kind="process", trigger=trigger)

    if settings.mailpit_url:
        from lane.intake import mailpit

        mailpit.start_in_background(settings, enqueue)
    if settings.ses_inbox_prefix:  # real email: SES stores it in S3, this ingests it
        from lane.intake import ses

        ses.start_in_background(settings, enqueue)

    presence = Presence(owner)
    threading.Thread(target=presence.keep, args=(stop,), daemon=True).start()
    log.info("worker.ready", worker=owner, lease_s=settings.worker_lease_s)
    while not stop.is_set():
        with get_sessionmaker()() as session:
            claimed = queue.claim(
                session,
                owner,
                lease_s=settings.worker_lease_s,
                max_attempts=settings.worker_max_attempts,
            )
        if claimed is None:
            stop.wait(settings.worker_poll_s)
            continue
        run_job(claimed, owner, settings, presence)
    presence.leave()  # a clean stop; a killed worker simply goes quiet
    if publisher is not None:
        publisher.close()
    log.info("worker.stopped", worker=owner)
    return 0


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        raise SystemExit(main())
