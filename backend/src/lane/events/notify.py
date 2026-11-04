"""Progress events between processes over Postgres LISTEN/NOTIFY.

The worker publishes each event to its own bus and, through `Publisher`, to a Postgres channel.
The API listens on that channel and republishes every event into its bus, so the live view reads
events the same way whether the run happened in the API (inline) or in a worker (queue).
A notification carries at most 8,000 bytes; a larger event is sent without its details.
"""

import json
import threading
from typing import Any

import psycopg
import structlog
from psycopg import sql
from sqlalchemy.engine import make_url

from lane.config import Settings
from lane.events.bus import BUS, Event

log = structlog.get_logger()
LIMIT = 7900


def available(settings: Settings) -> bool:
    return settings.run_mode == "queue" and settings.resolved_database_url.startswith("postgresql")


def _dsn(settings: Settings) -> str:
    url = make_url(settings.resolved_database_url).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


def encode(key: str, event: Event) -> str:
    body: dict[str, Any] = {"k": key, "t": event.type, "d": event.data}
    text = json.dumps(body, default=str, separators=(",", ":"))
    if len(text.encode()) > LIMIT:
        body["d"] = {"run_id": event.data.get("run_id"), "truncated": True}
        text = json.dumps(body, default=str, separators=(",", ":"))
    return text


class Publisher:
    """One connection for the worker's notifications, reopened if it drops."""

    def __init__(self, settings: Settings) -> None:
        self._dsn = _dsn(settings)
        self._channel = settings.events_channel
        self._lock = threading.Lock()
        self._conn: psycopg.Connection[Any] | None = None

    def send(self, key: str, event: Event) -> None:
        with self._lock:
            try:
                if self._conn is None or self._conn.closed:
                    self._conn = psycopg.connect(self._dsn, autocommit=True)
                self._conn.execute("SELECT pg_notify(%s, %s)", (self._channel, encode(key, event)))
            except psycopg.Error as exc:  # progress is best effort; the run itself goes on
                log.warning("events.notify_failed", error=str(exc))
                self.close()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def listen(settings: Settings, stop: threading.Event) -> None:
    """API side: republish the workers' events into this process's bus until `stop` is set."""
    dsn, channel = _dsn(settings), settings.events_channel
    while not stop.is_set():
        try:
            with psycopg.connect(dsn, autocommit=True) as conn:
                conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(channel)))
                log.info("events.listening", channel=channel)
                while not stop.is_set():
                    for note in conn.notifies(timeout=1.0):
                        body = json.loads(note.payload)
                        BUS.publish(body["k"], body["t"], body["d"], forward=False)
        except psycopg.Error as exc:
            log.warning("events.listen_failed", error=str(exc))
            stop.wait(2.0)


def start_listening(settings: Settings) -> threading.Event:
    stop = threading.Event()
    threading.Thread(target=listen, args=(settings, stop), daemon=True).start()
    return stop
