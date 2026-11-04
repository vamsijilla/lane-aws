"""Live progress events per submission, for the intake view and anything else that follows a run.

Thread-safe, one bus per process: the pipeline publishes with a function call; the event stream
reads from a bounded buffer per submission, so a client that reconnects can resume from the last
event it saw. When the pipeline runs in a separate worker (queue mode), the worker forwards each
event over Postgres LISTEN/NOTIFY and the API republishes it here (lane.events.notify).
"""

import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class Event:
    seq: int
    type: str
    data: dict[str, Any]
    at: str


@dataclass
class _Channel:
    events: deque[Event]
    seq: int = 0
    finished: bool = False


@dataclass
class Bus:
    capacity: int = 2000
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _channels: dict[str, _Channel] = field(default_factory=lambda: dict[str, _Channel]())
    # Also send each event elsewhere (a worker sends its events to the API, lane.events.notify)
    forward: Callable[[str, Event], None] | None = None

    def _channel(self, key: str) -> _Channel:
        ch = self._channels.get(key)
        if ch is None:
            ch = self._channels[key] = _Channel(deque(maxlen=self.capacity))
        return ch

    def publish(
        self, key: str, type: str, data: dict[str, Any] | None = None, *, forward: bool = True
    ) -> Event:
        event = self._append(key, type, data)
        if forward and self.forward is not None:
            self.forward(key, event)
        return event

    def _append(self, key: str, type: str, data: dict[str, Any] | None) -> Event:
        with self._lock:
            ch = self._channel(key)
            ch.seq += 1
            event = Event(
                ch.seq, type, data or {}, datetime.now(UTC).isoformat(timespec="milliseconds")
            )
            ch.events.append(event)
            if type == "run.started":
                ch.finished = False
            elif type == "run.finished":
                ch.finished = True
            return event

    def since(self, key: str, after: int) -> tuple[list[Event], bool]:
        """Events after sequence number `after`, and whether the latest run has finished."""
        with self._lock:
            ch = self._channels.get(key)
            if ch is None:
                return [], True
            return [e for e in ch.events if e.seq > after], ch.finished

    def last_seq(self, key: str) -> int:
        with self._lock:
            ch = self._channels.get(key)
            return ch.seq if ch else 0


BUS = Bus()
