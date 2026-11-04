from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from lane.documents.types import PageVerdict
from lane.domain import normalize as nz
from lane.extract.checks import ifta_checks, loss_run_checks, share_checks
from lane.extract.grounding import PageWords
from lane.extract.schemas import ApplicationSheetExtraction, Val
from lane.extract.sheets import Sheet, Workbook
from lane.llm.gateway import _strip_fences, blank_to_none, wire_schema

# --- normalization -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,024", Decimal("1024")),
        ("$2,936.92", Decimal("2936.92")),
        ("-$326.30", Decimal("-326.30")),
        ("$-326.30", Decimal("-326.30")),
        ("(12.50)", Decimal("-12.50")),
        ("0.2600", Decimal("0.2600")),
        ("CLASS C", None),
        ("", None),
    ],
)
def test_number(text: str, expected: Decimal | None) -> None:
    assert nz.number(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12/15/2026.", date(2026, 12, 15)),
        ("09/14/79", date(1979, 9, 14)),
        ("12/15/24", date(2024, 12, 15)),
        ("March 31, 2026", date(2026, 3, 31)),
        ("1979-09-14T00:00:00", date(1979, 9, 14)),
        ("06-02-2026", date(2026, 6, 2)),
        ("sometime", None),
    ],
)
def test_to_date(text: str, expected: date | None) -> None:
    assert nz.to_date(text) == expected


def test_percent_and_yes_no() -> None:
    assert nz.percent("15%") == 15 and nz.percent("0.25") == 25 and nz.percent("30") == 30
    assert nz.yes_no("Yes") is True and nz.yes_no("False") is False and nz.yes_no("maybe") is None


# --- grounding -----------------------------------------------------------------------------


def layout(*lines: tuple[float, list[str]]) -> dict[str, Any]:
    words: list[list[Any]] = []
    for line_no, (y, tokens) in enumerate(lines):
        x = 10.0
        for i, t in enumerate(tokens):
            words.append([x, y, x + 8 * len(t), y + 10, t, 0, line_no, i])
            x += 8 * len(t) + 4
    return {"unit": "pt", "words": words}


PAGE = layout(
    (100, ["Policy", "Number:", "GAL8100457"]),
    (120, ["TOTAL", "LOSSES", "PAID", "$24,959.00"]),
    (140, ["Arizona", "2.", "Diesel", "1,024", "1,024"]),
    (160, ["Alabama", "2.", "Diesel", "1,024", "6.11"]),
)


def test_value_inside_its_quote_is_located_exactly() -> None:
    g = PageWords(PAGE).locate("GAL8100457", "Policy Number: GAL8100457")
    assert g.kind == "exact" and g.bbox is not None and g.bbox[1] == 100


def test_repeated_values_are_disambiguated_by_the_row_quote() -> None:
    g = PageWords(PAGE).locate("1,024", "Alabama 2. Diesel")
    assert g.kind == "exact" and g.bbox is not None and g.bbox[1] == 160


def test_near_miss_is_fuzzy_and_absent_is_none() -> None:
    words = PageWords(PAGE)
    assert words.locate(None, "TOTAL LOSSES PAlD $24,959.00").kind == "fuzzy"
    assert words.locate("$99,999.00", "Nothing like this").kind == "none"


# --- worksheets ----------------------------------------------------------------------------


GRID = {
    "sheet": "Application Info",
    "merged": ["E5:H5"],
    "cells": [
        {"ref": "B5", "r": 5, "c": 2, "v": "Business Name"},
        {"ref": "E5", "r": 5, "c": 5, "v": "ACME HAULING INC"},
        {"ref": "A10", "r": 10, "c": 1, "v": "Name"},
        {"ref": "A11", "r": 11, "c": 1, "v": "Pat"},
        {"ref": "B11", "r": 11, "c": 2, "v": "1980-01-02T00:00:00"},
        {"ref": "A12", "r": 12, "c": 1, "v": "Lee"},
    ],
}


def test_cell_refs_resolve_through_merges_and_sheet_prefixes() -> None:
    book = Workbook([Sheet.from_grid(GRID, "p1")])
    for ref in ("G5", "'Application Info'!E5", "Application Info!e5"):
        cell = book.resolve(ref)
        assert cell is not None and cell.text == "ACME HAULING INC" and cell.ref == "E5"
    assert book.resolve("Z99") is None and book.resolve("not a ref") is None
    assert book.resolve("'Other'!A1") is None


def test_tables_read_rows_until_the_data_ends() -> None:
    book = Workbook([Sheet.from_grid(GRID, "p1")])
    rows = book.read_table(None, 11, None, {"name": "A", "dob": "B"})
    assert rows[0]["dob"].text == "1980-01-02T00:00:00" and rows[0]["dob"].ref == "B11"
    assert "dob" not in rows[1]  # empty cells are absent, never invented
    assert [r["name"].text for r in rows] == ["Pat", "Lee"]


# --- checks --------------------------------------------------------------------------------


def ifta(rows: list[dict[str, str]], totals: dict[str, str], **summary: str) -> dict[str, Any]:
    return {"rows": rows, "totals": totals, **{k: {"value": v} for k, v in summary.items()}}


ROWS = [
    {
        "jurisdiction": "AZ",
        "total_miles": "1,000",
        "taxable_gallons": "200",
        "net_taxable_gallons": "100",
        "rate": "0.2600",
        "tax": "$26.00",
    },
    {
        "jurisdiction": "KY",
        "total_miles": "500",
        "taxable_gallons": "100",
        "net_taxable_gallons": "50",
        "rate": "0.2200",
        "tax": "$11.00",
    },
    {
        "jurisdiction": "KY",
        "surcharge": "Surcharge",
        "total_miles": "0",
        "taxable_gallons": "100",
        "net_taxable_gallons": "0",
        "rate": "0.1050",
        "tax": "$10.50",
    },
]


def test_ifta_reconciles_with_surcharge_rows_handled() -> None:
    checks = ifta_checks(
        ifta(
            ROWS,
            {"total_miles": "1,500", "taxable_gallons": "300", "tax": "$47.50"},
            subtotal_due="$47.50",
            penalty="$2.00",
            interest="$0.50",
            total_due="$50.00",
        )
    )
    assert all(c.passed for c in checks), [c.detail for c in checks if not c.passed]


def test_ifta_flags_a_misread_cell() -> None:
    misread = [dict(ROWS[0], total_miles="1,800"), *ROWS[1:]]
    checks = {c.name: c for c in ifta_checks(ifta(misread, {"total_miles": "1,500"}))}
    assert not checks["sum_total_miles"].passed
    assert "rows[0].total_miles" in checks["sum_total_miles"].paths
    bad_tax = [dict(ROWS[0], tax="$62.00"), *ROWS[1:]]
    assert not {c.name: c for c in ifta_checks(ifta(bad_tax, {}))}["row_tax"].passed


def test_loss_run_checks() -> None:
    data = {
        "total_claims": {"value": "2"},
        "coverage_totals": [
            {"coverage": "Auto Liability", "losses_paid": "$30.00", "incurred": "$80.00"}
        ],
        "claims": [
            {
                "claim_number": "A",
                "total_paid": "$10.00",
                "total_incurred": "$10.00",
                "lines": [
                    {"paid": "$4.00", "incurred": "$4.00"},
                    {"paid": "$6.00", "incurred": "$6.00"},
                ],
            },
            {"claim_number": "B", "total_paid": "$20.00", "total_incurred": "$70.00", "lines": []},
        ],
    }
    assert all(c.passed for c in loss_run_checks(data))
    data["total_claims"] = {"value": "3"}
    assert not {c.name: c for c in loss_run_checks(data)}["claim_count"].passed


def test_share_checks() -> None:
    ok = {"radius": [{"label": "a", "percent": "0.4"}, {"label": "b", "percent": "0.6"}]}
    bad = {"commodities": [{"label": "a", "percent": "30"}, {"label": "b", "percent": "30"}]}
    assert share_checks(ok, "radius")[0].passed
    assert not share_checks(bad, "commodities")[0].passed


# --- wire schema ---------------------------------------------------------------------------


def test_wire_schema_requires_everything_and_keeps_field_names() -> None:
    schema = wire_schema(PageVerdict)
    assert "title" in schema["properties"] and set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["subject_id"] == {"type": "string"}
    assert schema["additionalProperties"] is False


def test_blanks_become_none_only_where_allowed() -> None:
    data = blank_to_none(PageVerdict, {"subject_id": "null", "title": "", "period": " "})
    assert data == {"subject_id": None, "title": "", "period": None}
    app = blank_to_none(
        ApplicationSheetExtraction, {"dba": "", "fein": {"cell": "", "value": "None"}}
    )
    assert app == {"dba": {}, "fein": {"cell": None, "value": None}}
    assert (
        Val.model_validate(blank_to_none(Val, {"value": "", "quote": "x", "page": 2})).value is None
    )


def test_fenced_json_is_unwrapped() -> None:
    assert _strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _strip_fences('{"a": 1}') == '{"a": 1}'
