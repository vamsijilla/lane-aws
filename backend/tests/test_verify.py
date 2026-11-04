"""The second read: code reads each value again from the page, anchored to its label, its
record or its cell, and compares by type. Plus the catalog's validators and recall checks."""

from datetime import date
from typing import Any

from lane.extract.validators import recall_checks, run_validators
from lane.extract.verify import Page, Want, compare, is_short, locate


def page(rows: list[list[tuple[str, float]]], height: float = 10.0) -> Page:
    """Rows of (word, x) on a page; each row 14 points below the last."""
    words: list[list[Any]] = []
    for r, row in enumerate(rows):
        for i, (text, x) in enumerate(row):
            y = 20 + r * 14
            words.append([x, y, x + 7 * len(text), y + height, text, 0, r, i])
    found = Page.from_layout({"words": words})
    assert found is not None
    return found


def test_a_value_beside_its_label_is_read_twice() -> None:
    p = page(
        [
            [
                ("Policy", 10),
                ("Number:", 60),
                ("GAL7102318", 130),
                ("Run", 400),
                ("Date:", 430),
                ("10/06/2026", 480),
            ]
        ]
    )
    got = locate(p, Want("identifier", "GAL7102318", ("Policy Number",)))
    assert got.verified and got.kind == "label" and got.where == "same line"
    got = locate(p, Want("date", "10/06/2026", ("Run Date",)))
    assert got.verified and got.read == "2026-10-06"


def test_a_value_under_its_label_is_read_twice() -> None:
    p = page([[("License:", 10)], [("M5520814", 10)]])
    assert locate(p, Want("identifier", "M5520814", ("License",))).where == "below"


def test_a_different_value_beside_the_label_is_a_disagreement() -> None:
    p = page([[("DOB:", 10), ("10/30/1991", 60)]])
    got = locate(p, Want("date", "10/03/1991", ("DOB",)))
    assert got.mismatch and not got.verified and got.read == "1991-10-30"


def test_numbers_never_match_loosely_but_identifiers_fold_look_alikes() -> None:
    assert not compare("money", "$7,454.00", "Total Paid $7,545.00").equal
    assert compare("money", "$7,454.00", "Total Paid 7454").equal
    folded = compare("identifier", "C4471925", "License: C447I925")
    assert folded.equal and folded.folding


def test_short_values_never_ground_anywhere_on_the_page() -> None:
    p = page([[("Status:", 10), ("Open", 70)], [("Owner", 10), ("Yes", 70)]])
    assert is_short("enum", "Yes") and is_short("number", "4")
    got = locate(p, Want("enum", "Yes", ("Driver Owner",)))
    assert got.kind == "none"  # its label is not on the page; "Yes" elsewhere does not count


def test_a_record_is_read_inside_its_own_block() -> None:
    p = page(
        [
            [("Claim", 10), ("Number:", 50), ("GA1", 110)],
            [("Total", 10), ("Paid", 50), ("$7,454.00", 110)],
            [("Claim", 10), ("Number:", 50), ("GA2", 110)],
            [("Total", 10), ("Paid", 50), ("$17,505.00", 110)],
        ]
    )
    first = Want("money", "$7,454.00", ("Total Paid",))
    assert locate(p, first, row_quote="Claim Number: GA1", next_quote="Claim Number: GA2").verified
    second = Want("money", "$7,454.00", ("Total Paid",))
    got = locate(p, second, row_quote="Claim Number: GA2")
    assert not got.verified  # the first claim's total does not verify the second claim's


def test_validators_and_recall() -> None:
    today = date(2026, 10, 22)
    data: dict[str, Any] = {
        "total_claims": {"value": "3"},
        "claims": [
            {"claim_number": "A", "date_of_loss": "02/10/2025", "date_reported": "01/10/2025",
             "total_paid": "100", "total_reserves": "50", "total_incurred": "200", "vin": "1XPBD49X5MD73520"},
            {"claim_number": "B"},
        ],
    }  # fmt: skip
    failed = {c.name for c in run_validators("loss_run", data, today) if not c.passed}
    assert {
        "validator:report_after_loss",
        "validator:paid_reserved_incurred",
        "validator:vin",
    } <= failed
    [recall] = recall_checks("loss_run", data, "")
    assert not recall.passed and "3 claims" in recall.detail


def test_the_label_the_model_quoted_anchors_a_layout_the_catalog_does_not_know() -> None:
    p = page(
        [
            [("State", 10), ("minimum", 60), ("Motor", 130), ("truck", 180)],
            [("Garaging", 10), ("address:", 70), ("12", 140), ("Main", 160), ("St,", 200)],
            [("Policy:", 10), ("LCA-2023-4471", 70)],
        ]
    )
    want = Want("enum", "WI", ("State",), state=True)
    got = locate(p, Want("identifier", "LCA-2023-4471", ()), quote="Policy: LCA-2023-4471")
    assert got.verified and got.label == "Policy"
    # Not on the page beside the quoted label: no verification from the quote alone
    got = locate(p, Want("identifier", "LCA-2099-0000", ()), quote="Policy: LCA-2099-0000")
    assert not got.verified
    assert not locate(p, want, quote="Garaging address: 12 Main St, Green Bay, WI").verified
