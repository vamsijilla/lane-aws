"""Every age counts from the application date, and a masked date-of-birth conflict says which
part differs without revealing the date."""

from datetime import date

from lane.api.privacy import Masker
from lane.config import get_settings
from lane.domain.ages import age_on, difference, masked_candidate
from lane.rules.model import Outcome
from tests.test_rules import ViewBuilder, clean_submission, driver, results_of

APP = date(2026, 10, 22)


def test_age_counts_from_the_application_date_even_when_rules_count_from_another() -> None:
    b = ViewBuilder()
    clean_submission(b)
    # 23 on Nov 1, 2026: after the application date, before the effective date (Dec 15)
    driver(b, "young", dob="2003-11-01")
    by_id, _ = results_of(b.view)
    drv03 = by_id["DRV-03"]
    assert drv03.outcome is Outcome.REFER and drv03.verdict.items == ["Young: 22"]
    assert drv03.anchor_note is None  # the effective date does not make them 23


def test_the_difference_names_only_the_parts_that_differ() -> None:
    oct3, oct30 = date(1991, 10, 3), date(1991, 10, 30)
    assert difference([oct30, oct3]) == "Month and day differ: Oct 3 vs Oct 30, year hidden"
    assert difference([date(1991, 10, 3), date(1992, 10, 3)]) == (
        "Year differs: 1991 vs 1992, month and day hidden"
    )
    assert difference([date(1991, 10, 3), date(1991, 11, 3)]) == (
        "Month and day differ: Oct 3 vs Nov 3, year hidden"
    )
    both = difference([date(1991, 10, 3), date(1992, 1, 9)], APP)
    assert both == "Every part of the date differs (age 35 vs age 34); the dates stay hidden"
    assert difference([oct3, oct3]) is None
    assert masked_candidate(oct3, ["month and day"], APP) == "Oct 3, ••••"


def test_masked_conflict_candidates_stay_distinct() -> None:
    mask = Masker(get_settings(), APP)
    cands = [
        {"value": "1991-10-03", "display": "10/03/1991"},
        {"value": "1991-10-30", "display": "10/30/1991"},
    ]
    shown = [c["display"] for c in mask.candidates("date_of_birth", cands)]
    assert shown == ["Oct 3, ••••", "Oct 30, ••••"]
    assert "1991" not in " ".join(shown)
    assert mask.difference("date_of_birth", cands) == (
        "Month and day differ: Oct 3 vs Oct 30, year hidden"
    )
    one = mask.candidates("date_of_birth", cands[:1])
    assert one[0]["display"] == "Oct 3, ••••"  # one value: the year hidden
    assert age_on(date(1991, 10, 30), APP) == 34  # birthday after the application date
