"""A reply is measured point by point against what the agent was asked: the request a person
marked sent, else the last draft. Lane itself never sends."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.actions import points
from lane.api.main import create_app
from lane.db.base import utcnow
from lane.db.models import (
    AgentRequestItem,
    AnalysisRecord,
    AuditEvent,
    RequestSnapshot,
    Run,
    RunStatus,
)
from lane.storage.blobs import BlobStore
from tests.factories import PDF, make_email
from tests.test_intake import ingest

CONFLICTS = ["Effective date: Dec 15 or Dec 1", "Power units: 4 or 5", "Cargo: dry goods or steel"]


def row(
    id: str,
    text: str,
    details: list[str],
    keys: list[str] | None = None,
    included: bool = True,
    group: str = "before_quote",
) -> dict[str, object]:
    return {
        "id": id,
        "group": group,
        "text": text,
        "details": details,
        "keys": keys if keys is not None else [f"C:{d[:5]}" for d in details],
        "included": included,
        "source_kind": "rule",
    }


def base(rows: list[dict[str, object]]) -> tuple[str, None, None, None, list[dict[str, object]]]:
    return ("sent", None, None, None, rows)


def test_a_point_is_a_question_not_a_line_and_fyi_items_are_not_points() -> None:
    who = row(
        "d",
        "Confirm who drives",
        ["Owner holds a class C license", "Does the owner drive? The documents show No and Yes."],
        ["R:DRV-01", "R:DRV-01"],  # one question: who drives
    )
    before = [
        row("c", "Clarify the conflicting information", CONFLICTS),
        row("f", "FEIN on the signed application", [], []),
        who,
        row("h", "Helpful: years in business", [], [], included=False),
        row("s", "Sign the application", ["Not signed."], ["M:SIGN"], group="before_binding"),
        row("i", "Physical damage is not available", [], [], group="inform"),
    ]
    after = [
        row("c", "Clarify the conflicting information", [*CONFLICTS[1:], "Agency name: A or B"]),
        row("s", "Sign the application", ["Not signed."], ["M:SIGN"], group="before_binding"),
        row("i", "Physical damage is not available", [], [], group="inform"),
    ]
    d = points.diff(base(before), after).as_dict()
    assert d["answered"] == 3 and d["requested"] == 6  # the effective date, the FEIN, who drives
    quote, binding = d["groups"]
    assert (quote["group"], quote["answered"], quote["requested"]) == ("before_quote", 3, 5)
    assert (binding["group"], binding["answered"], binding["requested"]) == ("before_binding", 0, 1)
    items = {i["item"]: i for i in quote["items"]}
    assert (
        items["Confirm who drives"]["answered"],
        len(items["Confirm who drives"]["points"]),
    ) == (
        1,
        1,
    )
    assert items["Clarify the conflicting information"]["answered"] == 1
    assert d["answered_not_requested"] == [
        {"item": "Helpful: years in business", "lines": ["Helpful: years in business"]}
    ]
    assert d["new"] == [
        {"item": "Clarify the conflicting information", "lines": ["Agency name: A or B"]}
    ]


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def _auth(client: TestClient, role: str = "underwriter") -> dict[str, str]:
    token = client.post("/api/v1/auth/demo-login", json={"role": role}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _submission(db: Session, store: BlobStore) -> str:
    sid = ingest(db, store, make_email([("a.pdf", PDF)])).submission_id
    for n, (iid, text, details, kind) in enumerate(
        [
            ("i1", "Clarify the conflicting information", CONFLICTS, "rule"),
            ("i2", "Send the signed application", [], "missing"),
            ("i3", "Explain the gap in coverage", [], "finding"),
        ],
        1,
    ):
        db.add(
            AgentRequestItem(
                id=f"{iid}{sid[:20]}",
                submission_id=sid,
                ordinal=n,
                group="before_quote",
                text=text,
                details=details,
                source_kind=kind,
                source_refs=[],
                included=True,
            )
        )
    db.commit()
    return sid


def test_mark_as_sent_snapshots_the_request_and_replies_are_measured_against_it(
    client: TestClient, db: Session, store: BlobStore
) -> None:
    sid = _submission(db, store)
    assert points.baseline(db, sid)[0] == "draft"  # nothing marked sent: the last draft

    viewer = client.post(
        f"/api/v1/submissions/{sid}/actions/mark-sent", headers=_auth(client, "viewer")
    )
    assert viewer.status_code == 403
    out = client.post(f"/api/v1/submissions/{sid}/actions/mark-sent", headers=_auth(client))
    assert out.status_code == 200
    sent = out.json()["last_sent"]
    assert sent["items"] == 3 and sent["current"] and sent["sent_by"].endswith("underwriter")
    snap = db.scalars(select(RequestSnapshot)).one()
    assert snap.items[0]["text"] == "Clarify the conflicting information"
    event = next(
        e for e in db.scalars(select(AuditEvent)) if e.action == "agent_request.marked_sent"
    )
    assert event.payload["snapshot_id"] == snap.id and "does not send" in event.payload["note"]

    # The request changes after it was sent; the reply is still measured against what was sent
    item = db.get(AgentRequestItem, f"i1{sid[:20]}")
    assert item is not None
    item.details = CONFLICTS[:1]
    db.commit()
    b = points.baseline(db, sid)
    assert b[0] == "sent" and b[1] and b[1].endswith("underwriter") and b[4] == snap.items
    d = points.diff(b, points.item_rows(db, sid)).as_dict()
    assert d["answered"] == 2 and d["requested"] == 5
    again = client.get(f"/api/v1/submissions/{sid}/actions", headers=_auth(client)).json()
    assert again["last_sent"]["current"] is False


def test_recommendations_from_a_review_older_than_the_reply_are_stale(
    client: TestClient, db: Session, store: BlobStore
) -> None:
    sid = _submission(db, store)
    now = utcnow()
    db.add(AnalysisRecord(submission_id=sid, status="ok", analyzed_at=now - timedelta(hours=1)))
    db.commit()
    items = client.get(f"/api/v1/submissions/{sid}/actions", headers=_auth(client)).json()["items"]
    assert not any(i["stale"] for i in items)  # no reply yet

    db.add(Run(submission_id=sid, trigger="follow_up", status=RunStatus.SUCCEEDED, started_at=now))
    db.commit()
    items = client.get(f"/api/v1/submissions/{sid}/actions", headers=_auth(client)).json()["items"]
    assert [i["text"] for i in items if i["stale"]] == ["Explain the gap in coverage"]


def _rule(db: Session, sid: str, outcome: str, potential: bool) -> None:
    from lane.db.models import RuleOutcomeRecord

    rec = db.get(RuleOutcomeRecord, f"r{sid[:20]}") or RuleOutcomeRecord(
        id=f"r{sid[:20]}",
        submission_id=sid,
        ordinal=1,
        rule_id="DRV-01",
        title="Every driver holds a valid CDL",
        section="Drivers",
        quote="Valid CDL",
        outcome=outcome,
        reason="test",
        ruleset_version="test",
    )
    rec.outcome, rec.potential_decline = outcome, potential
    db.add(rec)
    db.commit()


def test_a_rule_cleared_by_the_agents_document_waits_for_an_underwriter(
    client: TestClient, db: Session, store: BlobStore
) -> None:
    from types import SimpleNamespace

    from lane.db.models import RuleConfirmation
    from lane.pipeline.rules_step import _confirmations  # pyright: ignore[reportPrivateUsage]

    sid = _submission(db, store)
    run = Run(submission_id=sid, trigger="follow_up", status=RunStatus.SUCCEEDED)
    db.add(run)
    db.commit()
    _rule(db, sid, "pass", False)
    ctx = SimpleNamespace(session=db, submission=SimpleNamespace(id=sid), run=run)
    change = {
        "rule_id": "DRV-01",
        "title": "Every driver holds a valid CDL",
        "before": "refer",
        "after": "pass",
        "was_potential_decline": True,
        "potential_decline": False,
    }
    assert _confirmations(ctx, [change]) == ["DRV-01"]  # pyright: ignore[reportArgumentType]
    db.commit()

    row = next(
        r for r in client.get("/api/v1/submissions", headers=_auth(client)).json() if r["id"] == sid
    )
    assert row["pending_confirmations"] == 1 and row["potential_decline"]  # until confirmed

    c = db.scalars(select(RuleConfirmation)).one()
    assert (c.before, c.after, c.status) == ("potential decline", "pass", "pending")
    viewer = client.post(
        f"/api/v1/rule-confirmations/{c.id}/confirm", headers=_auth(client, "viewer")
    )
    assert viewer.status_code == 403
    # Clearing a decline is four-eyes: an underwriter cannot, a senior underwriter can
    assert (
        client.post(f"/api/v1/rule-confirmations/{c.id}/confirm", headers=_auth(client))
    ).status_code == 403
    out = client.post(
        f"/api/v1/rule-confirmations/{c.id}/confirm", headers=_auth(client, "senior_underwriter")
    )
    assert out.status_code == 200 and out.json()["status"] == "confirmed"
    actions = {e.action for e in db.scalars(select(AuditEvent))}
    assert {"rule_change.pending", "rule_change.confirmed"} <= actions
    row = next(
        r for r in client.get("/api/v1/submissions", headers=_auth(client)).json() if r["id"] == sid
    )
    assert row["pending_confirmations"] == 0 and not row["potential_decline"]


def test_a_change_from_a_person_needs_no_confirmation_and_going_back_voids_one(
    db: Session, store: BlobStore
) -> None:
    from types import SimpleNamespace

    from lane.db.models import RuleConfirmation
    from lane.pipeline.rules_step import _confirmations  # pyright: ignore[reportPrivateUsage]

    sid = _submission(db, store)
    change = {
        "rule_id": "DRV-01",
        "title": "t",
        "before": "decline",
        "after": "pass",
        "was_potential_decline": False,
        "potential_decline": False,
    }
    rerun = Run(submission_id=sid, trigger="resolution", status=RunStatus.SUCCEEDED)
    reply = Run(submission_id=sid, trigger="follow_up", status=RunStatus.SUCCEEDED)
    db.add_all([rerun, reply])
    db.commit()
    _rule(db, sid, "pass", False)
    ctx = SimpleNamespace(session=db, submission=SimpleNamespace(id=sid), run=rerun)
    assert _confirmations(ctx, [change]) == []  # pyright: ignore[reportArgumentType]
    ctx.run = reply
    assert _confirmations(ctx, [change]) == ["DRV-01"]  # pyright: ignore[reportArgumentType]
    _rule(db, sid, "decline", False)  # a later run puts it back
    assert _confirmations(ctx, []) == []  # pyright: ignore[reportArgumentType]
    assert db.scalars(select(RuleConfirmation)).one().status == "void"


def test_owner_drives_is_settled_by_an_exclusion_of_the_owner_pending_confirmation() -> None:
    from lane.resolve.board import Board
    from lane.resolve.service import _settled_by_document  # pyright: ignore[reportPrivateUsage]

    board = Board()
    ops = board.entity("operations", "operations", "Operations")
    f = ops.field("owner_drives", "Owner drives", "bool")
    f.state = "conflict"
    assert _settled_by_document(ops, "owner_drives", f) is None  # no letter: still a conflict
    ops.details["owner_excluded"] = {"document_id": "d", "title": "Exclusion", "name": "A Owner"}
    r = _settled_by_document(ops, "owner_drives", f)
    assert r is not None and r["pending"] and r["display"] == "No"
    assert r["reason"].startswith("Settled by exclusion letter, pending confirmation")
    assert (f.state, f.resolution) == ("known", "document")


def test_the_api_counts_open_and_resolved_conflicts_apart(
    client: TestClient, db: Session, store: BlobStore
) -> None:
    from lane.db.models import Conflict, ResolvedEntity, ResolvedField

    sid = _submission(db, store)
    db.add(
        ResolvedEntity(
            id=f"e{sid[:20]}", submission_id=sid, kind="operations", key="ops", label="Ops"
        )
    )
    for n, resolution in enumerate(
        [None, {"display": "No", "reason": "r", "actor": "user:u", "at": ""}]
    ):
        fid = f"f{n}{sid[:20]}"
        db.add(
            ResolvedField(
                id=fid,
                submission_id=sid,
                entity_id=f"e{sid[:20]}",
                attribute=f"a{n}",
                label="A",
                kind="text",
                state="conflict",
                value="x",
            )
        )
        db.flush()
        db.add(
            Conflict(
                id=f"c{n}{sid[:20]}",
                submission_id=sid,
                field_id=fid,
                entity_id=f"e{sid[:20]}",
                type="value",
                title=f"Field {n}",
                resolution=resolution,
            )
        )
    db.commit()
    row = next(
        r for r in client.get("/api/v1/submissions", headers=_auth(client)).json() if r["id"] == sid
    )
    assert (row["conflicts_open"], row["conflicts_resolved"]) == (1, 1)


def test_the_underwriter_records_a_decision_with_a_reason_beside_lanes_recommendation(
    client: TestClient, db: Session, store: BlobStore
) -> None:
    sid = _submission(db, store)
    url = f"/api/v1/submissions/{sid}/decision"
    body = {"decision": "quote", "reason": "", "conditions": []}
    assert client.post(url, json=body, headers=_auth(client)).status_code == 422  # reason needed
    assert (
        client.post(url, json={**body, "reason": "x"}, headers=_auth(client, "viewer")).status_code
        == 403
    )
    out = client.post(
        url,
        json={
            "decision": "quote",
            "reason": "Owner excluded; loss runs to follow",
            "conditions": ["Named driver exclusion signed", " "],
        },
        headers=_auth(client),
    )
    assert out.status_code == 200
    row = out.json()
    assert row["decision"] == "quote" and row["decided_by"].endswith("underwriter")
    assert row["decision_conditions"] == ["Named driver exclusion signed"]
    event = next(e for e in db.scalars(select(AuditEvent)) if e.action == "decision.recorded")
    assert event.payload["reason"] == "Owner excluded; loss runs to follow"
    listed = next(
        r for r in client.get("/api/v1/submissions", headers=_auth(client)).json() if r["id"] == sid
    )
    assert listed["decision"] == "quote" and listed["decided_at"]


def test_received_time_is_the_emails_date_header_not_the_ingest_time(
    client: TestClient, db: Session, store: BlobStore
) -> None:
    sid = _submission(db, store)  # the factory's Date header: Thu, 22 Oct 2026 10:00 -0700
    row = next(
        r for r in client.get("/api/v1/submissions", headers=_auth(client)).json() if r["id"] == sid
    )
    assert row["received_at"].startswith("2026-10-22T17:00:00")
    # the ingest time stays in the audit
    assert any(e.action == "email.received" for e in db.scalars(select(AuditEvent)))


def test_after_a_reply_the_summary_and_brief_read_the_point_counts(
    client: TestClient, db: Session, store: BlobStore
) -> None:
    from lane.db.models import Step, StepStatus

    sid = _submission(db, store)
    run = Run(submission_id=sid, trigger="follow_up", status=RunStatus.SUCCEEDED)
    db.add(run)
    db.flush()
    points_summary = points.diff(
        base([row("f", "FEIN", [], []), row("c", "Clarify", CONFLICTS)]),
        [row("c", "Clarify", [*CONFLICTS, "New: A or B"])],
    ).as_dict()
    db.add(
        Step(
            run_id=run.id,
            name="actions",
            version="1",
            status=StepStatus.SUCCEEDED,
            summary={"points": points_summary},
        )
    )
    db.commit()
    rows = client.get("/api/v1/submissions", headers=_auth(client))
    assert rows.status_code == 200
    row_ = next(r for r in rows.json() if r["id"] == sid)
    assert (row_["points_answered"], row_["points_requested"], row_["points_new"]) == (1, 4, 1)
    brief = client.get(f"/api/v1/submissions/{sid}", headers=_auth(client))
    assert brief.status_code == 200 and brief.json()["amendment"]["answered"] == 1
    assert brief.json()["amendment"]["in_progress"] is False


def test_a_reply_still_being_processed_shows_no_diff_yet(
    client: TestClient, db: Session, store: BlobStore
) -> None:
    sid = _submission(db, store)
    db.add(Run(submission_id=sid, trigger="follow_up", status=RunStatus.RUNNING))
    db.commit()
    brief = client.get(f"/api/v1/submissions/{sid}", headers=_auth(client)).json()
    assert brief["amendment"]["in_progress"] is True


def test_a_make_the_vin_decode_settles_reads_the_decoded_value() -> None:
    from lane.resolve.board import Board, Source
    from lane.resolve.service import _settled_by_reference  # pyright: ignore[reportPrivateUsage]

    board = Board()
    v = board.entity("vehicle", "VIN1", "2020 Volvo")
    board.add(v, "make", "Freightliner", Source("d1", "loss_run", "Loss run"), kind="company")
    board.add(v, "make", "Volvo", Source("d2", "vin_decode", "VIN decode"), kind="company")
    f = v.fields["make"]
    f.state, f.conflict = "conflict", {"type": "reference"}
    settled = _settled_by_reference(f)
    assert settled is not None and settled.compared.display == "Volvo"
    assert (f.state, f.resolution) == ("known", "reference")


def test_after_a_reply_an_earlier_review_is_marked_and_its_asks_are_not_requested(
    client: TestClient, db: Session, store: BlobStore
) -> None:
    from lane.actions.build import build_items
    from lane.config import get_settings
    from lane.db.models import FindingRecord
    from lane.rules.service import ruleset

    sid = _submission(db, store)
    now = utcnow()
    db.add(AnalysisRecord(submission_id=sid, status="ok", analyzed_at=now - timedelta(hours=1)))
    db.add(
        FindingRecord(
            id=f"f{sid[:20]}",
            submission_id=sid,
            ordinal=1,
            title="Ask for the garaging address",
            body="b",
            kind="recommendation",
            severity="medium",
            confidence="high",
            basis="b",
            review_state="accepted",
        )
    )
    db.commit()

    def asks() -> list[str]:
        return [i.text for i in build_items(db, sid, ruleset(), get_settings())]

    assert "Ask for the garaging address" in asks()
    assert not client.get(f"/api/v1/submissions/{sid}/analysis", headers=_auth(client)).json()[
        "stale"
    ]
    db.add(Run(submission_id=sid, trigger="follow_up", status=RunStatus.SUCCEEDED, started_at=now))
    db.commit()
    assert "Ask for the garaging address" not in asks()
    assert client.get(f"/api/v1/submissions/{sid}/analysis", headers=_auth(client)).json()["stale"]
    assert client.get(f"/api/v1/submissions/{sid}", headers=_auth(client)).json()["summary"][
        "stale"
    ]


def test_an_excluded_driver_is_pending_until_a_person_confirms() -> None:
    from lane.db.models import FieldResolution
    from lane.resolve.board import Board
    from lane.resolve.service import _exclusion_status, stable_id  # pyright: ignore[reportPrivateUsage]

    board = Board()
    owner = board.entity("driver", "name:owner", "A Owner")
    owner.details["owner_marker"] = {"document_id": "d", "title": "Schedule"}
    owner.details["excluded"] = {"document_id": "x", "title": "Exclusion", "name": "A Owner"}
    _exclusion_status(board, "sub", {})
    assert owner.details["excluded"]["status"] == "pending"
    ops_field = stable_id(stable_id("sub", "operations", "operations"), "owner_drives")
    said_no = FieldResolution(
        field_id=ops_field,
        submission_id="sub",
        display="No",
        value=False,
        reason="r",
        actor="user:u",
    )
    _exclusion_status(board, "sub", {ops_field: said_no})
    assert owner.details["excluded"]["status"] == "confirmed"


def test_how_lane_works_reads_every_number_from_the_submission(
    client: TestClient, db: Session, store: BlobStore
) -> None:
    sid = _submission(db, store)
    out = client.get(f"/api/v1/submissions/{sid}/how", headers=_auth(client))
    assert out.status_code == 200
    how = out.json()
    assert [s["name"] for s in how["stages"]] == [
        "Email",
        "Files",
        "Pages",
        "Documents",
        "Facts",
        "Reconcile",
        "Rules",
        "Decision",
    ]
    assert how["stages"][1]["count"] == 1  # the one PDF this email carried
    assert all(set(s["by"]) <= {"AI", "Code"} and s["by"] for s in how["stages"])
    assert len(how["guarantees"]) == 8 and len(how["person"]) == 5
    assert client.get("/api/v1/submissions/nope/how", headers=_auth(client)).status_code == 404
