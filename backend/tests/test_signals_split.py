import pytest

from lane.db.models import ConfidenceBand
from lane.documents.signals import filename_hint, keyword_vote
from lane.documents.split import PageCall, mark_supersession, norm_id, split_pages
from lane.documents.types import DocType


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("STATE Driver Record  Violations/Convictions  Suspensions/Revocations", DocType.MVR),
        (
            "Jurisdiction Summary  Taxable Miles  Tax Paid Gal  IFTA Carrier Tax",
            DocType.IFTA_RETURN,
        ),
        ("Claim Number  Date of Loss  TOTAL INCURRED  Run Date", DocType.LOSS_RUN),
        ("To Whom It May Concern: There have been no paid losses", DocType.LOSS_HISTORY_LETTER),
        ("Vehicle # Year Make Model VIN Cost New", DocType.VEHICLE_SCHEDULE),
        ("Hello, please find attached", None),
    ],
)
def test_keyword_vote(text: str, expected: DocType | None) -> None:
    assert keyword_vote(text).doc_type == expected


@pytest.mark.parametrize(
    ("name", "hint"),
    [
        ("MVR REPORT - ACME.pdf", DocType.MVR),
        ("Q3 2025.pdf", DocType.IFTA_RETURN),
        ("IFTA_2026Q1.pdf", DocType.IFTA_RETURN),
        ("AL 24-25.pdf", DocType.LOSS_RUN),
        ("MTC 23-24.pdf", DocType.LOSS_RUN),
        ("Loss Runs 2024.pdf", DocType.LOSS_RUN),
        ("Drivers.xlsx", DocType.DRIVER_SCHEDULE),
        ("Fleet schedule.xlsx", DocType.VEHICLE_SCHEDULE),
        ("ACME TRANSPORT - CARRIER.xlsx", None),
    ],
)
def test_filename_hint(name: str, hint: DocType | None) -> None:
    assert filename_hint(name) == hint


def page(
    no: int,
    doc_type: str,
    *,
    att: str = "a",
    new: bool = False,
    sid: str | None = None,
    period: str | None = None,
    date: str | None = None,
    sheet: bool = False,
) -> PageCall:
    return PageCall(
        page_id=f"{att}{no}",
        attachment_id=att,
        page_no=no,
        doc_type=doc_type,
        starts_new_document=new,
        subject_id=sid,
        subject_name=None,
        period=period,
        document_date=date,
        title=doc_type,
        confidence=ConfidenceBand.HIGH,
        is_sheet=sheet,
    )


def test_split_follows_headers_type_changes_and_identifiers() -> None:
    pages = [
        page(1, "mvr", new=True, sid="C111"),
        page(2, "mvr", new=True, sid="W222"),
        page(3, "mvr", new=False),  # "END OF REPORT" continuation
        page(4, "mvr", new=False, sid="X333"),  # missing header, but a different license
        page(5, "drivers_license", new=False, sid="X333"),
        page(1, "loss_run", att="b", new=False),  # a new file always starts a document
    ]
    docs = split_pages(pages)
    assert [[p.page_id for p in d.pages] for d in docs] == [
        ["a1"],
        ["a2", "a3"],
        ["a4"],
        ["a5"],
        ["b1"],
    ]


def test_worksheets_of_one_type_stay_one_document() -> None:
    docs = split_pages(
        [
            page(1, "application", new=True, sheet=True),
            page(2, "application", new=True, sheet=True),
            page(3, "other", new=True, sheet=True),
        ]
    )
    assert [len(d.pages) for d in docs] == [2, 1]


def test_older_copy_of_the_same_record_is_superseded() -> None:
    docs = split_pages(
        [
            page(1, "mvr", new=True, sid="W 3821-56", date="2026-10-09"),
            page(2, "mvr", new=True, sid="w382156", date="2025-09-24"),
            page(3, "mvr", new=True, sid="C111", date="2026-10-09"),
        ]
    )
    mark_supersession(docs)
    assert [d.superseded_by for d in docs] == [None, "a:1", None]


def test_different_periods_are_not_copies() -> None:
    docs = split_pages(
        [
            page(1, "ifta_return", att="q1", new=True, sid="247-1", period="Q1", date="2026-03-31"),
            page(1, "ifta_return", att="q2", new=True, sid="247-1", period="Q2", date="2026-06-30"),
        ]
    )
    mark_supersession(docs)
    assert all(d.superseded_by is None for d in docs)


def test_copies_without_comparable_dates_go_to_review() -> None:
    docs = split_pages(
        [
            page(1, "loss_run", att="x", new=True, sid="P1"),
            page(1, "loss_run", att="y", new=True, sid="P1", date="2026-01-01"),
        ]
    )
    mark_supersession(docs)
    assert all(d.superseded_by is None and d.review for d in docs)


def test_norm_id() -> None:
    assert norm_id(" v6193 02874-15503 ") == "V61930287415503"
    assert norm_id("--") is None and norm_id(None) is None
