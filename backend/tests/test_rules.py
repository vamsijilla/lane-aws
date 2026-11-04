"""Every guideline rule on small hand-built views: no database, no model."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from lane.rules.engine import evaluate, pick_anchor
from lane.rules.model import Outcome, Ruleset
from lane.rules.predicates import REGISTRY, RuleContext
from lane.rules.view import EntityView, FieldView, SubmissionView

RULES = Ruleset.load()
PARAMS = RULES.params
APP_DATE = date(2026, 10, 22)
EFFECTIVE = date(2026, 12, 15)

# --- a tiny builder for views --------------------------------------------------------------


class Conflict:
    def __init__(self, *values: Any) -> None:
        self.values = values


class ViewBuilder:
    def __init__(self) -> None:
        self.view = SubmissionView()
        self.n = 0

    def entity(
        self,
        kind: str,
        key: str = "",
        label: str = "",
        details: dict[str, Any] | None = None,
        **fields: Any,
    ) -> EntityView:
        self.n += 1
        fv: dict[str, FieldView] = {}
        for attr, value in fields.items():
            fid = f"{kind}.{key or kind}.{attr}"
            if isinstance(value, Conflict):
                cands = [
                    {"value": v, "display": str(v), "sources": [{"title": f"doc {i}"}]}
                    for i, v in enumerate(value.values)
                ]
                f = FieldView(
                    fid, attr.replace("_", " ").capitalize(), "conflict", None, None, cands
                )
            elif value is None:
                f = FieldView(fid, attr, "missing", None, None, [])
            elif isinstance(value, tuple) and value and value[0] == "invalid":
                f = FieldView(
                    fid,
                    attr,
                    "invalid",
                    value[1],
                    str(value[1]),
                    [{"value": value[1], "display": str(value[1])}],
                )
            else:
                f = FieldView(
                    fid,
                    attr,
                    "known",
                    value,
                    str(value),
                    [{"value": value, "display": str(value), "sources": [{"title": "doc"}]}],
                )
            fv[attr] = f
            self.view.fields_by_id[fid] = f
        e = EntityView(
            f"e{self.n}", kind, key or kind, label or key or kind, details or {}, [], fv, self.view
        )
        self.view.entities.append(e)
        return e


def ctx(
    view: SubmissionView, anchor: date = APP_DATE, conflicts: list[str] | None = None
) -> RuleContext:
    return RuleContext(view, PARAMS, anchor, "application date", EFFECTIVE, conflicts or [])


def run(name: str, view: SubmissionView, **kw: Any) -> Any:
    return REGISTRY[name](ctx(view, **kw))


def mvr(order: str, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"order_date": order, "events": events or []}


def driver(
    b: ViewBuilder,
    key: str,
    *,
    cdl: Any = True,
    dob: Any = "1985-01-01",
    owner: bool = False,
    mvrs: list[dict[str, Any]] | None = None,
    scheduled: bool = True,
    **fields: Any,
) -> EntityView:
    details: dict[str, Any] = {
        "scheduled": scheduled,
        "mvrs": mvrs if mvrs is not None else [mvr("2026-10-09")],
    }
    if owner:
        details["owner_marker"] = {"document_id": "d", "title": "Driver list"}
    return b.entity(
        "driver",
        key,
        key.title(),
        details,
        commercial_license=cdl,
        date_of_birth=dob,
        license_status="VALID",
        experience_stated="5+",
        **fields,
    )


def term(
    b: ViewBuilder,
    start: str,
    end: str,
    *,
    evidence: list[dict[str, Any]] | None = None,
    listed: bool = True,
    line: str = "auto_liability",
) -> EntityView:
    return b.entity(
        "coverage_term",
        f"{line}:{start}",
        f"{line} {start[:4]}",
        {
            "line": line,
            "start": start,
            "end": end,
            "loss_evidence": evidence or [],
            "listed_on_application": listed,
        },
    )


RUN = {"kind": "loss_run", "complete": True, "run_date": "2026-10-06"}

# --- submission requirements -------------------------------------------------------------


def test_fein_and_mc() -> None:
    b = ViewBuilder()
    b.entity("applicant", fein=None, mc_number="123456")
    assert run("has_fein", b.view).outcome is Outcome.REFER
    assert run("has_mc", b.view).outcome is Outcome.PASS


def test_loss_run_age_and_effective_date_warning() -> None:
    b = ViewBuilder()
    term(b, "2025-12-15", "2026-12-15", evidence=[RUN])
    v = run("loss_runs_current", b.view)
    assert v.outcome is Outcome.FLAG and "70 days old" in v.reason
    stale = run("loss_runs_current", b.view, anchor=date(2027, 1, 30))
    assert stale.outcome is Outcome.REFER


def test_loss_run_years() -> None:
    b = ViewBuilder()
    for y in (2022, 2023, 2024, 2025):
        term(b, f"{y}-12-15", f"{y + 1}-12-15", evidence=[RUN])
    assert run("loss_run_years", b.view).outcome is Outcome.PASS
    b2 = ViewBuilder()
    term(b2, "2025-12-15", "2026-12-15", evidence=[RUN])
    term(b2, "2024-12-15", "2025-12-15", evidence=[RUN])
    term(b2, "2023-03-01", "2024-02-01")
    v = run("loss_run_years", b2.view)
    assert v.outcome is Outcome.REFER and any("2023" in i for i in v.items)


def test_ifta_quarters_respect_the_filing_lag() -> None:
    b = ViewBuilder()
    b.entity(
        "operations",
        details={
            "ifta": {
                "quarters": [{"quarter": q} for q in ("2025-Q3", "2025-Q4", "2026-Q1", "2026-Q2")]
            }
        },
    )
    assert run("ifta_quarters", b.view).outcome is Outcome.PASS  # Q3 2026 not yet due on Oct 22
    later = run("ifta_quarters", b.view, anchor=EFFECTIVE)
    assert later.outcome is Outcome.REFER and later.items == ["IFTA return for Q3 2026"]


def test_mvrs_current() -> None:
    b = ViewBuilder()
    driver(b, "lee", mvrs=[mvr("2026-10-09")])
    driver(b, "kim", mvrs=[mvr("2025-09-24")])
    driver(b, "pat", mvrs=[])
    v = run("mvrs_current", b.view)
    assert v.outcome is Outcome.REFER and len(v.items) == 2


def test_vehicle_schedule_complete() -> None:
    b = ViewBuilder()
    b.entity(
        "vehicle",
        "a",
        "Unit A",
        {"scheduled": True},
        vin="4V4NC9EH9LN248615",
        year=2020,
        make="VOLVO",
        stated_value="90000",
    )
    assert run("vehicle_schedule_complete", b.view).outcome is Outcome.PASS
    b.entity(
        "vehicle",
        "b",
        "Unit B",
        {"scheduled": True},
        vin=("invalid", "3AKJGLD59HSKR736"),
        year=2017,
        make="FREIGHTLINER",
        stated_value=None,
    )
    v = run("vehicle_schedule_complete", b.view)
    assert v.outcome is Outcome.REFER and v.items == [
        "Unit B: send a valid 17-character VIN and the stated value"
    ]


# --- eligibility ---------------------------------------------------------------------------


def test_eligible_units() -> None:
    b = ViewBuilder()
    b.entity("vehicle", "a", "Unit A", {"scheduled": True}, vehicle_type="Tractor")
    b.entity("applicant", operation="TRUCKING FOR HIRE")
    assert run("eligible_units", b.view).outcome is Outcome.PASS
    b.entity("vehicle", "b", "Unit B", {"scheduled": True}, vehicle_type="Dump truck")
    assert run("eligible_units", b.view).outcome is Outcome.DECLINE


@pytest.mark.parametrize(
    ("state", "outcome"), [("CA", Outcome.PASS), ("IL", Outcome.REFER), ("NY", Outcome.DECLINE)]
)
def test_available_state(state: str, outcome: Outcome) -> None:
    b = ViewBuilder()
    b.entity("applicant", mailing_state=state)
    assert run("available_state", b.view).outcome is outcome


def test_texas_rule_only_for_texas_accounts() -> None:
    b = ViewBuilder()
    b.entity("applicant", garaging_state="TX")
    assert run("texas_north_of_i10", b.view).outcome is Outcome.UNKNOWN
    b2 = ViewBuilder()
    b2.entity("applicant", mailing_state="CA")
    assert run("texas_north_of_i10", b2.view).outcome is Outcome.NA


def test_prohibited_operations() -> None:
    b = ViewBuilder()
    b.entity(
        "operations", {"commodities_quote": {"items": [{"label": "Fresh produce"}]}}, q_hazmat=False
    )
    assert run("prohibited_operations", b.view).outcome is Outcome.PASS
    b2 = ViewBuilder()
    b2.entity(
        "operations", details={"commodities_quote": {"items": [{"label": "Lithium batteries"}]}}
    )
    assert run("prohibited_operations", b2.view).outcome is Outcome.DECLINE
    b3 = ViewBuilder()
    b3.entity(
        "operations", details={"commodities_quote": {"items": [{"label": "Household appliances"}]}}
    )
    assert run("prohibited_operations", b3.view).outcome is Outcome.FLAG


def test_border_and_safer_are_unknown_without_data() -> None:
    b = ViewBuilder()
    b.entity("applicant", mailing_state="CA", dot_number="1234567")
    b.entity("operations", details={"ifta": {"miles_by_state": {"CA": "1000"}}})
    assert run("border_distance", b.view).outcome is Outcome.UNKNOWN
    assert "1234567" in run("safer_record", b.view).reason
    b2 = ViewBuilder()
    b2.entity("applicant", mailing_state="WI")
    b2.entity("operations", details={"ifta": {"miles_by_state": {"WI": "1000"}}})
    assert run("border_distance", b2.view).outcome is Outcome.PASS


def test_new_venture() -> None:
    b = ViewBuilder()
    b.entity("operations", new_venture=False)
    b.entity("applicant", years_experience=5)
    assert run("new_venture", b.view).outcome is Outcome.NA
    b2 = ViewBuilder()
    b2.entity("operations", new_venture=True)
    b2.entity("applicant", years_experience=1, entity_form="LLC")
    assert run("new_venture", b2.view).outcome is Outcome.REFER


def test_coverage_rules() -> None:
    b = ViewBuilder()
    b.entity(
        "coverage_request", auto_liability_deductible="Do not offer", physical_damage_requested=True
    )
    assert run("no_liability_deductible", b.view).outcome is Outcome.PASS
    assert run("no_physical_damage", b.view).outcome is Outcome.FLAG
    b2 = ViewBuilder()
    b2.entity("coverage_request", auto_liability_deductible="$2,500")
    assert run("no_liability_deductible", b2.view).outcome is Outcome.REFER


# --- drivers -------------------------------------------------------------------------------


def test_cdl_rule_excludes_an_owner_who_does_not_drive() -> None:
    b = ViewBuilder()
    driver(b, "owner", cdl=False, owner=True)
    driver(b, "lee")
    ops = b.entity("operations", owner_drives=True)
    assert run("drivers_hold_cdl", b.view).outcome is Outcome.DECLINE
    ops._fields["owner_drives"].value = False
    assert run("drivers_hold_cdl", b.view).outcome is Outcome.PASS


def test_experience_age_and_medical() -> None:
    b = ViewBuilder()
    driver(b, "young", dob="2005-01-01")
    driver(b, "senior", dob="1955-01-01")
    assert run("driver_age", b.view).outcome is Outcome.REFER
    assert run("senior_medical", b.view).outcome is Outcome.REFER
    assert run("driver_experience", b.view).outcome is Outcome.PASS
    b2 = ViewBuilder()
    b2.entity(
        "driver",
        "new",
        "New",
        {"scheduled": True},
        commercial_license=True,
        license_issued="2025-06-01",
    )
    assert run("driver_experience", b2.view).outcome is Outcome.REFER


def test_points_windows() -> None:
    b = ViewBuilder()
    driver(b, "lee", mvrs=[mvr("2026-10-09", [{"event_date": "06/07/2023", "points": "4"}])])
    v = run("driver_points", b.view)
    assert v.outcome is Outcome.PASS and "just outside" in v.reason
    b2 = ViewBuilder()
    driver(
        b2,
        "kim",
        mvrs=[
            mvr(
                "2026-10-09",
                [
                    {"event_date": "03/01/2026", "points": "3"},
                    {"event_date": "05/01/2026", "points": "2"},
                ],
            )
        ],
    )
    assert run("driver_points", b2.view).outcome is Outcome.REFER


@pytest.mark.parametrize(
    ("description", "outcome"),
    [
        ("SPEED-TRUCK/TRAILER OVER 55 MPH", Outcome.PASS),
        ("SPEEDING 15-29 MPH OVER LIMIT", Outcome.PASS),
        ("SPEEDING 35 MPH OVER LIMIT", Outcome.DECLINE),
        ("DUI", Outcome.DECLINE),
        ("RECKLESS DRIVING", Outcome.DECLINE),
        ("PASSING STOPPED SCHOOL BUS", Outcome.DECLINE),
    ],
)
def test_unacceptable_history(description: str, outcome: Outcome) -> None:
    b = ViewBuilder()
    driver(
        b,
        "lee",
        mvrs=[
            mvr(
                "2026-10-09",
                [{"kind": "conviction", "description": description, "event_date": "03/14/2025"}],
            )
        ],
    )
    assert run("unacceptable_history", b.view).outcome is outcome


def test_old_dui_falls_outside_five_years() -> None:
    b = ViewBuilder()
    driver(b, "lee", mvrs=[mvr("2026-10-09", [{"description": "DUI", "event_date": "01/01/2019"}])])
    assert run("unacceptable_history", b.view).outcome is Outcome.PASS


# --- prior insurance -----------------------------------------------------------------------


def test_continuous_coverage_and_lapses() -> None:
    b = ViewBuilder()
    term(b, "2022-03-08", "2023-02-10")
    term(b, "2023-12-15", "2024-12-15")
    term(b, "2024-12-15", "2025-12-15")
    term(b, "2025-12-15", "2026-12-15")
    v = run("continuous_coverage", b.view)
    assert v.outcome is Outcome.REFER and v.reason.startswith("54 days")
    from_effective = run("continuous_coverage", b.view, anchor=EFFECTIVE)
    # The gap ended on the day the 3-year window opens: it is history, not a lapse to explain
    assert from_effective.outcome is Outcome.PASS


def test_losses_for_every_term() -> None:
    b = ViewBuilder()
    term(b, "2024-12-15", "2025-12-15", evidence=[RUN])
    term(
        b,
        "2023-12-15",
        "2024-12-15",
        line="cargo",
        evidence=[{"kind": "letter", "complete": False, "scope": "paid losses"}],
    )
    v = run("losses_for_every_term", b.view)
    assert v.outcome is Outcome.REFER and "paid losses only" in v.items[0]


def test_selective_rules() -> None:
    b = ViewBuilder()
    assert run("premium_per_unit", b.view).outcome is Outcome.UNKNOWN
    b.entity("vehicle", "a", "Unit A", {"scheduled": True}, vehicle_type="Box truck")
    assert run("selective_units", b.view).outcome is Outcome.REFER


# --- engine --------------------------------------------------------------------------------


def clean_submission(b: ViewBuilder, *, owner_drives: Any = False, units: Any = 4) -> None:
    b.entity(
        "applicant",
        fein="12-3456789",
        mc_number="123",
        mailing_state="CA",
        application_date="2026-10-22",
        effective_date="2026-12-15",
        years_experience=5,
        garaging_state="WI",
    )
    b.entity(
        "operations",
        details={
            "ifta": {
                "quarters": [{"quarter": q} for q in ("2025-Q3", "2025-Q4", "2026-Q1", "2026-Q2")],
                "miles_by_state": {"WI": "1000"},
            }
        },
        owner_drives=owner_drives,
        new_venture=False,
    )
    b.entity("coverage_request", auto_liability_deductible="none")
    b.entity("fleet", power_units=units)
    for y in (2022, 2023, 2024, 2025):
        term(b, f"{y}-12-15", f"{y + 1}-12-15", evidence=[{**RUN, "run_date": "2026-11-20"}])
    b.entity(
        "vehicle",
        "a",
        "Unit A",
        {"scheduled": True},
        vin="4V4NC9EH9LN248615",
        year=2020,
        make="VOLVO",
        stated_value="90000",
        vehicle_type="Tractor",
    )
    driver(b, "owner", cdl=False, owner=True)
    driver(b, "lee")


def results_of(
    view: SubmissionView, conflicts: dict[str, str] | None = None
) -> tuple[dict[str, Any], Any]:
    results, decision = evaluate(view, RULES, received=APP_DATE, conflicts=conflicts or {})
    return {r.spec.id: r for r in results}, decision


def test_a_conflict_that_changes_a_decline_becomes_a_potential_decline() -> None:
    b = ViewBuilder()
    clean_submission(b, owner_drives=Conflict(False, True))
    fid = "operations.operations.owner_drives"
    by_id, decision = results_of(b.view, {fid: "Owner drives"})
    drv = by_id["DRV-01"]
    assert drv.outcome is Outcome.REFER and drv.potential_decline
    assert drv.blocking_conflicts == [fid]
    assert {s["outcome"] for s in drv.sensitivity} == {"pass", "decline"}
    assert decision.outcome == "refer" and decision.potential_decline


def test_a_confirmed_decline_stands() -> None:
    b = ViewBuilder()
    clean_submission(b, owner_drives=True)
    by_id, decision = results_of(b.view)
    assert by_id["DRV-01"].outcome is Outcome.DECLINE and not by_id["DRV-01"].potential_decline
    assert decision.outcome == "decline" and decision.headline == "Decline recommended"


def test_unknown_resolves_when_every_candidate_agrees() -> None:
    b = ViewBuilder()
    clean_submission(b)
    driver(b, "morales", dob=Conflict("1991-10-03", "1991-10-30"))
    fid = "driver.morales.date_of_birth"
    by_id, _ = results_of(b.view, {fid: "Date of birth"})
    assert by_id["DRV-03"].outcome is Outcome.PASS
    assert "under every value" in by_id["DRV-03"].verdict.reason


def test_routing_is_stable_or_goes_to_the_larger_team() -> None:
    b = ViewBuilder()
    clean_submission(b, units=Conflict(4, 5))
    _, decision = results_of(b.view)
    assert decision.team == "small_fleet" and decision.route_stable
    b2 = ViewBuilder()
    clean_submission(b2, units=Conflict(9, 12))
    _, decision2 = results_of(b2.view)
    assert decision2.team == "fleet" and not decision2.route_stable


def test_a_clean_submission_is_ready() -> None:
    b = ViewBuilder()
    clean_submission(b)
    by_id, decision = results_of(b.view)
    blocking = {
        k: (r.outcome, r.verdict.reason)
        for k, r in by_id.items()
        if r.outcome in (Outcome.REFER, Outcome.DECLINE)
    }
    assert not blocking, blocking
    # SAFER and premium stay unknown at intake (A07) and become checks at quote, not a refer
    assert {r.spec.id for r in by_id.values() if r.outcome is Outcome.UNKNOWN} == {
        "ELIG-06",
        "SEL-01",
    }
    assert decision.outcome == "ready"
    assert decision.headline.startswith("Ready for")
    assert decision.reasons == [
        "To check at quote: ELIG-06 No serious SAFER violations",
        "To check at quote: SEL-01 Premium per unit or fleet size",
    ]


def test_any_other_unknown_still_refers() -> None:
    b = ViewBuilder()
    clean_submission(b)
    b.view.entities = [e for e in b.view.entities if e.kind != "driver"]  # no drivers shown
    by_id, decision = results_of(b.view)
    assert by_id["DRV-01"].outcome is Outcome.UNKNOWN
    assert decision.outcome == "refer"
    assert any(r.startswith("DRV-01") for r in decision.reasons)
    assert decision.reasons[-2:] == [
        "To check at quote: ELIG-06 No serious SAFER violations",
        "To check at quote: SEL-01 Premium per unit or fleet size",
    ]


def test_anchor_choice_and_note() -> None:
    b = ViewBuilder()
    clean_submission(b)
    assert pick_anchor(b.view, "application_date", date(2026, 10, 31)) == (
        APP_DATE,
        "application_date",
    )
    assert pick_anchor(b.view, "effective_date", date(2026, 10, 31)) == (
        EFFECTIVE,
        "effective_date",
    )
    by_id, _ = results_of(b.view)
    assert by_id["DOC-03"].anchor_note and "Q3 2026" in by_id["DOC-03"].anchor_note


def test_ruleset_file_is_valid() -> None:
    assert RULES.version and len(RULES.sha256) == 64
    assert all(r.predicate in REGISTRY for r in RULES.rules)
    assert {r.id for r in RULES.rules if r.decline} >= {
        "ELIG-01",
        "ELIG-02",
        "ELIG-04",
        "DRV-01",
        "DRV-06",
    }


# --- readings and assumptions ------------------------------------------------------------


def miles(b: ViewBuilder, by_state: dict[str, str]) -> None:
    b.entity("operations", details={"ifta": {"miles_by_state": by_state}})


@pytest.mark.parametrize(
    ("by_state", "outcome"),
    [
        ({"CA": "1000", "AZ": "500"}, Outcome.PASS),
        ({"CA": "1000", "NJ": "30"}, Outcome.FLAG),  # under the 5% tolerance
        ({"CA": "1000", "NJ": "900", "PA": "400"}, Outcome.DECLINE),
        ({}, Outcome.UNKNOWN),
    ],
)
def test_where_the_trucks_run(by_state: dict[str, str], outcome: Outcome) -> None:
    b = ViewBuilder()
    miles(b, by_state)
    assert run("operations_in_listed_states", b.view).outcome is outcome


def test_an_implausible_reading_that_declines_only_flags() -> None:
    b = ViewBuilder()
    clean_submission(b)
    ops = next(e for e in b.view.entities if e.kind == "operations")
    ops.details["ifta"]["miles_by_state"] = {"WI": "1000", "NJ": "900"}
    by_id, decision = results_of(b.view)
    elig = by_id["ELIG-02"]
    assert elig.outcome is Outcome.FLAG and not elig.potential_decline
    assert "assumption A03" in elig.verdict.reason
    assert elig.verdict.items == ["Confirm where trucks are dispatched and garaged."]
    assert [r["outcome"] for r in elig.readings] == ["flag"]
    assert not decision.potential_decline


def test_a_plausible_reading_that_declines_makes_a_potential_decline() -> None:
    b = ViewBuilder()
    clean_submission(b)
    ops = next(e for e in b.view.entities if e.kind == "operations")
    ops.details["ifta"]["miles_by_state"] = {"WI": "1000", "NJ": "900"}
    rules = RULES.model_copy(deep=True)
    spec = next(r for r in rules.rules if r.id == "ELIG-02")
    spec.readings[0].plausible = True
    results, decision = evaluate(b.view, rules, received=APP_DATE, conflicts={})
    elig = next(r for r in results if r.spec.id == "ELIG-02")
    assert elig.outcome is Outcome.REFER and elig.potential_decline
    assert decision.potential_decline


def test_readings_that_agree_change_nothing() -> None:
    b = ViewBuilder()
    clean_submission(b)
    by_id, _ = results_of(b.view)
    elig = by_id["ELIG-02"]
    assert elig.outcome is Outcome.PASS and not elig.potential_decline
    assert elig.readings[0]["outcome"] == "pass"


def test_a_decline_that_another_reading_passes_is_not_certain() -> None:
    b = ViewBuilder()
    clean_submission(b)
    applicant = next(e for e in b.view.entities if e.kind == "applicant")
    applicant._fields["garaging_state"].value = "NY"  # based outside the program states
    by_id, _ = results_of(b.view)
    elig = by_id["ELIG-02"]
    assert elig.outcome is Outcome.REFER and elig.potential_decline
    assert "another reading" in elig.verdict.reason


def test_rules_cite_only_defined_assumptions(tmp_path: Path) -> None:
    assert RULES.assumptions["A03"].startswith('"Non-listed states"')
    text = (Path(__file__).resolve().parents[1] / "rules" / "knight_trucking.v1.yaml").read_text()
    (tmp_path / "knight_trucking.v1.yaml").write_text(
        text.replace("assumptions: [A04]", "assumptions: [A99]")
    )
    with pytest.raises(ValueError, match="A99"):
        Ruleset.load(directory=tmp_path)


def test_an_anchor_note_needs_a_real_change_not_a_conflicted_input() -> None:
    """Both dates of birth make the driver well over 23 under either anchor date, so counting
    from the effective date changes nothing and there is no note."""
    b = ViewBuilder()
    clean_submission(b)
    driver(b, "morales", dob=Conflict("1991-04-02", "1992-04-02"))
    fid = "driver.morales.date_of_birth"
    by_id, _ = results_of(b.view, {fid: "Date of birth: Morales"})
    assert by_id["DRV-03"].outcome is Outcome.PASS
    assert by_id["DRV-03"].anchor_note is None


def test_rules_waiting_on_one_unlisted_driver_share_one_question() -> None:
    """A driver found only in loss history is one question, however many rules wait on it."""
    b = ViewBuilder()
    clean_submission(b)
    sam = driver(b, "sam", scheduled=False, mvrs=[])
    sam.found_in = ["loss_run"]
    by_id, _ = results_of(b.view)
    waiting = {rid: [w["key"] for w in r.waiting_on] for rid, r in by_id.items() if r.waiting_on}
    unknown = [rid for rid, keys in waiting.items() if by_id[rid].outcome is Outcome.UNKNOWN]
    assert len(unknown) >= 2  # several rules wait on the same driver
    assert {k for keys in waiting.values() for k in keys} == {"driver:Sam"}
    assert by_id[unknown[0]].waiting_on[0]["label"] == "Sam's license and driving record"


def test_a_clean_submission_waits_on_nothing() -> None:
    b = ViewBuilder()
    clean_submission(b)
    by_id, _ = results_of(b.view)
    assert all(not r.waiting_on for r in by_id.values() if r.outcome is not Outcome.UNKNOWN)
