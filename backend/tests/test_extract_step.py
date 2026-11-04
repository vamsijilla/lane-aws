import types
from collections.abc import Iterator
from typing import Any, Union, get_args, get_origin

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.api.main import create_app
from lane.audit import chain
from lane.config import get_settings
from lane.db.models import Document, DocumentExtraction, EscalationApproval, Fact
from lane.extract import schemas as s
from lane.extract.service import approve_escalations
from lane.llm.gateway import LlmRequest
from lane.pipeline.registry import PIPELINE
from lane.pipeline.runner import process_submission
from lane.storage.blobs import BlobStore
from tests.docs_factory import image_pdf, text_image, text_pdf, workbook
from tests.factories import make_email
from tests.llm_fakes import FakeTransport, factory, verdict
from tests.test_intake import ingest

LOSS_LINES = [
    "Policy Number: POL-9",
    "Policy Period: 01/01/2025 to 01/01/2026",
    "TOTAL LOSSES PAID $10.00 TOTAL INCURRED $10.00",
    "Claim Number: C1 Claim Status: Closed Date of Loss: 03/09/2025",
    "CLAIM TOTAL: $10.00",
]


@pytest.fixture(autouse=True)
def extraction_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests describe extraction v1 (EXTRACT_V2=0): its grounding ladder and escalation by
    approval. v2 is covered by test_catalog, test_verify, test_tidy and test_two_reads."""
    monkeypatch.setenv("EXTRACT_V2", "0")
    get_settings.cache_clear()


def blank(model: type[BaseModel]) -> dict[str, Any]:
    """A valid 'nothing found' answer for any extraction schema."""
    out: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        ann = field.annotation
        if ann is s.Val:
            out[name] = {"value": None, "quote": None, "page": None}
        elif get_origin(ann) is list:
            out[name] = []
        elif isinstance(ann, type) and issubclass(ann, BaseModel):
            out[name] = blank(ann)
        elif get_origin(ann) in (Union, types.UnionType) and type(None) in get_args(ann):
            out[name] = None
        else:
            out[name] = ""
    return out


def val(value: str, quote: str, page: int = 1) -> dict[str, Any]:
    return {"value": value, "quote": quote, "page": page}


def model_answers(request: LlmRequest[Any], text: str) -> dict[str, Any]:
    if request.purpose == "classify_page":
        if "worksheet" in text:
            return verdict("driver_schedule")
        if "stored: scan" in text:
            return verdict("ifta_return", subject_id="A-1", period="Q1", document_date="2026-03-31")
        return verdict("loss_run", subject_id="POL-9", document_date="2026-10-06")

    if request.purpose == "extract_driver_schedule":
        table = blank(s.DriverColumns) | {
            "sheet": "Drivers",
            "header_row": 1,
            "first_data_row": 2,
            "full_name": "A",
            "date_of_birth": "B",
            "license_number": "C",
            "experience": "D",
        }
        return {"table": table}

    if request.purpose == "extract_loss_run":
        out = blank(s.LossRunExtraction)
        out["policy_number"] = val("POL-9", "Policy Number: POL-9")
        out["period_start"] = val("01/01/2025", "Policy Period: 01/01/2025 to 01/01/2026")
        out["coverage_totals"] = [
            {
                "coverage": "Auto Liability",
                "losses_paid": "$10.00",
                "incurred": "$10.00",
                "page": 1,
                "quote": "TOTAL LOSSES PAID $10.00",
            }
        ]
        claim = blank(s.Claim) | {
            "claim_number": "C1",
            "status": "Closed",
            "date_of_loss": "03/09/2025",
            "total_paid": "$10.00",
            "total_incurred": "$10.00",
            "page": 1,
            "quote": "Claim Number: C1 Claim Status: Closed",
        }
        out["claims"] = [claim]
        return out

    if request.purpose == "extract_ifta_return":
        strong = request.model == get_settings().llm_escalation_model
        out = blank(s.IftaExtraction)
        out["account_number"] = val("A-1", "Account Number: A-1")
        out["rows"] = [
            blank(s.IftaRow)
            | {"jurisdiction": "Nevada", "total_miles": "1,000" if strong else "1,900", "page": 1},
        ]
        out["totals"] = blank(s.IftaTotals) | {"total_miles": "1,000", "page": 1}
        return out
    if request.purpose == "extract_email_body":
        return blank(s.EmailExtraction)
    raise AssertionError(f"unexpected request {request.purpose}")


def run(db: Session, store: BlobStore, sub_id: str, transport: FakeTransport) -> Any:
    return process_submission(
        db,
        store,
        get_settings(),
        sub_id,
        steps=PIPELINE,
        trigger="test",
        llm_factory=factory(transport),
    )


@pytest.fixture
def submission(db: Session, store: BlobStore) -> str:
    raw = make_email(
        [
            ("drivers.xlsx", workbook()),
            ("loss run.pdf", text_pdf(lines=LOSS_LINES)),
            ("Q1.pdf", image_pdf(text_image())),
        ]
    )
    return ingest(db, store, raw).submission_id


def extraction(db: Session, sub_id: str, doc_type: str) -> DocumentExtraction:
    doc = db.scalars(
        select(Document).where(Document.submission_id == sub_id, Document.doc_type == doc_type)
    ).one()
    found = db.get(DocumentExtraction, doc.id)
    assert found is not None
    return found


def facts(db: Session, sub_id: str, doc_type: str) -> dict[str, Fact]:
    doc_id = extraction(db, sub_id, doc_type).document_id
    return {f.path: f for f in db.scalars(select(Fact).where(Fact.document_id == doc_id))}


def test_extraction_grounds_facts_and_waits_for_approval(
    db: Session, store: BlobStore, submission: str
) -> None:
    transport = FakeTransport(model_answers)
    result = run(db, store, submission, transport)
    step = next(st for st in result.steps if st.name == "extract")

    # Worksheet values come from cells, not from the model.
    sheet = facts(db, submission, "driver_schedule")
    assert sheet["rows[0].full_name"].value_raw == "Pat Example"
    assert sheet["rows[0].full_name"].cell_ref == "'Drivers'!A2"
    assert sheet["rows[0].date_of_birth"].value_norm == "1980-01-02"
    assert sheet["rows[0].experience"].value_norm == "6"
    assert all(f.confidence.value == "high" and f.grounding == "cell" for f in sheet.values())

    # Text PDF values are found on the page with a box.
    loss = facts(db, submission, "loss_run")
    policy = loss["policy_number"]
    assert policy.grounding == "exact" and policy.bbox and policy.confidence.value == "high"
    assert loss["claims[0].total_paid"].value_norm == "10.00"
    assert loss["claims[0].date_of_loss"].value_norm == "2025-03-09"
    assert extraction(db, submission, "loss_run").status.value == "ok"

    # The scan's rows do not add up on the cheap model: nothing is re-read without approval.
    ifta = extraction(db, submission, "ifta_return")
    assert ifta.status.value == "needs_escalation"
    assert ifta.model == "claude-haiku-5-5"
    assert ifta.escalation_cost_usd and ifta.escalation_cost_usd > 0
    assert "Rows add up to 1,900; printed total is 1,000" in ifta.escalation_reasons
    assert facts(db, submission, "ifta_return")["rows[0].total_miles"].confidence.value == "low"
    assert step.status.value == "degraded"
    assert step.summary["pending_escalations"][0]["document_id"] == ifta.document_id
    assert not any(
        r.model == get_settings().llm_escalation_model and r.purpose.startswith("extract")
        for r, _, _ in transport.calls
    )

    # Approval, then a rerun: only the approved document goes to the stronger model.
    calls_before = len(transport.calls)
    assert approve_escalations(db, submission, actor="user:test") == [ifta.document_id]
    rerun = run(db, store, submission, transport)
    new_calls = [c for c in transport.calls[calls_before:] if c[0].purpose.startswith("extract")]
    assert [(r.purpose, r.model) for r, _, _ in new_calls] == [
        ("extract_ifta_return", get_settings().llm_escalation_model)
    ]
    ifta = extraction(db, submission, "ifta_return")
    assert ifta.status.value == "ok" and ifta.model == get_settings().llm_escalation_model
    assert next(st for st in rerun.steps if st.name == "extract").status.value == "succeeded"
    assert db.get(EscalationApproval, ifta.document_id) is not None
    assert chain.verify(db).ok


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def test_extraction_api(client: TestClient, db: Session, store: BlobStore, submission: str) -> None:
    run(db, store, submission, FakeTransport(model_answers))
    items = client.get(f"/api/v1/submissions/{submission}/extractions").json()
    by_type = {i["doc_type"]: i for i in items}
    assert by_type["loss_run"]["status"] == "ok" and by_type["loss_run"]["facts"] > 0
    assert by_type["email_body"]["status"] == "ok"

    doc_id = by_type["loss_run"]["document_id"]
    facts_out = client.get(f"/api/v1/documents/{doc_id}/facts").json()
    policy = next(f for f in facts_out if f["path"] == "policy_number")
    assert policy["grounding"] == "exact" and len(policy["bbox"]) == 4

    pending = client.get(f"/api/v1/submissions/{submission}/escalations").json()
    assert pending["model"] == get_settings().llm_escalation_model
    assert [p["title"] for p in pending["pending"]] and pending["estimated_cost_usd"] > 0
    assert client.get("/api/v1/documents/nope/facts").status_code == 404


def test_resolution_api(client: TestClient, db: Session, store: BlobStore, submission: str) -> None:
    run(db, store, submission, FakeTransport(model_answers))
    drivers = client.get(
        f"/api/v1/submissions/{submission}/entities", params={"kind": "driver"}
    ).json()
    assert [d["label"] for d in drivers] == ["Pat Example"]
    name = next(f for f in drivers[0]["fields"] if f["attribute"] == "name")
    assert name["state"] == "known" and name["candidates"][0]["sources"][0]["fact_ids"]
    missing = client.get(f"/api/v1/submissions/{submission}/missing").json()
    codes = {m["code"] for m in missing}
    assert "DRV_MVR:lic:X12•••567" in codes  # no MVR for the scheduled driver; number masked
    assert "IFTA_QUARTERS" in codes  # one quarter sent, four required
    assert isinstance(client.get(f"/api/v1/submissions/{submission}/conflicts").json(), list)
    assert client.get("/api/v1/submissions/nope/conflicts").status_code == 404
