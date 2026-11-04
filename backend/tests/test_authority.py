"""Who may do what. The demo signs in as named people, one per role; the API enforces decision
authority (settings.decision_authority), so a hidden button never stands in for a check.

The seeded submission's route is a potential decline: proceeding to quote is at risk."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.api.main import create_app
from lane.db.models import AuditEvent, DecisionRecord, RuleConfirmation, Run, RunStatus
from lane.storage.blobs import BlobStore
from tests.seed import seed_submission

PEOPLE = {
    "underwriter": "priya",
    "senior_underwriter": "daniel",
    "viewer": "guest",
    "ops": "sam",
    "admin": "alex",
}


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def sid(db: Session, store: BlobStore) -> str:
    return seed_submission(db, store)


def as_person(client: TestClient, role: str) -> dict[str, str]:
    body = client.post(
        "/api/v1/auth/demo-login", json={"role": role, "person": PEOPLE[role]}
    ).json()
    assert body["role"] == role and body["name"]
    return {"Authorization": f"Bearer {body['token']}"}


def decide(client: TestClient, sid: str, role: str, decision: str) -> int:
    r = client.post(
        f"/api/v1/submissions/{sid}/decision",
        json={"decision": decision, "reason": "Test reason"},
        headers=as_person(client, role),
    )
    return r.status_code


def test_one_named_person_per_role(client: TestClient) -> None:
    people = client.get("/api/v1/auth/people").json()
    assert sorted(p["role"] for p in people) == sorted(PEOPLE)
    me = client.get("/api/v1/auth/me", headers=as_person(client, "senior_underwriter")).json()
    assert me["name"] == "Daniel Reyes" and me["role_label"] == "Senior underwriter"
    assert "quote_at_risk" in me["authority"] and "confirm_cleared_decline" in me["authority"]


def test_an_underwriter_refers_back_or_declines_and_is_named_in_the_audit(
    client: TestClient, db: Session, sid: str
) -> None:
    assert decide(client, sid, "underwriter", "refer_back") == 200
    assert decide(client, sid, "underwriter", "decline") == 200
    actors = {
        e.actor
        for e in db.scalars(select(AuditEvent).where(AuditEvent.action == "decision.recorded"))
    }
    assert actors == {"user:Priya Shah"}


def test_proceeding_to_quote_at_risk_needs_a_senior_underwriter(
    client: TestClient, sid: str
) -> None:
    r = client.post(
        f"/api/v1/submissions/{sid}/decision",
        json={"decision": "quote", "reason": "Test reason"},
        headers=as_person(client, "underwriter"),
    )
    assert r.status_code == 403 and r.json()["detail"] == "Needs senior underwriter authority"
    assert decide(client, sid, "senior_underwriter", "quote") == 200


def test_an_underwriter_may_quote_when_nothing_is_at_risk(
    client: TestClient, db: Session, sid: str
) -> None:
    rec = db.get(DecisionRecord, sid)
    assert rec is not None
    rec.potential_decline = False
    db.commit()
    assert decide(client, sid, "underwriter", "quote") == 200


def test_viewer_operations_and_admin_cannot_decide(client: TestClient, sid: str) -> None:
    for role in ("viewer", "ops", "admin"):
        assert decide(client, sid, role, "refer_back") == 403, role


def test_confirming_a_cleared_decline_needs_a_senior_underwriter(
    client: TestClient, db: Session, sid: str
) -> None:
    run = Run(submission_id=sid, trigger="follow_up", status=RunStatus.SUCCEEDED)
    db.add(run)
    db.flush()
    c = RuleConfirmation(
        submission_id=sid,
        run_id=run.id,
        rule_id="DRV-01",
        title="Every driver holds a valid CDL",
        before="potential decline",
        after="pass",
    )
    db.add(c)
    db.commit()
    url = f"/api/v1/rule-confirmations/{c.id}/confirm"
    assert client.post(url, headers=as_person(client, "underwriter")).status_code == 403
    assert client.post(url, headers=as_person(client, "senior_underwriter")).status_code == 200


def test_a_request_for_senior_review_shows_until_a_decision_is_made(
    client: TestClient, sid: str
) -> None:
    r = client.post(
        f"/api/v1/submissions/{sid}/senior-review",
        json={"action": "quote"},
        headers=as_person(client, "underwriter"),
    )
    assert r.status_code == 200
    review = r.json()["senior_review"]
    assert review["by"] == "Priya Shah" and review["action"] == "quote"
    senior = as_person(client, "senior_underwriter")
    assert client.get(f"/api/v1/submissions/{sid}", headers=senior).json()["submission"][
        "senior_review"
    ]
    assert decide(client, sid, "senior_underwriter", "quote") == 200
    after = client.get(f"/api/v1/submissions/{sid}", headers=senior).json()
    assert after["submission"]["senior_review"] is None


def test_a_viewer_cannot_ask_for_review(client: TestClient, sid: str) -> None:
    r = client.post(
        f"/api/v1/submissions/{sid}/senior-review",
        json={"action": "quote"},
        headers=as_person(client, "viewer"),
    )
    assert r.status_code == 403
