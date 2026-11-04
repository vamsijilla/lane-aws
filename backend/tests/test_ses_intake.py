"""Real inbound email (SES into S3): only an authenticated, allowlisted sender is processed.
Anything else is held, never ingested, so a stranger cannot spend the model budget."""

import io
from typing import Any

import pytest
from sqlalchemy.orm import Session

from lane.config import get_settings
from lane.intake.mailpit import poll_once
from lane.intake.ses import S3Mailbox, held_reason
from lane.storage.blobs import BlobStore
from tests.factories import PDF, make_email

ALLOW = ["agent@agency.example", "@laneunder.online"]
SES_PASS = {
    "X-SES-Virus-Verdict": "PASS",
    "X-SES-Spam-Verdict": "PASS",
    "Authentication-Results": "amazonses.com; spf=pass smtp.mailfrom=agent@agency.example; "
    "dkim=pass header.i=@agency.example; dmarc=pass header.from=agency.example;",
}


class FakeS3:
    """Just the calls S3Mailbox makes, over a dict."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def list_objects_v2(self, *, Bucket: str, Prefix: str, MaxKeys: int) -> dict[str, Any]:
        keys = sorted(k for k in self.objects if k.startswith(Prefix))[:MaxKeys]
        return {"Contents": [{"Key": k} for k in keys]}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        return {"Body": io.BytesIO(self.objects[Key])}

    def copy_object(self, *, Bucket: str, Key: str, CopySource: dict[str, str]) -> None:
        self.objects[Key] = self.objects[CopySource["Key"]]

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        del self.objects[Key]


def email(headers: dict[str, str] | None = None, **kw: Any) -> bytes:
    return make_email([("a.pdf", PDF)], headers=SES_PASS if headers is None else headers, **kw)


def poll(db: Session, store: BlobStore, s3: FakeS3) -> tuple[list[str], Any]:
    processed: list[str] = []
    box = S3Mailbox(s3, "bucket", "inbound/", ALLOW)
    result = poll_once(db, store, get_settings(), box, lambda sid, _t: processed.append(sid))
    return processed, result


def test_an_authenticated_allowlisted_email_is_processed_and_filed(
    db: Session, store: BlobStore
) -> None:
    s3 = FakeS3()
    s3.objects["inbound/m1"] = email()
    processed, result = poll(db, store, s3)
    assert len(result.ingested) == 1 and len(processed) == 1
    assert list(s3.objects) == ["inbound/done/m1"]


@pytest.mark.parametrize(
    ("headers", "sender", "why"),
    [
        # A forged From: SES found no valid signature of the From domain
        (
            {
                "X-SES-Virus-Verdict": "PASS",
                "X-SES-Spam-Verdict": "PASS",
                "Authentication-Results": "amazonses.com; spf=fail; dkim=pass header.i=@evil.example; dmarc=fail",
            },
            "Agent <agent@agency.example>",
            "not authenticated",
        ),
        # A sender who wrote their own verdict: only SES's counts
        (
            {
                "X-SES-Virus-Verdict": "PASS",
                "X-SES-Spam-Verdict": "PASS",
                "Authentication-Results": "evil.example; dmarc=pass",
            },
            "Agent <agent@agency.example>",
            "not authenticated",
        ),
        ({**SES_PASS, "X-SES-Virus-Verdict": "FAIL"}, "Agent <agent@agency.example>", "virus"),
        ({**SES_PASS, "X-SES-Spam-Verdict": "FAIL"}, "Agent <agent@agency.example>", "spam"),
        (SES_PASS, "Stranger <someone@elsewhere.example>", "not on the allowlist"),
    ],
    ids=["forged sender", "self-written verdict", "virus", "spam", "not on the allowlist"],
)
def test_anything_else_is_held_and_never_ingested(
    db: Session, store: BlobStore, headers: dict[str, str], sender: str, why: str
) -> None:
    s3 = FakeS3()
    s3.objects["inbound/m1"] = email(headers, sender=sender)
    processed, result = poll(db, store, s3)
    assert result.ingested == [] and processed == []
    assert list(s3.objects) == ["inbound/held/m1"]
    reason = held_reason(s3.objects["inbound/held/m1"], ALLOW)
    assert reason is not None and why in reason


def test_a_whole_domain_can_be_allowed() -> None:
    raw = email(
        {
            "X-SES-Virus-Verdict": "PASS",
            "X-SES-Spam-Verdict": "PASS",
            "Authentication-Results": "amazonses.com; dkim=pass header.i=@laneunder.online; dmarc=pass",
        },
        sender="Daniel Okafor <agent@laneunder.online>",
    )
    assert held_reason(raw, ALLOW) is None


def test_the_same_email_twice_is_processed_once(db: Session, store: BlobStore) -> None:
    s3 = FakeS3()
    s3.objects["inbound/m1"] = email()
    s3.objects["inbound/m2"] = email()  # the same message, delivered again
    processed, result = poll(db, store, s3)
    assert len(processed) == 1 and result.repeated == 1
    assert sorted(s3.objects) == ["inbound/done/m1", "inbound/done/m2"]


def test_filed_and_held_messages_are_not_read_again(db: Session, store: BlobStore) -> None:
    s3 = FakeS3()
    s3.objects["inbound/done/old"] = email()
    s3.objects["inbound/held/bad"] = email()
    s3.objects["inbound/AMAZON_SES_SETUP_NOTIFICATION"] = b"setup"
    processed, _ = poll(db, store, s3)
    assert processed == []
