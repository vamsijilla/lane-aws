"""A small resolved submission built row by row: no documents, no model."""

from typing import Any

from sqlalchemy.orm import Session

from lane.db.models import (
    Conflict,
    DecisionRecord,
    MissingInfo,
    ResolvedEntity,
    ResolvedField,
    RuleOutcomeRecord,
)
from lane.storage.blobs import BlobStore
from tests.factories import make_email
from tests.test_intake import ingest

DOC = {
    "document_id": "doc-app",
    "doc_type": "application",
    "title": "Application",
    "fact_ids": ["fact-1"],
    "as_of": None,
    "note": None,
}


# --- a small resolved submission -----------------------------------------------------------


class Seed:
    def __init__(self, db: Session, sid: str) -> None:
        self.db = db
        self.sid = sid
        self.n = 0

    def entity(self, kind: str, key: str, label: str, details: dict[str, Any] | None = None) -> str:
        self.n += 1
        eid = f"e{self.n}"
        self.db.add(
            ResolvedEntity(
                id=eid,
                submission_id=self.sid,
                kind=kind,
                key=key,
                label=label,
                found_in=["application"],
                details=details or {},
            )
        )
        return eid

    def field(
        self,
        eid: str,
        attribute: str,
        value: Any,
        *,
        state: str = "known",
        values: list[Any] | None = None,
    ) -> str:
        self.n += 1
        fid = f"f{self.n}"
        cands = [{"display": str(v), "value": v, "sources": [DOC]} for v in (values or [value])]
        self.db.add(
            ResolvedField(
                id=fid,
                submission_id=self.sid,
                entity_id=eid,
                attribute=attribute,
                label=attribute.replace("_", " ").capitalize(),
                kind="text",
                state=state,
                value_display=None if state == "conflict" else str(value),
                value=None if state == "conflict" else value,
                candidates=cands,
            )
        )
        return fid

    def rule(
        self,
        i: int,
        rule_id: str,
        outcome: str,
        items: list[str] | None = None,
        *,
        potential: bool = False,
        inputs: list[str] | None = None,
    ) -> None:
        self.db.add(
            RuleOutcomeRecord(
                id=f"r{i}",
                submission_id=self.sid,
                ordinal=i,
                rule_id=rule_id,
                title=f"Rule {rule_id}",
                section="Section",
                quote="guideline text",
                outcome=outcome,
                reason=f"{rule_id} reason",
                items=items or [],
                inputs=inputs or [],
                potential_decline=potential,
                ruleset_version="1.0.0",
            )
        )


def seed_submission(db: Session, store: BlobStore) -> str:
    """A small resolved submission: entities, a conflict, missing items, rules, a decision."""
    sub = ingest(db, store, make_email(subject="Acme Hauling submission")).submission_id
    s = Seed(db, sub)
    app = s.entity("applicant", "applicant", "Applicant")
    s.field(app, "legal_name", "Acme Hauling Inc")
    s.field(app, "mailing_address", "12 Elm Street")
    s.field(app, "mailing_state", "WI")
    agency = s.entity("agency", "agency", "Agency")
    s.field(agency, "contact", "Pat Agent")
    s.field(agency, "email", "pat@agency.example")
    s.entity(
        "operations",
        "operations",
        "Operations",
        {
            "commodities_quote": {
                "document_id": "d",
                "items": [{"label": "Produce", "value": "125000"}],
            }
        },
    )
    request = s.entity("coverage_request", "request", "Coverage requested")
    s.field(request, "cargo_limit", 100000)
    fleet = s.entity("fleet", "fleet", "Fleet")
    units = s.field(fleet, "power_units", None, state="conflict", values=[2, 3])
    s.entity("vehicle", "VIN1", "2020 Volvo", {"scheduled": True})
    driver = s.entity("driver", "lic:D1234567", "Lee Park")
    s.field(driver, "license_number", "D1234567")
    s.field(driver, "date_of_birth", "1980-05-01")
    claim = s.entity(
        "claim",
        "CL1",
        "Claim CL1",
        {"term_start": "2025-01-01", "lines": [{"claimant": "JANE ROE", "quote": "JANE ROE BI"}]},
    )
    s.field(claim, "date_of_loss", "2025-03-01")
    s.field(claim, "date_reported", "2025-03-29")
    s.field(claim, "incurred", "40000")
    s.field(claim, "reserved", "30000")
    s.field(claim, "status", "Open")
    for q, miles in (("2026-Q1", "60000"), ("2026-Q2", "40000")):
        s.entity(
            "ifta_quarter",
            q,
            f"IFTA {q}",
            {"total_miles": miles, "miles_by_state": {"WI": "10000"}},
        )
    s.db.flush()
    db.add(
        Conflict(
            id="c1",
            submission_id=sub,
            field_id=units,
            entity_id=fleet,
            type="cardinality",
            title="Power units",
            candidates=[
                {"display": "2", "value": 2, "sources": [DOC]},
                {"display": "3", "value": 3, "sources": [DOC]},
            ],
            blocking_rules=[],
        )
    )
    for code, category, detail in (
        ("APP_FEIN", "application", "No FEIN."),
        ("APP_SIGNATURE", "application", "The application is not signed."),
        ("DRV_UNLISTED:x", "drivers", "Sam Doe drove in a claim but is not scheduled."),
    ):
        db.add(
            MissingInfo(
                id=code, submission_id=sub, code=code, title=code, detail=detail, category=category
            )
        )
    s.rule(1, "APP-01", "refer")
    s.rule(2, "DOC-01", "flag", ["Updated loss runs before binding"])
    s.rule(3, "COV-02", "flag", ["internal wording"])
    s.rule(
        4, "DRV-01", "refer", ["Lee Park holds a class C license"], potential=True, inputs=[units]
    )
    s.rule(5, "DATA-01", "refer", ["Power units"])
    s.rule(6, "APP-02", "pass")
    db.add(
        DecisionRecord(
            submission_id=sub,
            outcome="refer",
            headline="Refer to Small Fleet underwriting",
            team="small_fleet",
            team_label="Small Fleet underwriting",
            route_reason="2 or 3 units",
            route_stable=True,
            potential_decline=True,
            reasons=[],
            anchor="application_date",
            anchor_date="2026-10-22",
            ruleset_version="1.0.0",
            ruleset_sha256="0" * 64,
            counts={},
        )
    )
    db.commit()
    return sub
