"""Emails that arrive in Mailpit are ingested once, processed, and marked read."""

import threading
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.config import get_settings
from lane.db.models import EmailMessage
from lane.intake import mailpit
from lane.storage.blobs import BlobStore
from tests.docs_factory import text_pdf
from tests.factories import make_email


class FakeMailbox:
    def __init__(self, messages: dict[str, bytes]) -> None:
        self.messages = messages
        self.read: set[str] = set()

    def unread(self) -> list[dict[str, Any]]:
        return [{"ID": k, "Subject": k} for k in self.messages if k not in self.read]

    def raw(self, message_id: str) -> bytes:
        return self.messages[message_id]

    def mark_read(self, message_ids: list[str]) -> None:
        self.read |= set(message_ids)


def test_new_emails_are_ingested_processed_and_marked_read(db: Session, store: BlobStore) -> None:
    raw = make_email([("application.pdf", text_pdf())])
    box = FakeMailbox({"a": raw, "b": raw})  # the same email delivered twice
    processed: list[str] = []
    r = mailpit.poll_once(db, store, get_settings(), box, lambda sid, _t: processed.append(sid))
    assert len(r.ingested) == 1 and r.repeated == 1 and not r.rejected
    assert processed == r.ingested
    assert box.read == {"a", "b"}
    assert db.scalars(select(EmailMessage.source)).all() == ["smtp"]
    # Nothing unread: a second pass does nothing
    again = mailpit.poll_once(db, store, get_settings(), box, lambda sid, _t: processed.append(sid))
    assert not again.ingested and processed == r.ingested


def test_a_rejected_email_is_reported_and_not_retried(db: Session, store: BlobStore) -> None:
    tiny = get_settings().model_copy(update={"intake_max_email_mb": 0})  # every email too big
    box = FakeMailbox({"big": make_email([("application.pdf", text_pdf())])})
    r = mailpit.poll_once(db, store, tiny, box, lambda _sid, _t: None)
    assert r.rejected and not r.ingested and box.read == {"big"}


def test_the_watcher_survives_mailpit_being_down(monkeypatch: Any) -> None:
    class Down:
        def unread(self) -> list[dict[str, Any]]:
            raise ConnectionRefusedError("Mailpit is not running")

        def raw(self, message_id: str) -> bytes:
            raise AssertionError

        def mark_read(self, message_ids: list[str]) -> None:
            raise AssertionError

    stop = threading.Event()
    calls = 0

    def wait(_s: float) -> bool:
        nonlocal calls
        calls += 1
        if calls >= 2:
            stop.set()
        return stop.is_set()

    monkeypatch.setattr(stop, "wait", wait)
    mailpit.watch(get_settings(), lambda _sid, _t: None, stop=stop, mailbox=Down())
    assert calls == 2  # it kept polling instead of crashing
