"""A value decides something only if it was found on the page and read cleanly. Anything else
keeps its evidence and an honest state, and rules treat it as unknown."""

from datetime import date
from typing import Any

import pytest

from lane.documents.layout import load_layout  # noqa: F401  (layout format used below)
from lane.domain import normalize as nz
from lane.extract.grounding import PageWords
from lane.resolve.board import Board, Source, resolve_field
from lane.resolve.build import Builder
from lane.resolve.compare import compare, explain_unparsed
from lane.resolve.derive import finalize
from tests.test_resolve import APPLICATION, TODAY, doc

# --- compound counts and stated absence ----------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "3 power units and 2 trailers",
        "3 trucks, 1 spare",
        "4 tractors & 2 trailers",
        "2 or 3 units",
    ],
)
def test_a_compound_count_is_ambiguous_not_guessed(raw: str) -> None:
    assert compare("number", raw) is None
    assert "more than one" in explain_unparsed("number", raw)


@pytest.mark.parametrize(
    ("raw", "value"),
    [
        ("5", 5),
        ("5+", 5),
        ("5 YRS", 5),
        ("5 (3 owned, 2 leased)", 5),  # one total with detail
        ("1,200", 1200),
        ("0-500 Miles (CA only)", 0),  # unchanged: a range reads as its first number
    ],
)
def test_a_single_count_still_reads(raw: str, value: int) -> None:
    got = compare("number", raw)
    assert got is not None and got.norm == str(value)


def test_stated_absence_is_not_a_value_to_read() -> None:
    for raw in ("Not provided", "n/a", "TBD", "to follow", "pending", "none", "-"):
        assert nz.is_absent(raw, "date"), raw
    assert not nz.is_absent("12/15/2026", "date")
    assert not nz.is_absent("maybe", "bool")  # ambiguous, not absent
    assert not nz.is_absent("n/a", "bool")  # an unanswered yes/no question is not "no"
    assert not nz.is_absent("Not provided", "text")  # free text is kept as printed


# --- a value that cannot be read ----------------------------------------------------------


def _source(title: str = "Application") -> Source:
    return Source("d1", "application", title)


def test_an_unparseable_value_is_invalid_not_dropped() -> None:
    board = Board()
    e = board.entity("applicant", "applicant")
    board.add(e, "effective_date", "sometime next spring", _source(), kind="date")
    f = e.fields["effective_date"]
    resolve_field(f)
    assert f.state == "invalid" and f.value is not None  # a candidate, with no usable value
    assert f.value.compared.norm is None and f.value.compared.display == "sometime next spring"
    assert "could not be read as a date" in f.invalid[0]


def test_an_unparseable_value_beside_a_valid_one_is_a_conflict() -> None:
    board = Board()
    e = board.entity("applicant", "applicant")
    board.add(e, "effective_date", "12/15/2026", _source("Application"), kind="date")
    board.add(e, "effective_date", "next spring", Source("d2", "email_body", "Email"), kind="date")
    f = e.fields["effective_date"]
    resolve_field(f)
    assert f.state == "conflict" and len(f.active) == 2


def test_stated_absence_creates_no_candidate_and_free_text_is_untouched() -> None:
    board = Board()
    e = board.entity("applicant", "applicant")
    board.add(e, "effective_date", "Not provided", _source(), kind="date")
    board.add(e, "operation", "Not provided", _source(), kind="text")
    resolve_field(e.fields["effective_date"])
    resolve_field(e.fields["operation"])
    assert e.fields["effective_date"].state == "missing"
    assert e.fields["operation"].state == "known"  # free text is whatever was printed


# --- not on the page -----------------------------------------------------------------------


def _built(ungrounded: frozenset[str], facts: dict[tuple[str, str], str] | None = None) -> Board:
    docs = [doc("app", "application", "Application")]
    board = Builder(
        docs,
        {"app": APPLICATION},
        facts or {("app", "dot_number"): "f-dot"},
        today=TODAY,
        ungrounded=ungrounded,
    ).build()
    finalize(board, today=TODAY)
    return board


def test_a_value_not_found_on_the_page_is_unverified_and_not_known() -> None:
    f = _built(frozenset({"f-dot"})).entity("applicant", "applicant").fields["dot_number"]
    assert f.state == "unverified" and f.value is None
    assert f.candidates and f.candidates[0].compared.display == "1234567"  # still shown


def test_a_grounded_value_is_known() -> None:
    f = _built(frozenset()).entity("applicant", "applicant").fields["dot_number"]
    assert f.state == "known" and f.value is not None


def test_an_unverified_value_does_not_make_a_conflict_with_a_verified_one() -> None:
    board = Board()
    e = board.entity("applicant", "applicant")
    board.add(e, "dot_number", "1234567", _source(), kind="id")
    board.add(
        e,
        "dot_number",
        "7654321",
        Source("d2", "email_body", "Email", fact_ids=("f1",), verified=False),
        kind="id",
    )
    f = e.fields["dot_number"]
    resolve_field(f)
    assert f.state == "known" and f.value and f.value.compared.display == "1234567"
    assert any("not found on the page" in n for n in f.notes)


def test_inferred_fields_do_not_need_to_appear_on_the_page() -> None:
    b = Builder(
        [doc("lr", "loss_run")],
        {},
        {("lr", "coverage_totals[0].covered"): "f-c", ("lr", "coverage_totals[0].coverage"): "f-x"},
        today=TODAY,
        ungrounded=frozenset({"f-c", "f-x"}),
    )
    inferred = b.source(doc("lr", "loss_run"), "coverage_totals[0].covered")
    quoted = b.source(doc("lr", "loss_run"), "coverage_totals[0].coverage")
    assert inferred.verified and not quoted.verified


def test_a_misspelled_label_still_grounds_by_fuzzy_words() -> None:
    layout: dict[str, Any] = {
        "width": 800,
        "height": 600,
        "words": [
            [10, 10, 40, 20, "COVERAGE", 0, 0, 0],
            [60, 10, 90, 20, "AUTO", 0, 0, 1],
            [95, 10, 150, 20, "LIABLITY", 0, 0, 2],  # as printed
            [160, 10, 200, 20, "TOTAL", 0, 0, 3],
        ],
    }
    g = PageWords(layout).locate("Auto Liability", None)
    assert g.kind == "fuzzy" and g.bbox is not None
    assert PageWords(layout).locate("Hazardous Waste", None).kind == "none"  # not a match
    assert date(2026, 1, 1)  # keep the date import honest


@pytest.mark.parametrize(
    ("kind", "a", "b"),
    [
        ("company", "Bayline Insurance Agency Inc", "BAYLINE INSURANCE AGENCY, INC."),
        ("text", "Truck-Tractor", "truck tractor"),
        ("person", "O'Neil, Sean", "SEAN ONEIL"),
        ("text", "Lathrop", "LATHROP."),
    ],
)
def test_values_compare_without_punctuation_or_case(kind: str, a: str, b: str) -> None:
    x, y = compare(kind, a), compare(kind, b)
    assert x is not None and y is not None and x.key == y.key
    assert x.norm == a and y.norm == b  # what was printed is kept verbatim; display may recase


def test_a_value_not_on_the_page_does_not_ground_on_its_label() -> None:
    """The model once read 'Skyline Insurance Services Inc' with the quote 'Agency Name:'. The
    page says Bayline. Most words match, the value is not there: it must stay ungrounded."""
    words = [
        "Agency",
        "Name:",
        "Bayline",
        "Insurance",
        "Services",
        "Inc.",
        "Ph:",
        "(925)",
        "555-0172",
    ]
    layout: dict[str, Any] = {
        "width": 800,
        "height": 600,
        "words": [[10 + 60 * i, 10, 60 + 60 * i, 20, w, 0, 0, i] for i, w in enumerate(words)],
    }
    page = PageWords(layout)
    assert page.locate("Skyline Insurance Services Inc", "Agency Name:").kind == "none"
    assert page.locate("Bayline Insurance Services Inc.", "Agency Name:").kind != "none"


def test_an_unverified_license_number_matches_the_driver_by_name() -> None:
    docs = [doc("qf", "quote_form", "Quote form"), doc("mvr", "mvr", "MVR")]
    data: dict[str, Any] = {
        "qf": {"drivers": [{"name": "Ana Garcia", "license_number": "C0471925"}]},
        "mvr": {"driver_name": "GARCIA, ANA", "license_number": "C4471925"},
    }
    facts = {
        ("qf", "drivers[0].license_number"): "f-qf-lic",
        ("qf", "drivers[0].name"): "f-qf-name",
        ("mvr", "license_number"): "f-mvr-lic",
    }
    board = Builder(docs, data, facts, today=TODAY, ungrounded=frozenset({"f-qf-lic"})).build()
    finalize(board, today=TODAY)
    drivers = [e for e in board.entities.values() if e.kind == "driver"]
    assert len(drivers) == 1  # one person, not a second driver from a misread number
    lic = drivers[0].fields["license_number"]
    assert lic.state == "known" and lic.value and "C4471925" in lic.value.compared.display


def test_a_printed_yes_beside_a_commercial_license_label_is_a_cdl() -> None:
    from lane.resolve.build import commercial_flag

    assert commercial_flag("YES") is True and commercial_flag("No") is False
    assert commercial_flag("COMMERCIAL DRIVER LICENSE") is True
    assert commercial_flag("NON-COMMERCIAL") is False and commercial_flag("CLASS C") is None


def test_a_non_cdl_record_or_a_learners_permit_is_not_a_cdl() -> None:
    from lane.resolve.build import commercial_flag

    for printed in ("NON-CDL", "Non CDL", "NONCDL", "Noncommercial", "NON COMMERCIAL"):
        assert commercial_flag(printed) is False, printed
    for printed in ("CLP", "CDL PERMIT", "Commercial learner permit", "INSTRUCTION PERMIT"):
        assert commercial_flag(printed) is False, printed
    for printed in ("CDL", "CDL-A", "Commercial", "Class A CDL"):
        assert commercial_flag(printed) is True, printed


def test_the_state_printed_in_an_address_fills_an_unread_state_field() -> None:
    data: dict[str, Any] = {
        "app": {
            "mailing_address": "2207 Hillcrest Road, Green Bay, WI 54303",
            "mailing_state": None,
        }
    }
    facts = {("app", "mailing_address"): "f-addr"}
    board = Builder([doc("app", "application")], data, facts, today=TODAY).build()
    finalize(board, today=TODAY)
    f = board.entity("applicant", "applicant").fields["mailing_state"]
    assert f.state == "known" and f.value and f.value.compared.display == "WI"
    assert f.candidates[0].sources[0].fact_ids == ("f-addr",)  # evidence: the address
