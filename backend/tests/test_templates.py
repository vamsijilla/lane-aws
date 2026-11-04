"""Known layouts are recognised by their landmarks, deterministically, and only when sure."""

from typing import Any

from sqlalchemy.orm import Session

from lane.documents import templates as T
from lane.documents.template_tool import propose_anchors

LAYOUT = T.Template(
    id="t",
    name="Test loss run",
    doc_type=T.DocType.LOSS_RUN,
    status="active",
    page="first",
    anchors=[
        T.Anchor(text="Policy Number:", box=(0.05, 0.05, 0.2, 0.08), required=True),
        T.Anchor(text="Policy Period:", box=(0.05, 0.1, 0.2, 0.13)),
        T.Anchor(text="Run Date:", box=(0.6, 0.05, 0.75, 0.08)),
        T.Anchor(text="TOTAL CLAIMS:", box=(0.05, 0.2, 0.2, 0.23)),
        T.Anchor(text="LATE REPORTED CLAIMS:", box=(0.4, 0.2, 0.6, 0.23)),
    ],
    fields={
        "subject_id": [r"Policy Number:\s*\n?(?P<value>[A-Z0-9]{5,})"],
        "period": [r"Policy Period:\s*(?P<value>\d+/\d+/\d{4} to \d+/\d+/\d{4})"],
        "document_date": [
            r"Run Date:\s*\n?(?P<value>[A-Z][a-z]+ \d{1,2},\s?\d{4})",
            r"^(?P<value>[A-Z][a-z]+ \d{1,2},\s?\d{4})\s*\n\s*Run Date:",
        ],
    },
    required=["subject_id", "period"],
    title="Loss run, {carrier|company}, {period|years}",
)


def page(lines: list[tuple[str, float, float]], dx: float = 0, scale: float = 1) -> dict[str, Any]:
    """A layout like triage stores: words with boxes, on a 1000 x 1000 page."""
    words: list[list[Any]] = []
    for i, (text, x, y) in enumerate(lines):
        x0, y0 = (x + dx) * 1000 * scale, y * 1000 * scale
        words.append([x0, y0, x0 + 8 * len(text), y0 + 12, text, 0, i, 0])
    return {"unit": "pt", "width": 1000 * scale, "height": 1000 * scale, "words": words}


LINES = [
    ("Policy Number: GAL7102318", 0.06, 0.06),
    ("Policy Period: 12/15/2023 to 12/15/2024", 0.06, 0.11),
    ("Run Date: October 6,2026", 0.61, 0.06),
    ("TOTAL CLAIMS:", 0.06, 0.21),
    ("LATE REPORTED CLAIMS:", 0.41, 0.21),
]
TEXT = "\n".join(text for text, _, _ in LINES)


def match(layout: dict[str, Any], text: str = TEXT, templates: list[T.Template] | None = None):
    return T.match_page(templates or [LAYOUT], text=text, layout=layout)


def test_a_page_with_its_landmarks_in_place_matches_and_reads_its_fields() -> None:
    m = match(page(LINES))
    assert m is not None and m.hits == 5 and m.score == 1.0 and m.first_page is True
    assert m.fields == {
        "subject_id": "GAL7102318",
        "period": "12/15/2023 to 12/15/2024",
        "document_date": "2026-10-06",  # OCR's "October 6,2026" read as a date
    }
    assert not m.missing
    assert all(lm.box is not None for lm in m.landmarks)  # drawable in the lens


def test_small_shifts_and_rescans_still_match_but_a_different_layout_does_not() -> None:
    assert match(page(LINES, dx=0.03, scale=1.5)) is not None  # nudged and at a different size
    assert match(page(LINES, dx=0.3)) is None  # the same words elsewhere: another layout


def test_too_few_landmarks_or_a_missing_required_one_is_no_match() -> None:
    assert match(page(LINES[:3])) is None  # 3 of 5 is under the 0.8 threshold
    without_policy = [ln for ln in LINES if not ln[0].startswith("Policy Number")]
    assert match(page(without_policy)) is None


def test_words_that_must_not_appear_rule_a_layout_out() -> None:
    picky = LAYOUT.model_copy(update={"absent": ["loss history letter"]})
    assert match(page(LINES), TEXT + "\nLoss History Letter", [picky]) is None


def test_a_value_printed_before_its_label_is_still_read() -> None:
    lines = [ln for ln in LINES if not ln[0].startswith("Run Date")]
    text = TEXT.replace("Run Date: October 6,2026", "October 6, 2026\nRun Date:")
    m = match(page([*lines, ("Run Date:", 0.61, 0.06)]), text)
    assert m is not None and m.fields["document_date"] == "2026-10-06"


def test_a_missing_required_field_hands_the_page_to_the_model() -> None:
    m = match(page(LINES), TEXT.replace("12/15/2023 to 12/15/2024", "see attached"))
    assert m is not None and m.missing == ("period",)


def test_markers_and_sheets() -> None:
    acord = T.Template(
        id="a",
        name="ACORD 125",
        doc_type=T.DocType.APPLICATION,
        kind="marker",
        status="active",
        markers=[r"\bACORD\s*125\b"],
    )
    hit = T.match_page([acord], text="ACORD 125 (2016/03) COMMERCIAL INSURANCE", layout=None)
    assert hit is not None and hit.reason.startswith("Printed form identifier")
    assert T.match_page([acord], text="ACORD 1250", layout=None) is None
    sheet = T.Template(
        id="s",
        name="Drivers",
        doc_type=T.DocType.DRIVER_SCHEDULE,
        kind="sheet",
        status="active",
        sheet_name=r"^driver_export",
        anchors=[
            T.Anchor(text=t) for t in ("Driver #", "First Name", "Last Name", "Date of Birth")
        ],
    )
    cells = {"cells": [{"v": v} for v in ("Driver #", "First Name", "Last Name", "Date of Birth")]}
    assert T.match_page([sheet], text="", layout=cells, sheet_name="driver_export2026") is not None
    assert T.match_page([sheet], text="", layout=cells, sheet_name="Vehicles") is None


def test_titles_drop_what_could_not_be_read() -> None:
    fields = {
        "carrier": "GRANITE PEAK SPECIALTY INSURANCE COMPANY",
        "period": "12/15/2023 to 12/15/2024",
    }
    assert (
        T.render_title(LAYOUT.title or "", fields) == "Loss run, Granite Peak Specialty, 2023-2024"
    )
    assert T.render_title(LAYOUT.title or "", {"period": "12/15/2023 to 12/15/2024"}) == (
        "Loss run, 2023-2024"
    )
    assert T.render_title("IFTA return, {period|quarter}", {"period": "March 31, 2026"}) == (
        "IFTA return, Q1 2026"
    )
    assert (
        T.render_title("MVR, {name|person}", {"name": "OSEI, NATHANIEL K"})
        == "MVR, Osei, Nathaniel K"
    )


def test_no_layout_ships_and_a_new_database_has_none(db: Session) -> None:
    """Layouts are learned only from documents a person confirmed, never seeded from a packet."""
    assert not T.TEMPLATE_DIR.exists() or not list(T.TEMPLATE_DIR.glob("*.yaml"))
    from lane.documents.layouts import all_layouts

    assert all_layouts(db) == []


def test_proposed_landmarks_are_labels_not_values() -> None:
    layout = page(
        [
            ("Policy Number:", 0.05, 0.05),
            ("GAL7102318", 0.25, 0.05),
            ("Name Of Insured:", 0.05, 0.1),
            ("COBALT MESA TRANSPORT INC", 0.25, 0.1),
            ("TOTAL INCURRED", 0.05, 0.3),
            ("$1,250.00", 0.25, 0.3),
        ]
    )
    texts = [a.text for a in propose_anchors(layout, ["COBALT MESA TRANSPORT INC"])]
    assert texts == ["Policy Number:", "Name Of Insured:", "TOTAL INCURRED"]
