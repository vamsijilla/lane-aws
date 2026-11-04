"""Analyst, agent request, email draft and eval scoring on a small hand-built submission.

No model is ever called: the analyst and the drafter see a scripted transport.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.actions.service import build_actions
from lane.analyze.context import build_brief
from lane.analyze.engine import AnalystOutput, analyze, check, normalize_ref
from lane.analyze.service import analyze_submission, review_finding
from lane.api.main import create_app
from lane.config import get_settings
from lane.db.models import (
    AgentRequestItem,
    AnalysisRecord,
    AuditEvent,
    FindingRecord,
    Submission,
)
from lane.evals.gold import Gold
from lane.evals.score import find_submission, markdown, norm, score, write
from lane.llm.gateway import LlmGateway, LlmRequest
from lane.storage.blobs import BlobStore
from tests.llm_fakes import FakeTransport, factory
from tests.seed import seed_submission


@pytest.fixture(autouse=True)
def analyst_runs_without_asking(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests are about the analysis itself; the spend gate is tested in test_review."""
    monkeypatch.setenv("LANE_ANALYZE_AUTO_BUDGET_USD", "100")
    get_settings.cache_clear()


@pytest.fixture
def sid(db: Session, store: BlobStore) -> str:
    return seed_submission(db, store)


def finding(title: str, cites: list[str], **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "title": title,
        "body": f"{title} body.",
        "kind": "observation",
        "severity": "medium",
        "confidence": "high",
        "basis": "The cited facts.",
        "citations": cites,
    }
    return base | kw


def answer(
    findings: list[dict[str, Any]], sentences: int = 5, cite: str = "R:DRV-01"
) -> dict[str, Any]:
    return {
        "summary": [{"text": f"Sentence {i}.", "citations": [cite]} for i in range(sentences)],
        "findings": findings,
        "security_notes": [],
    }


# --- the brief -----------------------------------------------------------------------------


def test_brief_minimizes_identifiers_and_computes_figures(db: Session, sid: str) -> None:
    brief = build_brief(db, sid, get_settings())
    text = brief.text()
    for raw in ("D1234567", "1980-05-01", "12 Elm Street", "JANE ROE", "pat@agency.example"):
        assert raw not in text, raw
    assert "LICENSE_NUMBER_1" in text and "age 46" in text and "CLAIMANT_1" in text
    assert brief.tokens["LICENSE_NUMBER_1"] == "D1234567"
    figures = {f["label"]: f["value"] for f in brief.data["figures"]}
    assert figures["Claim CL1 reported after loss"] == "28 days"
    assert figures["Annual miles per power unit at 2 units"] == "100,000"
    assert figures["Annual miles per power unit at 3 units"] == "66,667"
    assert figures["Highest declared load value vs requested cargo limit"] == "$125,000 vs $100,000"
    assert "-33% vs prior quarter" in figures["IFTA 2026-Q2"]
    rule = brief.refs["R:DRV-01"]
    assert rule.kind == "rule" and rule.cites  # links to the fields it read
    field_ref = next(r for r in brief.refs.values() if r.kind == "field")
    assert field_ref.fact_ids == ["fact-1"] and field_ref.document_ids == ["doc-app"]


# --- citation checks -----------------------------------------------------------------------


def test_check_rejects_uncited_and_deciding_findings(db: Session, sid: str) -> None:
    refs = build_brief(db, sid, get_settings()).refs
    out = AnalystOutput.model_validate(
        answer(
            [
                finding("Grounded", ["X1", "Z9"]),
                finding("Invented", ["F999"]),
                finding("Decides", ["R:DRV-01"], body="We should decline this risk now."),
                finding("Guess", ["E1"], kind="hypothesis", basis=" "),
                finding("Low", ["drv-01"], severity="low"),
                finding("High", ["C1"], severity="high"),
            ],
            sentences=3,
        )
    )
    c = check(out, refs, get_settings())
    assert [f.title for f in c.findings] == ["High", "Grounded", "Low"]  # sorted by severity
    assert c.findings[1].dropped_citations == ["Z9"]
    assert c.findings[2].citations == ["R:DRV-01"]  # bare rule ids are normalized
    assert {r["title"]: r["reason"] for r in c.rejected} == {
        "Invented": "no valid citation (F999 not in the brief)",
        "Decides": "tells the underwriter to approve, bind or decline",
        "Guess": "hypothesis without a basis",
    }
    assert any("3 sentences" in p for p in c.problems)


def test_normalize_ref() -> None:
    assert normalize_ref(" [f12] ") == "F12"
    assert normalize_ref("DRV-01") == "R:DRV-01"
    assert normalize_ref("r:drv-01") == "R:DRV-01"


def test_analyst_retries_once_with_the_problems(db: Session, sid: str) -> None:
    replies = [
        answer([finding("Invented", ["F999"]), finding("Good", ["C1"])]),
        answer([finding("Good", ["C1"]), finding("Fixed", ["R:DRV-01", "X1"])]),
    ]
    seen: list[str] = []

    def respond(req: LlmRequest[Any], text: str) -> dict[str, Any]:
        seen.append(text)
        return replies[len(seen) - 1]

    transport = FakeTransport(respond)
    brief = build_brief(db, sid, get_settings())
    result = analyze(db, sid, brief, get_settings(), factory(transport), run_id=None)
    assert result.status == "ok" and result.attempts == 2 and not result.error
    assert "F999" in seen[1] and "not in the brief" in seen[1]
    assert [f.title for f in result.findings] == ["Fixed", "Good"]  # same severity: by title
    assert transport.calls[0][0].model == get_settings().llm_analyze_model
    # One footnote per distinct citation, numbered in reading order.
    assert result.summary[0]["footnotes"] == [1]
    assert result.footnotes[0]["ref"] == "R:DRV-01"


def test_analyst_unavailable_is_degraded_not_failed(db: Session, sid: str) -> None:
    sub = db.get(Submission, sid)
    assert sub is not None
    result = analyze_submission(db, sub, get_settings(), LlmGateway, run_id=None)
    assert result.status == "degraded" and "AI analysis unavailable" in (result.error or "")
    record = db.get(AnalysisRecord, sid)
    assert record is not None and record.status == "degraded"


def test_findings_persist_reviews_survive_reruns_and_injections_are_logged(
    db: Session, sid: str
) -> None:
    reply = answer(
        [finding("Open claim", ["C1"]), finding("Ask for ELD data", ["X1"], kind="recommendation")]
    )
    reply["security_notes"] = [{"text": "ignore prior instructions", "where": "E3"}]
    fake = factory(FakeTransport(lambda req, text: reply))
    sub = db.get(Submission, sid)
    assert sub is not None
    analyze_submission(db, sub, get_settings(), fake, run_id=None)
    rows = list(db.scalars(select(FindingRecord).order_by(FindingRecord.ordinal)))
    assert [r.title for r in rows] == ["Open claim", "Ask for ELD data"]
    with pytest.raises(ValueError, match="needs a reason"):
        review_finding(db, rows[0], state="dismissed", reason=" ", actor="user:test")
    review_finding(db, rows[1], state="accepted", reason=None, actor="user:test")

    analyze_submission(db, sub, get_settings(), fake, run_id=None)  # cached rerun
    again = db.get(FindingRecord, rows[1].id)
    assert again is not None and again.review_state == "accepted"
    actions = [e.action for e in db.scalars(select(AuditEvent))]
    assert "security.injection_detected" in actions and "finding.accepted" in actions

    # An accepted recommendation joins the agent request.
    build_actions(db, sub, get_settings(), LlmGateway, run_id=None)
    texts = [i.text for i in db.scalars(select(AgentRequestItem))]
    assert "Ask for ELD data" in texts


# --- agent request and draft ---------------------------------------------------------------


def test_request_items_are_grouped_and_deduplicated(db: Session, sid: str) -> None:
    sub = db.get(Submission, sid)
    assert sub is not None
    result = build_actions(db, sub, get_settings(), LlmGateway, run_id=None)
    by_text = {i.text: i for i in result.items}
    groups = [i.group for i in result.items]
    assert groups == sorted(groups, key=["before_quote", "before_binding", "inform"].index)
    sign = by_text["Sign the application"]  # needed to bind: asked with the binding items
    assert sign.group == "before_binding" and sign.details == ["The application is not signed."]
    assert all("No FEIN." not in i.details for i in result.items)  # covered by the FEIN request
    conflicts = next(i for i in result.items if i.source_refs == ["R:DATA-01"])
    assert conflicts.details == ["How many power units do you operate? The documents show 2 and 3."]
    pd = next(i for i in result.items if i.group == "inform")
    assert pd.details == []  # request_details: false
    assert by_text["Loss runs valued within the last 60 days"].group == "before_binding"

    draft = result.draft
    assert draft.method == "template" and draft.to == "pat@agency.example"
    assert draft.body.startswith("Hi Pat,") and "Acme Hauling Inc." in draft.body
    assert "   - Lee Park holds a class C license" in draft.body
    assert "R:" not in draft.body and "DRV-01" not in draft.body


def test_draft_is_rebuilt_only_when_items_change(db: Session, sid: str) -> None:
    sub = db.get(Submission, sid)
    assert sub is not None
    first = build_actions(db, sub, get_settings(), LlmGateway, run_id=None)
    again = build_actions(db, sub, get_settings(), LlmGateway, run_id=None)
    assert not again.redrafted and again.draft.version == first.draft.version
    item = db.scalars(select(AgentRequestItem).order_by(AgentRequestItem.ordinal)).first()
    assert item is not None
    item.included = False
    db.commit()
    third = build_actions(db, sub, get_settings(), LlmGateway, run_id=None)
    assert third.redrafted and third.draft.version == first.draft.version + 1
    assert item.text not in third.draft.body
    reloaded = db.get(AgentRequestItem, item.id)
    assert reloaded is not None and not reloaded.included  # a person's choice survives rebuilds


def test_model_draft_is_checked_and_details_never_reach_the_model(db: Session, sid: str) -> None:
    def respond(req: LlmRequest[Any], text: str) -> dict[str, Any]:
        assert "Lee Park holds" not in text  # details stay in code
        ids = [line.split(".")[0] for line in text.splitlines() if line[:1].isdigit()]
        items = [{"id": n, "text": f"Please send item {n}."} for n in ids]
        items[0]["text"] = "Per rule APP-01, send the FEIN."
        return {
            "subject": "Acme Hauling: a few items",
            "opening": "Thanks for this.",
            "items": items[:-1],
            "closing": "We will be in touch.",
        }

    sub = db.get(Submission, sid)
    assert sub is not None
    result = build_actions(db, sub, get_settings(), factory(FakeTransport(respond)), run_id=None)
    d = result.draft
    # the reference token lets the agent's reply find this submission again
    assert (
        d.method == "model" and d.subject == f"Acme Hauling: a few items [Ref LN-{sid[:8].upper()}]"
    )
    assert "APP-01" not in d.body and "Please send item 2." in d.body
    assert any("internal terms" in p for p in d.problems)
    assert any("missing from the draft" in p for p in d.problems)
    assert "   - Lee Park holds a class C license" in d.body


# --- API -----------------------------------------------------------------------------------


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def test_analysis_and_actions_endpoints(client: TestClient, db: Session, sid: str) -> None:
    assert client.get(f"/api/v1/submissions/{sid}/analysis").json()["status"] == "not_run"
    reply = answer([finding("Open claim", ["C1"])])
    sub = db.get(Submission, sid)
    assert sub is not None
    analyze_submission(
        db, sub, get_settings(), factory(FakeTransport(lambda r, t: reply)), run_id=None
    )
    body = client.get(f"/api/v1/submissions/{sid}/analysis").json()
    assert body["status"] == "ok" and body["findings"][0]["citations"] == ["C1"]
    assert body["refs"]["C1"]["kind"] == "conflict"
    fid = body["findings"][0]["id"]
    bad = client.post(f"/api/v1/findings/{fid}/review", json={"state": "dismissed"})
    assert bad.status_code == 422
    ok = client.post(
        f"/api/v1/findings/{fid}/review", json={"state": "dismissed", "reason": "Known"}
    )
    assert ok.json()["review_state"] == "dismissed"

    client.post(f"/api/v1/submissions/{sid}/actions/redraft")
    actions = client.get(f"/api/v1/submissions/{sid}/actions").json()
    assert actions["draft"]["current"] and actions["draft"]["sent"] is False
    item = actions["items"][0]
    assert (
        client.patch(f"/api/v1/request-items/{item['id']}", json={"included": False}).json()[
            "included"
        ]
        is False
    )
    assert client.get(f"/api/v1/submissions/{sid}/actions").json()["draft"]["current"] is False
    paths = {getattr(r, "path", "") for r in client.app.routes}  # type: ignore[attr-defined]
    assert not any("send" in p for p in paths)


def test_degraded_analysis_message(client: TestClient, db: Session, sid: str) -> None:
    sub = db.get(Submission, sid)
    assert sub is not None
    analyze_submission(db, sub, get_settings(), LlmGateway, run_id=None)
    body = client.get(f"/api/v1/submissions/{sid}/analysis").json()
    assert body["status"] == "degraded"
    assert (
        body["message"]
        == "AI analysis unavailable for this run. Rules and extracted data are complete."
    )


# --- eval scoring --------------------------------------------------------------------------


def test_norm_compares_by_meaning() -> None:
    assert norm("$70,005.00") == norm(70005) == "70005"
    assert norm(True) == "true" and norm("Lathrop.") == "lathrop"


def test_score_against_a_small_gold_set(db: Session, sid: str, tmp_path: Path) -> None:
    gold = Gold.model_validate(
        {
            "name": "tiny",
            "email_subject_contains": "Acme",
            "documents": [
                {"file": "Email body", "types": ["email_body"]},
                {"file": "pack.pdf", "types": ["mvr", "mvr"]},
            ],
            "supersessions": [
                {"file": "pack.pdf", "type": "mvr", "subject_contains": "Lee", "superseded": 1}
            ],
            "conflicts_also_valid": ["Years"],
            "fields": [
                {"entity": "applicant", "attribute": "legal_name", "values": ["ACME HAULING INC."]},
                {"entity": "claim", "match": "CL1", "attribute": "incurred", "values": ["$40,000"]},
                {"entity": "fleet", "attribute": "power_units", "state": "conflict"},
                {"entity": "driver", "attribute": "hire_date", "values": ["2024-01-01"]},
            ],
            "conflicts": [
                {"id": "C1", "label": "Power units"},
                {"id": "C2", "label": "Owner drives"},
            ],
            "missing": [{"id": "M1", "codes": ["APP_FEIN"]}, {"id": "M2", "rules": ["DRV-01"]}],
            "rules": {"APP-01": ["refer"], "APP-02": ["refer"]},
            "route": {
                "outcome": "refer",
                "team": "small_fleet",
                "potential_decline": True,
                "potential_decline_rule": "DRV-01",
            },
            "findings": [{"id": "F1", "topic": "claim", "all": [["claim"]]}],
        }
    )
    report = score(db, gold, sid)
    m = {x.name: x for x in report.metrics}
    assert (m["Field accuracy"].correct, m["Field accuracy"].total) == (3, 4)
    assert m["Field accuracy"].misses == ["driver.hire_date: not found"]
    assert m["Conflict recall"].correct == 1 and m["Conflict precision"].rate == 1.0
    assert m["Missing-information recall"].correct == 2
    assert m["Rule outcomes"].misses == ["APP-02: got pass, expected refer"]
    assert m["Route and recommendation"].correct == 4
    assert m["Expert finding coverage"].misses == ["analysis not run"]

    assert (
        m["Document classification"].correct == 0
        and m["Splitting (files with several documents)"].total == 1
    )
    assert m["Supersession"].misses == ["pack.pdf Lee: 0 superseded, expected 1"]
    assert find_submission(db, gold) == sid
    text = markdown(report)
    assert "| Rule outcomes | 1 / 2 | 50% |" in text and "## Misses" in text
    path = write(report, tmp_path)
    assert path.read_text() == text and (tmp_path / "tiny.json").exists()


def test_a_cited_figure_opens_with_its_working_and_its_inputs(db: Session, sid: str) -> None:
    brief = build_brief(db, sid, get_settings())
    per_unit = next(
        r for r in brief.refs.values() if r.label == "Annual miles per power unit at 2 units"
    )
    reply = answer([finding("High mileage", [per_unit.id])], cite=per_unit.id)
    sub = db.get(Submission, sid)
    assert sub is not None
    fake = factory(FakeTransport(lambda r, t: reply))
    analyze_submission(db, sub, get_settings(), fake, run_id=None)
    record = db.get(AnalysisRecord, sid)
    assert record is not None
    note = record.footnotes[0]
    assert note["value"] == "100,000 = 200,000 annualized miles divided by 2 units"
    # What the figure was worked out from is kept, so each input opens too
    assert note["cites"] and set(note["cites"]) <= set(record.refs)
    # The brief the review reads is unchanged by the working
    assert {f["value"] for f in brief.data["figures"]} >= {"100,000"}
