"""Personal data is masked everywhere unless an underwriter reveals it; roles limit what each
person can see and do; every action names who did it."""

from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.api.auth import issue_token, read_token
from lane.api.extractions import fact_out
from lane.api.main import create_app
from lane.api.privacy import Masker
from lane.audit import chain, describe
from lane.config import get_settings
from lane.db.models import AuditEvent, ConfidenceBand, Fact, ResolvedField
from lane.log import mask_pii
from lane.storage.blobs import BlobStore
from tests.seed import seed_submission

RAW = ("D1234567", "1980-05-01")  # a driver's identifiers; the company's address is not masked


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def sid(db: Session, store: BlobStore) -> str:
    return seed_submission(db, store)


def headers(client: TestClient, role: str) -> dict[str, str]:
    token = client.post("/api/v1/auth/demo-login", json={"role": role}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def field_id(db: Session, attribute: str) -> str:
    f = db.scalars(select(ResolvedField).where(ResolvedField.attribute == attribute)).first()
    assert f is not None
    return f.id


# --- masking -------------------------------------------------------------------------------


def test_masker_rules() -> None:
    m = Masker(get_settings(), date(2026, 10, 22))
    assert m.value("license_number", "C4471925") == "C44•••925"
    assert m.value("date_of_birth", "1980-05-01") == "May 1, ••••"
    # Business data is never masked; a driver's own address is
    assert m.attribute("fein") is None and m.attribute("mailing_address") is None
    assert m.attribute("garaging_address") is None
    assert m.attribute("license.address") == "home_address"
    assert m.value("home_address", "12 Elm Street") == "[street withheld]"
    assert m.attribute("rows[3].date_of_birth") == "date_of_birth"
    assert m.attribute("claims[0].driver_date_of_birth") == "date_of_birth"
    assert m.attribute("legal_name") is None
    assert m.key("lic:C4471925") == "lic:C44•••925"
    assert m.tree({"drivers": [{"license_number": {"value": "C4471925", "cell": "B2"}}]}) == {
        "drivers": [{"license_number": {"value": "C44•••925", "cell": "B2"}}]
    }


def test_facts_mask_value_and_quote() -> None:
    fact = Fact(
        id="x",
        path="rows[0].date_of_birth",
        value_raw="05/01/1980",
        value_norm="1980-05-01",
        quote="DOB 05/01/1980",
        method="text",
        grounding="exact",
        confidence=ConfidenceBand.HIGH,
        confidence_reasons=[],
    )
    out = fact_out(fact, Masker(get_settings(), date(2026, 10, 22)))
    assert out.value == "May 1, ••••"
    assert out.quote == "[date hidden]"  # a quote that is not a date alone: hidden whole
    assert out.value_norm == "May 1, ••••"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/submissions/{sid}/entities",
        "/api/v1/submissions/{sid}/conflicts",
        "/api/v1/submissions/{sid}",
        "/api/v1/submissions/{sid}/audit",
    ],
)
def test_no_raw_personal_data_in_responses(client: TestClient, sid: str, path: str) -> None:
    text = client.get(path.format(sid=sid)).text
    for raw in RAW:
        assert raw not in text, (path, raw)


def test_field_evidence_is_masked_until_revealed(client: TestClient, db: Session, sid: str) -> None:
    fid = field_id(db, "license_number")
    body = client.get(f"/api/v1/fields/{fid}").json()
    assert body["value_display"] == "D12•••567" and body["personal_data"] is True
    assert body["candidates"][0]["display"] == "D12•••567"
    assert body["candidates"][0]["sources"][0]["title"] == "Application"

    assert (
        client.post(f"/api/v1/fields/{fid}/reveal", headers=headers(client, "ops")).status_code
        == 403
    )
    assert (
        client.post(f"/api/v1/fields/{fid}/reveal", headers=headers(client, "admin")).status_code
        == 403
    )
    revealed = client.post(f"/api/v1/fields/{fid}/reveal", headers=headers(client, "underwriter"))
    assert revealed.json()["value_display"] == "D1234567"
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "pii.revealed")).one()
    assert event.actor == "user:underwriter" and event.subject_id == fid
    assert client.get("/api/v1/fields/nope").status_code == 404


# --- roles and tokens ----------------------------------------------------------------------


def test_tokens_are_signed_and_expire() -> None:
    settings = get_settings()
    token = issue_token("ops", settings, now=1000.0)
    assert read_token(token, settings, now=1001.0) == "ops"
    assert read_token(token, settings, now=1000.0 + settings.session_ttl_hours * 3600 + 1) is None
    body, _, sig = token.partition(".")
    assert read_token(f"{body}.{'0' * len(sig)}", settings, now=1001.0) is None
    assert read_token("garbage", settings) is None


def test_session_and_bad_tokens(client: TestClient) -> None:
    me = client.get("/api/v1/auth/me").json()
    assert me["role"] == "underwriter" and me["signed_in"] is False
    assert "reveal_pii" in me["permissions"]
    ops = client.get("/api/v1/auth/me", headers=headers(client, "ops")).json()
    assert ops["role"] == "ops" and ops["signed_in"] and "reveal_pii" not in ops["permissions"]
    bad = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer x.y"})
    assert bad.status_code == 401
    assert client.post("/api/v1/auth/demo-login", json={"role": "root"}).status_code == 422
    token = headers(client, "ops")["Authorization"].split()[1]
    assert client.get(f"/api/v1/auth/me?access_token={token}").json()["role"] == "ops"


def test_roles_limit_content_and_actions(client: TestClient, sid: str) -> None:
    ops = headers(client, "ops")
    admin = headers(client, "admin")
    assert client.get(f"/api/v1/submissions/{sid}/actions", headers=ops).status_code == 403
    assert client.get("/api/v1/facts?ids=x", headers=ops).status_code == 403
    assert client.get("/api/v1/pages/p/image", headers=ops).status_code == 403
    assert (
        client.post(f"/api/v1/submissions/{sid}/actions/redraft", headers=admin).status_code == 403
    )
    assert (
        client.post(
            "/api/v1/findings/x/review", json={"state": "accepted"}, headers=admin
        ).status_code
        == 403
    )
    assert (
        client.post(f"/api/v1/submissions/{sid}/escalations/approve", headers=ops).status_code
        == 403
    )
    # Ops may see extraction status, not extracted content.
    rows = client.get(f"/api/v1/submissions/{sid}/extractions", headers=ops).json()
    assert all(r["data"] is None for r in rows)


def test_actions_name_who_did_them(client: TestClient, db: Session, sid: str) -> None:
    client.post(f"/api/v1/submissions/{sid}/actions/redraft")
    item = client.get(f"/api/v1/submissions/{sid}/actions").json()["items"][0]
    client.patch(
        f"/api/v1/request-items/{item['id']}",
        json={"included": False},
        headers=headers(client, "underwriter"),
    )
    event = db.scalars(
        select(AuditEvent).where(AuditEvent.action == "agent_request.item_excluded")
    ).one()
    assert event.actor == "user:underwriter"


def test_logs_mask_personal_data() -> None:
    processor = mask_pii(get_settings().pii_attributes)
    out = processor(
        None, "info", {"event": "x", "license_number": "C4471925", "driver_date_of_birth": "1980"}
    )
    assert out["license_number"] == out["driver_date_of_birth"] == "[masked]"
    assert out["event"] == "x"


def test_the_audit_timeline_masks_a_resolved_personal_value(
    client: TestClient, db: Session, sid: str
) -> None:
    fid = field_id(db, "license_number")
    chain.record(
        db,
        actor="user:underwriter",
        action="field.overridden",
        subject_type="field",
        subject_id=fid,
        payload={
            "field": "License number",
            "attribute": "license_number",
            "before": "D1234567",
            "after": "D7654321",
            "reason": "Agent sent the new card",
        },
    )
    db.commit()
    body = client.get(f"/api/v1/submissions/{sid}/audit").text
    assert "D1234567" not in body and "D7654321" not in body
    event = next(
        e
        for e in client.get(f"/api/v1/submissions/{sid}/audit").json()["events"]
        if e["action"] == "field.overridden"
    )
    assert event["kind"] == "human"
    assert event["summary"] == (
        "Corrected License number from D12•••567 to D76•••321: Agent sent the new card"
    )


@pytest.mark.parametrize(
    "action",
    sorted(
        {
            "email.received",
            "email.resent",
            "intake.completed",
            "attachment.accepted",
            "attachment.duplicate",
            "attachment.expanded",
            "attachment.triaged",
            "attachment.unreadable",
            "attachment.damaged",
            "document.classified",
            "document.extracted",
            "submission.resolved",
            "submission.decided",
            "submission.analyzed",
            "analysis.approved",
            "escalation.approved",
            "agent_request.drafted",
            "agent_request.item_included",
            "run.started",
            "run.finished",
            "run.interrupted",
            "run.replayed",
            "conflict.resolved",
            "conflict.reopened",
            "field.overridden",
            "field.override_cleared",
            "pii.revealed",
            "security.injection_detected",
            "finding.accepted",
            "step.succeeded",
        }
    ),
)
def test_every_audit_action_reads_as_a_sentence(action: str) -> None:
    text = describe.summary(action, {}, lambda _a, v: str(v))
    assert text and text[0].isupper() and "." not in text.split(" ")[0]


def test_a_social_security_number_is_always_masked_and_a_fein_never() -> None:
    from lane.api.privacy import mask_ssn

    m = Masker(get_settings(), date(2026, 10, 22))
    assert m.attribute("owner_ssn") == "ssn" and m.attribute("applicant.SSN") == "ssn"
    assert m.value("ssn", "123-45-6789") == "•••-••-6789"
    assert (
        m.text("Owner SSN 123-45-6789, FEIN 12-3456789") == "Owner SSN •••-••-6789, FEIN 12-3456789"
    )
    assert mask_ssn("phone 209-555-0148") == "phone 209-555-0148"  # a phone is not an SSN


def test_an_ssn_in_any_response_is_masked_even_for_an_underwriter(
    client: TestClient, db: Session, sid: str
) -> None:
    from lane.db.models import EmailMessage, Submission

    sub = db.get(Submission, sid)
    email = db.get(EmailMessage, sub.email_id) if sub else None
    assert email is not None
    email.subject = "Owner SSN 123-45-6789"
    db.commit()
    for path in ("/api/v1/submissions", f"/api/v1/submissions/{sid}"):
        text = client.get(path).text  # tests sign in as an underwriter
        assert "123-45-6789" not in text and "•••-••-6789" in text, path
