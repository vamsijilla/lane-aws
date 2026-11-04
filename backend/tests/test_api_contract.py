"""Every read endpoint answers in its declared shape; the committed OpenAPI schema is current."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from lane.api.main import create_app
from lane.rules.predicates import power_unit_counts
from lane.rules.view import SubmissionView
from lane.storage.blobs import BlobStore
from tests.seed import Seed, seed_submission

SCHEMA = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "api" / "openapi.json"


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def sid(db: Session, store: BlobStore) -> str:
    return seed_submission(db, store)


def test_committed_schema_matches_the_api() -> None:
    """Run `make api-types` when this fails: the frontend's types are out of date."""
    live = create_app().openapi()
    assert json.loads(SCHEMA.read_text()) == json.loads(json.dumps(live))


def test_operation_ids_are_unique_and_readable() -> None:
    spec = create_app().openapi()
    ids = [op["operationId"] for path in spec["paths"].values() for op in path.values()]
    assert len(ids) == len(set(ids))
    assert all("api_v1" not in i for i in ids)


READS = [
    "/api/v1/submissions/{sid}",
    "/api/v1/submissions/{sid}/documents",
    "/api/v1/submissions/{sid}/pages",
    "/api/v1/submissions/{sid}/runs",
    "/api/v1/submissions/{sid}/extractions",
    "/api/v1/submissions/{sid}/escalations",
    "/api/v1/submissions/{sid}/entities",
    "/api/v1/submissions/{sid}/conflicts",
    "/api/v1/submissions/{sid}/missing",
    "/api/v1/submissions/{sid}/rules",
    "/api/v1/submissions/{sid}/analysis",
    "/api/v1/submissions/{sid}/findings",
    "/api/v1/submissions/{sid}/actions",
    "/api/v1/submissions/{sid}/audit",
]


@pytest.mark.parametrize("path", READS)
def test_reads_answer_and_unknown_ids_are_404(client: TestClient, sid: str, path: str) -> None:
    ok = client.get(path.format(sid=sid))
    assert ok.status_code == 200, ok.text
    assert client.get(path.format(sid="nope")).status_code == 404


def test_overview_orders_blockers_and_links_numbers(
    client: TestClient, sid: str, db: Session
) -> None:
    body = client.get(f"/api/v1/submissions/{sid}").json()
    assert body["route"]["headline"] == "Refer to Small Fleet underwriting"
    kinds = [b["kind"] for b in body["blockers"]]
    assert kinds[0] == "potential_decline"
    order = [
        "potential_decline",
        "decline",
        "missing_document",
        "blocking_conflict",
        "refer",
        "unknown",
        "flag",
    ]
    assert kinds == sorted(kinds, key=order.index)
    numbers = {n["key"]: n for n in body["key_numbers"]}
    assert numbers["power_units"]["value"] == "2 or 3" and numbers["power_units"]["conflict"]
    assert numbers["power_units"]["field_id"]
    # The header shows the counts the route uses (ROUTE-01), never its own reading
    counts = [str(n) for n, _ in power_unit_counts(SubmissionView.load(db, sid))]
    assert numbers["power_units"]["value"] == ", ".join(counts[:-1]) + f" or {counts[-1]}"
    assert numbers["open_claims"]["value"] == "1" and numbers["open_claims"]["entity_ids"]
    assert numbers["annual_miles"]["value"] == "200,000"
    assert numbers["outside_home"]["value"] == "80%"
    assert body["summary"]["status"] == "not_run"


def test_audit_includes_chain_verification(client: TestClient, sid: str) -> None:
    body = client.get(f"/api/v1/submissions/{sid}/audit?limit=1").json()
    assert body["chain"]["verified"] is True and body["chain"]["events"] >= 1
    assert len(body["events"]) == 1


def test_evals_and_ops(client: TestClient, sid: str) -> None:
    evals = client.get("/api/v1/evals")
    assert evals.status_code == 200
    if evals.json():
        assert client.get("/api/v1/evals/latest").json()["gold"]
    assert client.get("/api/v1/evals/no-such").status_code == 404

    assert client.get("/api/v1/ops/health").status_code == 403  # underwriter by default
    token = client.post("/api/v1/auth/demo-login", json={"role": "ops"}).json()["token"]
    ops = client.get("/api/v1/ops/health", headers={"Authorization": f"Bearer {token}"}).json()
    assert ops["submissions"] >= 1 and "models" in ops


# Free-form JSON the UI may not branch on: raw views and configuration, never screen data.
FREE_FORM_ALLOWED = {
    ("AuditEventOut", "payload"),
    ("EntityOut", "details"),
    ("ExtractionOut", "data"),
    ("RulesetOut", "params"),
    ("StepOut", "summary"),
    ("ValidationError", "ctx"),
}


def test_screen_payloads_are_typed() -> None:
    """Every object in a response is a declared model, so the generated client checks it."""

    def free(s: dict[str, Any]) -> bool:
        if s.get("type") == "object" and "properties" not in s:
            extra = s.get("additionalProperties", True)
            return extra is True or (isinstance(extra, dict) and free(cast(dict[str, Any], extra)))
        if s.get("type") == "array":
            return free(cast(dict[str, Any], s.get("items", {})))
        return any(free(x) for x in cast(list[dict[str, Any]], s.get("anyOf", [])))

    schemas = cast(dict[str, Any], create_app().openapi()["components"]["schemas"])
    loose = {
        (name, prop)
        for name, s in schemas.items()
        for prop, ps in cast(dict[str, Any], s.get("properties") or {}).items()
        if free(ps)
    }
    assert loose <= FREE_FORM_ALLOWED, sorted(loose - FREE_FORM_ALLOWED)


def test_open_external_checks_ride_with_the_route(
    client: TestClient, db: Session, sid: str
) -> None:
    """A07: SAFER and premium cannot be checked at intake; the route says so by name."""
    s = Seed(db, sid)
    s.rule(7, "ELIG-06", "unknown")
    s.rule(8, "SEL-01", "unknown")
    db.commit()
    body = client.get(f"/api/v1/submissions/{sid}").json()
    assert body["route"]["checks_at_quote"] == ["SAFER record", "premium per unit"]
    checks = [b for b in body["blockers"] if b["kind"] == "check"]
    assert [b["rule_id"] for b in checks] == ["ELIG-06", "SEL-01"]
