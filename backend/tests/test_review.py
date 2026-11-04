"""People settle conflicts and approve model spend; the system reruns and records who did what."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.actions.service import build_actions
from lane.analyze.service import analyze_submission
from lane.api.analysis import analysis_message
from lane.api.display import model_label
from lane.api.main import create_app
from lane.config import get_settings
from lane.db.models import (
    AnalysisRecord,
    AuditEvent,
    Conflict,
    FieldResolution,
    ResolvedField,
    RuleOutcomeRecord,
    Submission,
)
from lane.llm.gateway import LlmGateway, LlmRequest
from lane.resolve.board import Candidate, Field, Source
from lane.resolve.compare import compare
from lane.resolve.service import _apply_resolution  # pyright: ignore[reportPrivateUsage]
from lane.review.service import OverrideError, Rerun, override_field, resolve_conflict
from lane.rules.service import decide_submission
from lane.storage.blobs import BlobStore
from tests.llm_fakes import FakeTransport, factory
from tests.seed import seed_submission


@pytest.fixture
def sid(db: Session, store: BlobStore) -> str:
    return seed_submission(db, store)


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def conflicted(*values: str) -> Field:
    src = Source("d", "application", "Application")
    f = Field("owner_drives", "Owner drives", "text", state="conflict")
    for v in values:
        compared = compare("text", v)
        assert compared is not None
        f.candidates.append(Candidate(compared, [src]))
    return f


def resolution(display: str) -> FieldResolution:
    return FieldResolution(
        field_id="f",
        submission_id="s",
        display=display,
        value=display,
        reason="Agent confirmed",
        actor="user:underwriter",
    )


def test_a_resolution_settles_the_field_with_its_reason() -> None:
    f = conflicted("No", "Yes")
    assert _apply_resolution(f, resolution("No")) is not None
    assert f.state == "known" and f.resolution == "human"
    assert f.notes[-1] == "Resolved by underwriter: Agent confirmed"


def test_a_stale_resolution_leaves_the_conflict_open() -> None:
    f = conflicted("No", "Yes")
    assert _apply_resolution(f, resolution("Maybe")) is None
    assert f.state == "conflict" and "no longer matches" in f.notes[-1]
    known = conflicted("No")
    known.state = "known"
    assert _apply_resolution(known, resolution("No")) is None


def test_settled_conflicts_stop_counting(db: Session, sid: str) -> None:
    sub = db.get(Submission, sid)
    assert sub is not None
    c = db.scalars(select(Conflict)).one()
    decide_submission(db, sub, run_id=None)
    data01 = db.scalars(
        select(RuleOutcomeRecord).where(RuleOutcomeRecord.rule_id == "DATA-01")
    ).one()
    assert data01.outcome == "refer"

    c.resolution = {
        "display": "2",
        "reason": "Schedule is current",
        "actor": "user:underwriter",
        "at": "now",
    }
    db.commit()
    decide_submission(db, sub, run_id=None)
    data01 = db.scalars(
        select(RuleOutcomeRecord).where(RuleOutcomeRecord.rule_id == "DATA-01")
    ).one()
    assert data01.outcome == "pass"
    result = build_actions(db, sub, get_settings(), LlmGateway, run_id=None)
    assert all("Power units" not in d for item in result.items for d in item.details)


def test_resolve_and_reopen_endpoints(client: TestClient, db: Session, sid: str) -> None:
    c = db.scalars(select(Conflict)).one()
    url = f"/api/v1/conflicts/{c.id}/resolve"
    assert (
        client.post(
            "/api/v1/conflicts/nope/resolve", json={"candidate": 0, "reason": "x"}
        ).status_code
        == 404
    )
    assert client.post(url, json={"candidate": 0, "reason": "  "}).status_code == 422
    assert client.post(url, json={"candidate": 9, "reason": "x"}).status_code == 422
    ops = client.post("/api/v1/auth/demo-login", json={"role": "ops"}).json()["token"]
    assert (
        client.post(
            url, json={"candidate": 0, "reason": "x"}, headers={"Authorization": f"Bearer {ops}"}
        ).status_code
        == 403
    )

    body = client.post(url, json={"candidate": 0, "reason": "Schedule is current"}).json()
    assert body["run_id"] and "changes" in body
    saved = db.get(FieldResolution, c.field_id)
    assert saved is not None and saved.display == "2" and saved.actor == "user:underwriter"
    assert "conflict.resolved" in [e.action for e in db.scalars(select(AuditEvent))]


def test_reopen_endpoint(client: TestClient, db: Session, sid: str) -> None:
    c = db.scalars(select(Conflict)).one()
    cid, fid = c.id, c.field_id
    resolve_conflict(db, c, candidate=1, reason="Quote form is newer", actor="user:underwriter")
    assert client.delete(f"/api/v1/conflicts/{cid}/resolution").status_code == 200
    db.expire_all()
    assert db.get(FieldResolution, fid) is None
    assert "conflict.reopened" in [e.action for e in db.scalars(select(AuditEvent))]
    assert client.delete("/api/v1/conflicts/nope/resolution").status_code == 404


# --- corrections -------------------------------------------------------------------------


def override(display: str) -> FieldResolution:
    r = resolution(display)
    r.kind = "override"
    return r


def test_a_correction_stands_whatever_the_documents_say() -> None:
    for f in (conflicted("No", "Yes"), conflicted("Yes")):
        if len(f.candidates) == 1:
            f.state = "known"
        assert _apply_resolution(f, override("Maybe")) is not None
        assert f.state == "known" and f.resolution == "override"
        assert f.notes[-1] == "Corrected by underwriter: Agent confirmed"
    missing = Field("fein", "FEIN", "id", state="missing")
    assert _apply_resolution(missing, override("39-4471820")) is not None
    assert missing.state == "known"


@pytest.mark.parametrize(
    ("kind", "typed", "display", "value"),
    [
        ("date", "12/15/2026", "Dec 15, 2026", "2026-12-15"),
        ("money", "$100,000", "$100,000", "100000"),
        ("number", "5", "5", "5"),
        ("bool", "no", "No", False),
        ("state", "ca", "CA", "CA"),
        ("id", "39-4471820", "39-4471820", "394471820"),
    ],
)
def test_a_correction_is_read_as_the_fields_kind(
    db: Session, sid: str, kind: str, typed: str, display: str, value: Any
) -> None:
    f = db.scalars(select(ResolvedField).where(ResolvedField.state == "known")).first()
    assert f is not None
    f.kind = kind
    out = override_field(db, f, value=typed, reason="Agent confirmed", actor="user:underwriter")
    saved = db.get(FieldResolution, f.id)
    assert saved is not None and saved.kind == "override"
    assert out["display"] == saved.display == display
    assert saved.value == value


def test_a_correction_needs_a_readable_value_and_a_reason(db: Session, sid: str) -> None:
    f = db.scalars(select(ResolvedField).where(ResolvedField.state == "known")).first()
    assert f is not None
    f.kind = "date"
    with pytest.raises(OverrideError, match="a date such as"):
        override_field(db, f, value="next spring", reason="x", actor="user:u")
    with pytest.raises(OverrideError, match="reason"):
        override_field(db, f, value="2026-12-15", reason=" ", actor="user:u")


def test_override_endpoints(
    client: TestClient, db: Session, sid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The seed has no documents to resolve from, so a real rerun would drop its fields; the
    # rerun itself is covered by the resolve tests.
    monkeypatch.setattr(
        "lane.api.resolution.rerun", lambda *a, **k: Rerun("run-x", "h", "h", False, False, [])
    )
    f = db.scalars(select(ResolvedField).where(ResolvedField.state == "known")).first()
    assert f is not None
    fid, before = f.id, f.value_display
    url = f"/api/v1/fields/{fid}/override"
    assert client.post(url, json={"value": "", "reason": "x"}).status_code == 422
    viewer = client.post("/api/v1/auth/demo-login", json={"role": "viewer"}).json()["token"]
    headers = {"Authorization": f"Bearer {viewer}"}
    assert client.post(url, json={"value": "x", "reason": "x"}, headers=headers).status_code == 403

    body = client.post(url, json={"value": "Acme Freight", "reason": "Renamed"}).json()
    assert body["run_id"] == "run-x"
    db.expire_all()
    saved = db.get(FieldResolution, fid)
    assert saved is not None and saved.before == before and saved.reason == "Renamed"
    detail = client.get(f"/api/v1/fields/{fid}").json()
    assert detail["correction"]["display"] == "Acme Freight" and detail["expected"]

    assert client.delete(url).status_code == 200
    db.expire_all()
    assert db.get(FieldResolution, fid) is None
    assert client.delete(url).status_code == 404  # nothing left to clear
    assert client.delete("/api/v1/fields/nope/override").status_code == 404
    actions = [e.action for e in db.scalars(select(AuditEvent))]
    assert "field.overridden" in actions and "field.override_cleared" in actions


# --- model spend ---------------------------------------------------------------------------


def analyst_reply(req: LlmRequest[Any], text: str) -> dict[str, Any]:
    return {
        "summary": [{"text": f"Sentence {i}.", "citations": ["C1"]} for i in range(5)],
        "findings": [
            {
                "title": "Open claim",
                "body": "Body.",
                "kind": "observation",
                "severity": "high",
                "confidence": "high",
                "basis": "Facts.",
                "citations": ["C1"],
            }
        ],
        "security_notes": [],
    }


def test_the_analyst_waits_for_approval_unless_cached(db: Session, sid: str) -> None:
    sub = db.get(Submission, sid)
    assert sub is not None
    transport = FakeTransport(analyst_reply)
    first = analyze_submission(db, sub, get_settings(), factory(transport), run_id=None)
    record = db.get(AnalysisRecord, sid)
    assert first.status == "awaiting_approval" and not transport.calls
    assert record is not None and record.estimate_usd and record.estimate_usd > 0
    # The person approving sees which model they pay for
    assert record.model == get_settings().llm_analyze_model
    assert f"run on {model_label(record.model)}," in (analysis_message(record) or "")

    record.approved_at = record.created_at  # an underwriter approved
    db.commit()
    ran = analyze_submission(db, sub, get_settings(), factory(transport), run_id=None)
    assert ran.status == "ok" and len(transport.calls) == 1
    assert db.get(AnalysisRecord, sid).approved_at is None  # type: ignore[union-attr]  # one run per approval

    again = analyze_submission(db, sub, get_settings(), factory(transport), run_id=None)
    assert again.status == "ok" and len(transport.calls) == 1  # cached: free, so no approval needed


def test_a_budget_lets_cheap_runs_through(
    db: Session, sid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANE_ANALYZE_AUTO_BUDGET_USD", "5")
    get_settings.cache_clear()
    sub = db.get(Submission, sid)
    assert sub is not None
    result = analyze_submission(
        db, sub, get_settings(), factory(FakeTransport(analyst_reply)), run_id=None
    )
    assert result.status == "ok"


def test_approve_endpoint(client: TestClient, db: Session, sid: str) -> None:
    url = f"/api/v1/submissions/{sid}/analysis/approve"
    assert client.post(url).status_code == 409  # nothing is waiting
    sub = db.get(Submission, sid)
    assert sub is not None
    analyze_submission(db, sub, get_settings(), factory(FakeTransport(analyst_reply)), run_id=None)
    body = client.get(f"/api/v1/submissions/{sid}/analysis").json()
    assert body["status"] == "awaiting_approval" and "about $" in body["message"]
    admin = client.post("/api/v1/auth/demo-login", json={"role": "admin"}).json()["token"]
    assert client.post(url, headers={"Authorization": f"Bearer {admin}"}).status_code == 403

    accepted = client.post(url)
    assert accepted.status_code == 202 and accepted.json()["status"] == "running"
    # The background run has no model key in tests, so it ends degraded rather than running.
    assert client.get(f"/api/v1/submissions/{sid}/analysis").json()["status"] == "degraded"
    assert "analysis.approved" in [e.action for e in db.scalars(select(AuditEvent))]
