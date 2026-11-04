"""Resolution on synthetic extraction data: no model, no PDFs, every rule visible."""

from datetime import date
from typing import Any

import pytest

from lane.db.models import ConfidenceBand, Document
from lane.domain import geo, vin
from lane.domain.taxonomy import entity_form_from_name, question_key
from lane.resolve.board import Board, Entity, Source, resolve_field
from lane.resolve.build import Builder
from lane.resolve.compare import compare, person_key, same_person
from lane.resolve.derive import finalize
from lane.resolve.missing import find_missing

TODAY = date(2026, 10, 22)

# --- comparators and domain helpers ------------------------------------------------------


def test_companies_ignore_boilerplate_but_keep_real_differences() -> None:
    a, b = (
        compare("company", "Granite Peak Specialty"),
        compare("company", "GRANITE PEAK SPECIALTY INSURANCE COMPANY"),
    )
    assert a and b and a.key == b.key
    c, d = (
        compare("company", "Bayline Insurance Agency Inc"),
        compare("company", "Bayline Insurance Services Inc."),
    )
    assert c and d and c.key != d.key


def test_entity_form_from_names_and_checkboxes() -> None:
    assert entity_form_from_name("Acme Hauling Inc.") == ("ACME HAULING", "CORPORATION")
    assert entity_form_from_name("ACME HAULING LLC") == ("ACME HAULING", "LLC")
    form = compare("entity_form", "Corporation")
    assert form and form.key == "CORPORATION"


def test_person_names_match_across_orders_and_middle_names() -> None:
    assert person_key("SANDHU, DEVRAJ") == person_key("Devraj Sandhu")
    assert same_person(person_key("ALEKSANDR P VOLKOV"), person_key("VOLKOV ALEKSANDR PAVEL"))
    assert not same_person(person_key("Pat Lee"), person_key("Pat Kim"))


@pytest.mark.parametrize(
    ("text", "key"),
    [
        (
            "Are Filings Required? If Yes Please Provide:",
            "filings_required",
        ),  # "please" is not "lease"
        ("Does the applicant lease to others?", "leases_to_others"),
        ("Does the applicant haul any hazardous materials?", "hazmat"),
        ("Do you haul any Intermodal Containers?", "intermodal"),
        ("What is your favourite colour?", None),
    ],
)
def test_question_keys(text: str, key: str | None) -> None:
    assert question_key(text) == key


def test_vin_checks() -> None:
    good = vin.check("1XPBD49X5MD735204", current_year=2026)
    assert good.valid and good.maker == "PETERBILT" and good.model_year == 2021
    short = vin.check("3AKJGLD59HSKR736", current_year=2026)
    assert not short.valid and "16 characters instead of 17" in short.problems
    bad_digit = vin.check("1XPBD49X6MD735204", current_year=2026)
    assert not bad_digit.valid and any("check digit" in p for p in bad_digit.problems)
    assert vin.edit_distance_one("1XPBD49X5MD735204", "1XPBD49X5MD73520")


def test_geo() -> None:
    assert geo.to_code("Texas") == "TX" and geo.to_code("nj") == "NJ"
    assert 1100 < geo.miles_between("CA", "TX") < 1300


def test_resolve_field_supersession_and_conflict() -> None:
    board = Board()
    e = board.entity("driver", "x")
    old = Source("card", "drivers_license", "License card", as_of=date(2021, 1, 6))
    new = Source("mvr", "mvr", "MVR", as_of=date(2026, 10, 13))
    board.add(e, "license_expiration", "02/17/2025", old, kind="date")
    board.add(e, "license_expiration", "02/17/2030", new, kind="date")
    f = e.fields["license_expiration"]
    resolve_field(f, supersede_by_date=True)
    assert f.state == "known" and f.resolution == "supersession"
    assert f.value and f.value.compared.norm == "2030-02-17"

    board.add(e, "date_of_birth", "1991-10-03", old, kind="date")
    board.add(e, "date_of_birth", "10/30/1991", new, kind="date")
    dob = e.fields["date_of_birth"]
    resolve_field(dob, hint="The MVR is the state's record")
    assert dob.state == "conflict" and dob.conflict and dob.conflict["hint"]


# --- a synthetic submission --------------------------------------------------------------


def doc(doc_id: str, doc_type: str, title: str | None = None) -> Document:
    return Document(id=doc_id, doc_type=doc_type, title=title or doc_type, superseded_by_id=None)


def val(value: str | None) -> dict[str, Any]:
    return {"value": value, "quote": value, "page": 1}


def cell(value: str | None, interpreted: str | None = None) -> dict[str, Any] | None:
    if value is None and interpreted is None:
        return None
    return {"value": interpreted or value, "cell": "'App'!A1", "cell_value": value}


APPLICATION = {
    "business_name": cell("ACME HAULING INC"),
    "entity_type": cell("X", "Corporation"),
    "contact_name": cell("PAT OWNER"),
    "mailing_state": cell("CA"),
    "fein": None,
    "dot_number": cell("1234567"),
    "agency_name": cell("Harbor Agency Inc"),
    "um_limit": cell("State Minimum"),
    "reefer": cell("False"),
    "farthest_territory": cell("AZ"),
    "owner_is_driver": cell("False"),
    "years_prior_insurance": cell("5 YRS"),
    "commodities": [
        {"label": "Produce/Groceries", "percent": "0.6"},
        {"label": "Paper/Household goods", "percent": "0.4"},
    ],
    "questions": [{"question": "Do you haul any Intermodal Containers?", "answer": "False"}],
}
QUOTE = {
    "applicant_name": val("Acme Hauling Inc"),
    "agency_name": val("Harbor Agency Inc"),
    "um_limit": val("$30,000 / 60,000"),
    "reefer_breakdown_requested": val("Yes"),
    "commodities": [
        {"label": "Fresh produce", "percent": "30"},
        {"label": "Dry groceries", "percent": "30"},
        {"label": "Household goods", "percent": "40"},
    ],
    "questions": [
        {
            "question": "Are there any vehicles owned or operated by the applicant NOT listed on the application?",
            "answer": "No",
        }
    ],
    "prior_insurance": [
        {
            "start": "12/15/2025",
            "end": "12/15/2026",
            "carrier": "SUMMIT MUTUAL",
            "coverage": "(LIAB/PHD/MTC)",
            "units": "3",
        },
        {
            "start": "12/15/2024",
            "end": "12/15/2025",
            "carrier": "SUMMIT MUTUAL",
            "coverage": "(LIAB/PHD/MTC)",
            "units": "2",
        },
        {
            "start": "03/01/2023",
            "end": "02/01/2024",
            "carrier": "OLD CARRIER",
            "coverage": "(LIAB)",
            "units": "1",
        },
    ],
}
DRIVERS = {
    "rows": [
        {
            "first_name": "PAT",
            "last_name": "OWNER",
            "date_of_birth": "1980-01-02T00:00:00",
            "license_number": "C1111111",
            "license_state": "CA",
            "hire_date": "OWNER",
        },
        {
            "first_name": "LEE",
            "last_name": "DRIVER",
            "date_of_birth": "1985-05-03T00:00:00",
            "license_number": "D2222222",
            "license_state": "CA",
            "experience": "5+",
        },
    ]
}
VEHICLES = {
    "rows": [
        {"year": "2020", "make": "VOLVO", "vin": "4V4NC9EH9LN248615", "cost_new": "48000"},
        {"year": "2017", "make": "FREIGHTLINER", "vin": "3AKJGLD59HSKR736", "cost_new": "22000"},
    ]
}
MVR_LEE = {
    "driver_name": val("DRIVER, LEE"),
    "license_number": val("D2222222"),
    "date_of_birth": val("05/30/1985"),
    "license_type": val("COMMERCIAL"),
    "license_class": val("A"),
    "expiration_date": val("05/03/2030"),
    "order_date": val("10/09/2026"),
    "events": [],
    "notes": [],
}
LICENSE_LEE = {
    "full_name": val("DRIVER LEE"),
    "license_number": val("D222 2222"),
    "license_type": val("COMMERCIAL DRIVER LICENSE LIMITED-TERM"),
    "issue_date": val("01/06/2021"),
    "expiration_date": val("05/03/2025"),
}
LOSS_RUN = {
    "carrier": val("SUMMIT MUTUAL INSURANCE COMPANY"),
    "policy_number": val("P-1"),
    "period_start": val("12/15/2024"),
    "period_end": val("12/15/2025"),
    "run_date": val("10/06/2026"),
    "coverage_totals": [
        {"coverage": "Auto Liability", "losses_paid": "$10.00", "incurred": "$10.00"},
        {"coverage": "Physical Damage", "covered": "No Coverage"},
    ],
    "claims": [
        {
            "claim_number": "C9",
            "status": "Closed",
            "driver_name": "SAM UNLISTED",
            "vin": "1XPBD49X5MD735204",
            "vehicle_description": "2021 - PETERBILT -",
            "driver_status": "Unreported",
            "unit_reported": "Y",
            "total_paid": "$10.00",
        },
        {
            "claim_number": "C8",
            "status": "Open",
            "driver_name": "LEE DRIVER",
            "driver_date_of_birth": "05/30/1985",
            "vin": "4V4NC9EH9LN248615",
            "vehicle_description": "2020 - FREIGHTLINER -",
        },
    ],
}
LETTER = {
    "author": val("OTHER CARGO CO"),
    "coverage": val("MTC"),
    "period": val("12/15/24-12/15/25"),
    "statement": val("No paid losses"),
    "scope": val("paid losses"),
}
IFTA = {
    "taxpayer_name": val("ACME HAULING LLC"),
    "account_number": val("A-1"),
    "period_end": val("March 31, 2026"),
    "totals": {"total_miles": "10,000"},
    "rows": [
        {"jurisdiction": "California", "total_miles": "4,000"},
        {"jurisdiction": "Texas", "total_miles": "6,000"},
    ],
}


@pytest.fixture
def board() -> Board:
    docs = [
        doc("app", "application", "Application"),
        doc("quote", "quote_form", "Quote form"),
        doc("drv", "driver_schedule", "Driver schedule"),
        doc("veh", "vehicle_schedule", "Vehicle schedule"),
        doc("mvr", "mvr", "MVR, Lee Driver"),
        doc("card", "drivers_license", "License card"),
        doc("loss", "loss_run", "Loss run 2024-25"),
        doc("letter", "loss_history_letter", "Cargo letter"),
        doc("ifta", "ifta_return", "IFTA Q1 2026"),
    ]
    data = {
        "app": APPLICATION,
        "quote": QUOTE,
        "drv": DRIVERS,
        "veh": VEHICLES,
        "mvr": MVR_LEE,
        "card": LICENSE_LEE,
        "loss": LOSS_RUN,
        "letter": LETTER,
        "ifta": IFTA,
    }
    built = Builder(docs, data, {("loss", "claims[1].vin"): "fact-1"}, today=TODAY).build()
    finalize(built, today=TODAY)
    return built


def field(board: Board, kind: str, key: str, attr: str) -> Any:
    return board.entities[(kind, key)].fields[attr]


def entity(board: Board, kind: str, key: str) -> Entity:
    return board.entities[(kind, key)]


def test_the_same_driver_is_recognized_across_documents(board: Board) -> None:
    lee = entity(board, "driver", "lic:D2222222")
    assert lee.found_in >= {"driver_schedule", "mvr", "drivers_license", "loss_run"}
    assert field(board, "driver", "lic:D2222222", "name").state == "known"
    dob = field(board, "driver", "lic:D2222222", "date_of_birth")
    assert dob.state == "conflict" and "MVR" in dob.conflict["hint"]
    exp = field(board, "driver", "lic:D2222222", "license_expiration")
    assert exp.state == "known" and exp.value.compared.norm == "2030-05-03"
    card = lee.details["license_card"]
    assert "limited_term" not in card and "designation" not in card  # validity is the MVR's


def test_drivers_and_vehicles_only_in_losses_are_kept_apart(board: Board) -> None:
    sam = entity(board, "driver", "name:SAM-UNLISTED")
    assert sam.details["scheduled"] is False
    pete = entity(board, "vehicle", "1XPBD49X5MD735204")
    assert pete.details["scheduled"] is False and pete.label == "2021 Peterbilt (735204)"


def test_vehicle_make_conflict_carries_the_vin_decode(board: Board) -> None:
    make = field(board, "vehicle", "4V4NC9EH9LN248615", "make")
    assert make.state == "conflict" and make.conflict["type"] == "reference"
    assert make.conflict["hint"] == "The VIN itself decodes to VOLVO."
    assert any(s.doc_type == "vin_decode" for c in make.candidates for s in c.sources)
    assert field(board, "vehicle", "3AKJGLD59HSKR736", "vin").state == "invalid"


def test_cross_document_conflicts(board: Board) -> None:
    assert field(board, "applicant", "applicant", "entity_form").state == "conflict"
    assert field(board, "operations", "operations", "owner_drives").state == "conflict"
    assert field(board, "operations", "operations", "refrigerated").state == "conflict"
    assert field(board, "operations", "operations", "q_unlisted_vehicles").state == "conflict"
    assert field(board, "fleet", "fleet", "power_units").state == "conflict"  # 2 scheduled vs 3
    assert (
        field(board, "coverage_history", "history", "physical_damage_carried").state == "conflict"
    )
    # Stated farthest AZ (~480 mi from CA), but IFTA shows miles in TX (~1,200 mi).
    territory = field(board, "operations", "operations", "farthest_territory")
    assert territory.state == "conflict"
    assert {c.compared.norm for c in territory.candidates} == {"AZ", "TX"}


def test_mappings_reconcile_differently_shaped_forms(board: Board) -> None:
    um = field(board, "coverage_request", "request", "um_limit")
    # Kept as a low-severity conflict, with the reason the two likely agree as its hint
    assert um.state == "conflict" and um.conflict and "Likely the same" in um.conflict["hint"]
    mix = field(board, "operations", "operations", "commodity_mix")
    assert mix.state == "known" and mix.resolution == "mapping"


def test_carrier_names_agree_despite_boilerplate(board: Board) -> None:
    term = next(e for e in board.of_kind("coverage_term") if e.key == "auto_liability:2024-12-15")
    assert term.fields["carrier"].state == "known"


def test_missing_items(board: Board) -> None:
    codes = {m.code for m in find_missing(board)}
    assert "APP_FEIN" in codes
    assert "VEH_STATED_VALUE" in codes
    assert "VEH_VIN:3AKJGLD59HSKR736" in codes
    assert "VEH_UNLISTED:1XPBD49X5MD735204" in codes
    assert "DRV_UNLISTED:name:SAM-UNLISTED" in codes
    assert "DRV_MVR:lic:C1111111" in codes  # the owner has no MVR in this packet
    assert "LOSS_TERM:auto_liability:2023-03-01" in codes
    assert "LOSS_PARTIAL:cargo:2024-12-15" in codes
    assert any(c.startswith("LAPSE:2024-02-01") for c in codes)
    assert "IFTA_QUARTERS" in codes


def test_sources_keep_fact_ids(board: Board) -> None:
    vin_field = field(board, "vehicle", "4V4NC9EH9LN248615", "vin")
    ids = [fid for c in vin_field.candidates for s in c.sources for fid in s.fact_ids]
    assert "fact-1" in ids
    assert ConfidenceBand  # imported for type completeness


def test_each_value_cites_only_the_fact_it_was_read_from() -> None:
    """The legal form is read from the taxpayer name, so the account number printed beside it
    is not its evidence (the lens would box the wrong text)."""
    docs = [doc("ifta", "ifta_return", "IFTA Q1 2026"), doc("loss", "loss_run", "Loss run")]
    facts = {
        ("ifta", "taxpayer_name"): "f-name",
        ("ifta", "account_number"): "f-account",
        ("loss", "carrier"): "f-carrier",
        ("loss", "policy_number"): "f-policy",
    }
    built = Builder(docs, {"ifta": IFTA, "loss": LOSS_RUN}, facts, today=TODAY).build()

    def cited(kind: str, attr: str) -> set[str]:
        e = next(e for (k, _), e in built.entities.items() if k == kind)
        return {i for c in e.fields[attr].candidates for s in c.sources for i in s.fact_ids}

    assert cited("applicant", "entity_form") == {"f-name"}
    assert cited("applicant", "legal_name") == {"f-name"}
    assert cited("ifta_quarter", "account_number") == {"f-account"}
    assert cited("coverage_term", "carrier") == {"f-carrier"}
    assert cited("coverage_term", "policy_number") == {"f-policy"}


def test_an_email_body_is_never_evidence_for_entity_form_or_agency_name() -> None:
    email = {
        "applicant_name": val("Granite Ridge Freight LLC"),
        "agency_name": val("Some Agency"),
        "agent_name": val("Dana Doe"),
    }
    built = Builder([doc("mail", "email_body")], {"mail": email}, {}, today=TODAY).build()
    applicant = built.entities[("applicant", "applicant")].fields
    agency = built.entities[("agency", "agency")].fields
    assert "legal_name" in applicant  # the name it gives is still a candidate
    assert "entity_form" not in applicant and "name" not in agency
    assert "contact" in agency


class _EveryPathHasAFact(dict[tuple[str, str], str]):
    """Facts for every extracted path, so only a value that cites no path at all comes back
    without facts."""

    def get(self, key: tuple[str, str], default: str | None = None) -> str | None:  # type: ignore[override]
        return f"{key[0]}:{key[1]}"


def test_every_value_with_a_document_cites_a_fact() -> None:
    """Derived values included: a value worked out by code links the facts it was worked out
    from, so the evidence lens always has a page and a box to open."""
    docs = [
        doc("app", "application", "Application"),
        doc("quote", "quote_form", "Quote form"),
        doc("drv", "driver_schedule", "Driver schedule"),
        doc("veh", "vehicle_schedule", "Vehicle schedule"),
        doc("mvr", "mvr", "MVR, Lee Driver"),
        doc("card", "drivers_license", "License card"),
        doc("loss", "loss_run", "Loss run 2024-25"),
        doc("letter", "loss_history_letter", "Cargo letter"),
        doc("ifta", "ifta_return", "IFTA Q1 2026"),
    ]
    data = {
        "app": APPLICATION,
        "quote": QUOTE,
        "drv": DRIVERS,
        "veh": VEHICLES,
        "mvr": MVR_LEE,
        "card": LICENSE_LEE,
        "loss": LOSS_RUN,
        "letter": LETTER,
        "ifta": IFTA,
    }
    built = Builder(docs, data, _EveryPathHasAFact(), today=TODAY).build()
    finalize(built, today=TODAY)
    bare = sorted(
        f"{e.kind}.{f.attribute} = {c.compared.display} ({s.doc_type}, {s.title})"
        for e in built.entities.values()
        for f in e.fields.values()
        for c in f.candidates
        for s in c.sources
        if s.document_id and not s.fact_ids
    )
    assert bare == [], "\n".join(bare)


def test_a_license_class_read_from_the_experience_column_cites_that_cell() -> None:
    rows = {"rows": [{**DRIVERS["rows"][1], "experience": "Class C, 10 yrs"}]}
    docs = [doc("drv", "driver_schedule", "Driver schedule")]
    built = Builder(
        docs, {"drv": rows}, {("drv", "rows[0].experience"): "fact-exp"}, today=TODAY
    ).build()
    cls = field(built, "driver", "lic:D2222222", "license_class")
    assert [c.compared.norm for c in cls.candidates] == ["C"]
    assert [s.fact_ids for c in cls.candidates for s in c.sources] == [("fact-exp",)]


def test_a_license_state_read_from_the_state_column_cites_that_cell() -> None:
    row = {k: v for k, v in DRIVERS["rows"][1].items() if k != "license_state"}
    docs = [doc("drv", "driver_schedule", "Driver schedule")]
    built = Builder(
        docs,
        {"drv": {"rows": [{**row, "state": "CA"}]}},
        {("drv", "rows[0].state"): "fact-st"},
        today=TODAY,
    ).build()
    st = field(built, "driver", "lic:D2222222", "license_state")
    assert [s.fact_ids for c in st.candidates for s in c.sources] == [("fact-st",)]
