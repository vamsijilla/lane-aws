"""Email intake from Mailpit, a local SMTP server with a REST API.

An agent's email sent to Mailpit's SMTP port (1025) is picked up here: each unread message is
fetched raw, ingested exactly like an upload (idempotent by Message-ID), handed to the pipeline,
then marked read in Mailpit. Production replaces this with SES or Graph inbound mail to a queue.

    uv run python -m lane.intake.mailpit            # poll every few seconds
    uv run python -m lane.intake.mailpit --once     # one pass
"""

import argparse
import contextlib
import json
import threading
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog
from sqlalchemy.orm import Session

from lane.config import Settings, get_settings
from lane.db.base import get_sessionmaker
from lane.intake.service import IntakeLimits, IntakeRejected, ingest_email
from lane.log import configure_logging
from lane.storage.blobs import BlobStore, blob_store

log = structlog.get_logger()


class Mailbox(Protocol):
    def unread(self) -> list[dict[str, Any]]: ...
    def raw(self, message_id: str) -> bytes: ...
    def mark_read(self, message_ids: list[str]) -> None: ...


class MailpitClient:
    """Mailpit's REST API (v1), with the standard library only."""

    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, body: Any = None) -> bytes:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(  # noqa: S310 (the configured Mailpit URL)
            f"{self.base}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            return resp.read()

    def reachable(self) -> bool:
        try:
            self._request("GET", "/api/v1/info")
        except OSError:
            return False
        return True

    def unread(self) -> list[dict[str, Any]]:
        listing = json.loads(self._request("GET", "/api/v1/messages?limit=100"))
        messages: list[dict[str, Any]] = listing.get("messages") or []
        return [m for m in messages if not m.get("Read")]

    def raw(self, message_id: str) -> bytes:
        return self._request("GET", f"/api/v1/message/{message_id}/raw")

    def mark_read(self, message_ids: list[str]) -> None:
        if message_ids:
            self._request("PUT", "/api/v1/messages", {"IDs": message_ids, "Read": True})


@dataclass
class PollResult:
    ingested: list[str] = field(default_factory=lambda: list[str]())  # new submission ids
    repeated: int = 0  # the same email again; the existing submission was kept
    rejected: list[str] = field(default_factory=lambda: list[str]())


def poll_once(
    session: Session,
    store: BlobStore,
    settings: Settings,
    mailbox: Mailbox,
    process: Callable[[str, str], None],
) -> PollResult:
    """Ingest every unread message, start processing the new ones, then mark them read."""
    result = PollResult()
    done: list[str] = []
    for message in mailbox.unread():
        mid = str(message["ID"])
        try:
            ingested = ingest_email(
                session,
                store,
                mailbox.raw(mid),
                source="smtp",
                limits=IntakeLimits.from_settings(settings),
            )
        except IntakeRejected as exc:
            log.warning("mailpit.rejected", message=mid, reason=str(exc))
            result.rejected.append(f"{message.get('Subject') or mid}: {exc}")
            done.append(mid)  # a rejected email is not retried forever
            continue
        if ingested.created:
            result.ingested.append(ingested.submission_id)
            if ingested.process_id:
                process(ingested.process_id, ingested.trigger)
        else:
            result.repeated += 1
        done.append(mid)
    mailbox.mark_read(done)
    return result


def watch(
    settings: Settings,
    process: Callable[[str, str], None],
    *,
    stop: threading.Event | None = None,
    mailbox: Mailbox | None = None,
) -> None:
    """Poll until stopped. Mailpit being down is logged and retried, never fatal."""
    box = mailbox or MailpitClient(settings.mailpit_url)
    name = type(box).__name__  # MailpitClient, or S3Mailbox for SES (lane/intake/ses.py)
    store = blob_store(settings)
    stop = stop or threading.Event()
    if mailbox is None:
        log.info("mailpit.watching", url=settings.mailpit_url, every_s=settings.mailpit_poll_s)
    while not stop.is_set():
        try:
            with get_sessionmaker()() as session:
                r = poll_once(session, store, settings, box, process)
            if r.ingested or r.rejected:
                log.info("mailpit.polled", ingested=len(r.ingested), rejected=len(r.rejected))
        except OSError as exc:  # connection refused, timeouts
            log.warning("mailbox.unreachable", mailbox=name, error=str(exc))
        stop.wait(settings.mailpit_poll_s)


def start_in_background(settings: Settings, process: Callable[[str, str], None]) -> threading.Event:
    """Start the watcher on a daemon thread; set the returned event to stop it."""
    stop = threading.Event()
    threading.Thread(
        target=watch, args=(settings, process), kwargs={"stop": stop}, daemon=True
    ).start()
    return stop


def main(argv: list[str] | None = None) -> int:
    from lane.pipeline.registry import PIPELINE
    from lane.pipeline.runner import process_submission

    parser = argparse.ArgumentParser(description="Ingest agent emails from Mailpit")
    parser.add_argument("--once", action="store_true", help="one pass, then exit")
    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(json=settings.log_json, pii=settings.pii_attributes)
    if not settings.mailpit_url:
        raise SystemExit("Set LANE_MAILPIT_URL, for example http://localhost:8025")
    store = blob_store(settings)

    def process(sid: str, trigger: str) -> None:
        with get_sessionmaker()() as s:
            process_submission(s, store, settings, sid, steps=PIPELINE, trigger=trigger)

    if args.once:
        with get_sessionmaker()() as session:
            r = poll_once(session, store, settings, MailpitClient(settings.mailpit_url), process)
        print(f"Ingested {len(r.ingested)}, repeated {r.repeated}, rejected {len(r.rejected)}")
        return 0
    with contextlib.suppress(KeyboardInterrupt):
        watch(settings, process)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
