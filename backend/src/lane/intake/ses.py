"""Email intake from SES: messages SES stored in S3, offered to the same poller as Mailpit's
(`lane/intake/mailpit.py`: `poll_once`, `watch`), so ingest, linking and limits are the same.

Before a message is offered for ingest, code checks it came from someone allowed to spend the
model budget: SES's virus and spam scans passed, the sender is authenticated (DMARC, or DKIM for the From
domain, so a forged From fails), and the From address is on the allowlist. Anything else is
moved to `held/` and logged, never ingested. Ingested messages move to `done/`.
"""

import re
import threading
from collections.abc import Callable
from email import message_from_bytes, policy
from email.message import Message
from email.utils import parseaddr
from typing import Any, cast

import structlog

from lane.config import Settings

log = structlog.get_logger()

# SES's own verdicts, in the stored message's headers
_RESULT = re.compile(r"\b(dmarc|dkim|spf)=(\w+)([^;]*)", re.IGNORECASE)
_DKIM_DOMAIN = re.compile(r"header\.(?:i|d)=@?([\w.-]+)", re.IGNORECASE)


def sender_of(msg: Message) -> str:
    return parseaddr(str(msg.get("From", "")))[1].strip().lower()


def allowed(address: str, allowlist: list[str]) -> bool:
    domain = address.rpartition("@")[2]
    for entry in (e.strip().lower() for e in allowlist):
        if entry and (address == entry or (entry.startswith("@") and domain == entry[1:])):
            return True
    return False


def authenticated(msg: Message, address: str) -> bool:
    """SES's Authentication-Results: DMARC passed, or a DKIM signature of the From domain did."""
    domain = address.rpartition("@")[2]
    for header in msg.get_all("Authentication-Results") or []:
        text = str(header)
        if not text.strip().lower().startswith("amazonses.com"):
            continue  # only SES's own verdict counts, never one the sender wrote
        for kind, result, rest in _RESULT.findall(text):
            if result.lower() != "pass":
                continue
            if kind.lower() == "dmarc":
                return True
            if kind.lower() == "dkim":
                signed = _DKIM_DOMAIN.search(rest)
                if signed and (
                    signed.group(1).lower() == domain
                    or domain.endswith("." + signed.group(1).lower())
                ):
                    return True
    return False


def held_reason(raw: bytes, allowlist: list[str]) -> str | None:
    """Why a message must not be processed; None when it may be."""
    msg = message_from_bytes(raw, policy=policy.compat32)
    if str(msg.get("X-SES-Virus-Verdict", "")).strip().upper() != "PASS":
        return "the virus scan did not pass"
    if str(msg.get("X-SES-Spam-Verdict", "")).strip().upper() != "PASS":
        return "the spam scan did not pass"
    address = sender_of(msg)
    if not address:
        return "no sender address"
    if not allowed(address, allowlist):
        return f"{address} is not on the allowlist"
    if not authenticated(msg, address):
        return f"{address} is not authenticated (DMARC or DKIM)"
    return None


class S3Mailbox:
    """The `Mailbox` protocol over an S3 prefix. Storage errors surface as OSError, which the
    watcher logs and retries, as it does for Mailpit being down."""

    def __init__(self, client: Any, bucket: str, prefix: str, allowlist: list[str]) -> None:
        self.client = client
        self.bucket = bucket
        self.prefix = prefix if prefix.endswith("/") else prefix + "/"
        self.allowlist = allowlist
        self._raw: dict[str, bytes] = {}

    @classmethod
    def from_settings(cls, settings: Settings) -> "S3Mailbox":
        import boto3

        client = boto3.client(  # pyright: ignore[reportUnknownMemberType]
            "s3",
            region_name=settings.s3_region,
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key_id or None,
            aws_secret_access_key=settings.s3_secret_access_key.get_secret_value() or None,
        )
        return cls(
            client, settings.s3_bucket, settings.ses_inbox_prefix, settings.intake_allowed_senders
        )

    def _call(self, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except OSError:
            raise
        except Exception as exc:  # botocore's errors: retried by the watcher like any outage
            raise OSError(f"S3 inbox: {exc}") from exc

    def _move(self, key: str, folder: str) -> None:
        target = f"{self.prefix}{folder}/{key[len(self.prefix) :]}"
        self._call(
            lambda: self.client.copy_object(
                Bucket=self.bucket, Key=target, CopySource={"Bucket": self.bucket, "Key": key}
            )
        )
        self._call(lambda: self.client.delete_object(Bucket=self.bucket, Key=key))

    def unread(self) -> list[dict[str, Any]]:
        listing = cast(
            dict[str, Any],
            self._call(
                lambda: self.client.list_objects_v2(
                    Bucket=self.bucket, Prefix=self.prefix, MaxKeys=100
                )
            ),
        )
        offered: list[dict[str, Any]] = []
        for obj in cast(list[dict[str, Any]], listing.get("Contents") or []):
            key = str(obj["Key"])
            rest = key[len(self.prefix) :]
            # done/ and held/ are kept, never offered; SES's setup probe is not a message
            if not rest or "/" in rest or rest == "AMAZON_SES_SETUP_NOTIFICATION":
                continue
            raw = self._get(key)
            reason = held_reason(raw, self.allowlist)
            if reason:
                log.warning("intake.held", key=key, reason=reason)
                self._move(key, "held")
                continue
            self._raw[key] = raw
            subject = str(message_from_bytes(raw, policy=policy.compat32).get("Subject", ""))
            offered.append({"ID": key, "Subject": subject})
        return offered

    def _get(self, key: str) -> bytes:
        body = self._call(lambda: self.client.get_object(Bucket=self.bucket, Key=key))["Body"]
        return cast(bytes, body.read())

    def raw(self, message_id: str) -> bytes:
        return self._raw.pop(message_id, None) or self._get(message_id)

    def mark_read(self, message_ids: list[str]) -> None:
        for key in message_ids:
            self._move(key, "done")


def start_in_background(settings: Settings, process: Callable[[str, str], None]) -> threading.Event:
    """Watch the SES inbox on a daemon thread, with the same poller as Mailpit."""
    from lane.intake import mailpit

    stop = threading.Event()
    box = S3Mailbox.from_settings(settings)
    log.info("ses.watching", bucket=settings.s3_bucket, prefix=box.prefix)
    threading.Thread(
        target=mailpit.watch,
        args=(settings, process),
        kwargs={"stop": stop, "mailbox": box},
        daemon=True,
    ).start()
    return stop
