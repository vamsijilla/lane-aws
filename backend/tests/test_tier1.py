"""Tier 1 of classification: deterministic signals decide only on clear evidence."""

from typing import Any

from lane.documents.tier1 import decide
from lane.documents.types import DocType


def sheet(rows: list[list[object]]) -> dict[str, Any]:
    """A worksheet layout: one cell per non-empty value, 1-based rows and columns."""
    return {
        "cells": [
            {"r": r, "c": c, "v": v}
            for r, row in enumerate(rows, 1)
            for c, v in enumerate(row, 1)
            if v not in (None, "")
        ]
    }


def test_a_tab_with_driver_rows_and_a_placeholder_vehicle_block_is_a_driver_schedule() -> None:
    layout = sheet(
        [
            ["First Name", "Last Name", "DOB", "CDL Number", "CDL State", "Year", "Make", "VIN"],
            ["Ana", "Ruiz", "1980-01-02", "R1234567", "TX", "Vehicle list", "attached", "N/A"],
            ["Ben", "Ode", "1979-03-04", "O7654321", "NM", "", "", ""],
        ]
    )
    d = decide(text="", layout=layout, is_sheet=True, sheet_name="Drivers", sheet_hidden=False)
    assert d is not None and d.doc_type is DocType.DRIVER_SCHEDULE
    assert d.method == "sheet_headers"
    assert "vin" not in d.evidence  # its column holds only placeholders


def test_headers_without_a_clear_winner_leave_the_decision_to_the_next_tier() -> None:
    layout = sheet([["DOB", "VIN", "Name"], ["1980-01-02", "1FUJGLDR7CLBP8834", "Ana"]])
    assert decide(text="", layout=layout, is_sheet=True, sheet_name="S", sheet_hidden=False) is None


def test_a_hidden_sheet_of_list_values_is_lookup_values_and_no_document() -> None:
    layout = sheet([["Tractor"], ["Box truck"], ["Dry van trailer"]])
    d = decide(text="", layout=layout, is_sheet=True, sheet_name="Sheet3", sheet_hidden=True)
    assert d is not None and d.ignored and d.doc_type is None and d.method == "lookup_sheet"


def test_a_visible_sheet_named_like_a_lookup_is_lookup_values_too() -> None:
    layout = sheet([["CA"], ["TX"], ["AZ"]])
    d = decide(
        text="", layout=layout, is_sheet=True, sheet_name="Dropdown values", sheet_hidden=False
    )
    assert d is not None and d.ignored


def test_an_ordinary_visible_sheet_without_headers_is_not_called_a_lookup() -> None:
    layout = sheet([["Notes"], ["Call the agent on Monday"]])
    assert (
        decide(text="", layout=layout, is_sheet=True, sheet_name="Notes", sheet_hidden=False)
        is None
    )


def test_a_printed_form_identifier_decides() -> None:
    d = decide(
        text="ACORD 129 (2010/05) VEHICLE SCHEDULE",
        layout=None,
        is_sheet=False,
        sheet_name=None,
        sheet_hidden=False,
    )
    assert (
        d is not None and d.doc_type is DocType.VEHICLE_SCHEDULE and d.method == "form_identifier"
    )


def test_strong_key_phrases_decide_and_weak_ones_do_not() -> None:
    strong = (
        "IFTA quarterly fuel tax return. Jurisdiction summary. Taxable miles. Net taxable gallons."
    )
    d = decide(text=strong, layout=None, is_sheet=False, sheet_name=None, sheet_hidden=False)
    assert d is not None and d.doc_type is DocType.IFTA_RETURN and d.method == "key_phrases"
    weak = "Please see the attached. Fuel tax."
    assert (
        decide(text=weak, layout=None, is_sheet=False, sheet_name=None, sheet_hidden=False) is None
    )


def test_the_vocabulary_is_industry_wording_only() -> None:
    from lane.documents.tier1 import vocabulary

    v = vocabulary()
    words = " ".join(
        [f.name for f in v.form_identifiers]
        + [s for syns in v.sheet_headers.values() for s in syns]
        + v.lookup_sheet_names
    ).lower()
    for packet_word in ("cobalt", "granite", "lakeshore", "roadledger", "onward", "knight"):
        assert packet_word not in words
