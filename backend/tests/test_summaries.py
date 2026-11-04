"""Screen summaries follow the ruleset, not numbers of their own."""

from datetime import date
from typing import Any

from lane.api.models import CandidateOut, SourceOut
from lane.api.summaries import carrier_key, summarize
from lane.config import Settings
from lane.db.models import ResolvedEntity, ResolvedField, RuleOutcomeRecord
from lane.overview.service import headline
from lane.rules import points
from lane.rules.service import ruleset

ANCHOR = date(2026, 10, 22)
PARAMS = ruleset().params


def _entity(kind: str, details: dict[str, Any], eid: str = "e1", key: str = "k") -> ResolvedEntity:
    return ResolvedEntity(id=eid, kind=kind, key=key, label="Pat Doe", details=details, found_in=[])


def _field(
    attr: str, value: Any, display: str | None = None, state: str = "known"
) -> ResolvedField:
    return ResolvedField(
        id=f"f-{attr}",
        entity_id="e1",
        attribute=attr,
        label=attr,
        state=state,
        value=value,
        value_display=display if display is not None else str(value),
        candidates=[],
        notes=[],
        invalid=[],
    )


def _summarize(entity: ResolvedEntity, fields: list[ResolvedField], **shown: Any) -> Any:
    return summarize(
        [entity],
        {entity.id: fields},
        {entity.id: shown},
        params=PARAMS,
        anchor=ANCHOR,
        ages_on=ANCHOR,
        settings=Settings(),
    )[entity.id]


def _mvr(*events: dict[str, Any], order: str = "2026-10-09") -> dict[str, Any]:
    return {"document_id": "d", "title": "MVR", "order_date": order, "events": list(events)}


def test_carrier_key_ignores_company_suffixes() -> None:
    assert carrier_key("ACME CASUALTY INSURANCE COMPANY") == carrier_key("Acme") == "Acme"
    assert carrier_key(None) == "Carrier not shown"


def test_points_windows_count_back_from_the_anchor() -> None:
    inside = {"event_date": "2023-10-23", "points": "2"}  # just inside 3 years
    outside = {"event_date": "2023-10-01", "points": "3"}
    recent = {"event_date": "2026-01-05", "points": "1"}
    p = points.count([inside, outside, recent], ANCHOR)
    assert (p.three_years, p.twelve_months) == (3, 1)
    assert p.outside == [(date(2023, 10, 1), 3)]


def test_two_abstracts_of_one_license_do_not_double_count() -> None:
    speeding = {"event_date": "2025-06-01", "description": "Speeding 15 over", "points": "2"}
    events = points.events([_mvr(speeding), _mvr(dict(speeding), order="2026-01-01")])
    assert len(events) == 1
    assert points.count(events, ANCHOR).three_years == 2


def test_driver_badges_use_the_rule_thresholds() -> None:
    max3, max1 = int(PARAMS["max_points_3y"]), int(PARAMS["max_points_12m"])
    within = [
        {"event_date": "2024-02-01", "description": f"v{i}", "points": "1"} for i in range(max3)
    ]
    s = _summarize(_entity("driver", {"mvrs": [_mvr(*within)], "scheduled": True}), [])
    assert s.driver.points_3y == max3
    assert next(b for b in s.driver.badges if "points" in b.text).tone == "neutral"

    over = [
        {"event_date": "2026-09-01", "description": f"v{i}", "points": "1"} for i in range(max1 + 1)
    ]
    s = _summarize(_entity("driver", {"mvrs": [_mvr(*over)]}), [])
    assert s.driver.points_12m == max1 + 1
    assert next(b for b in s.driver.badges if "points" in b.text).tone == "warning"


def test_mvr_age_and_driver_age_follow_the_params() -> None:
    old = ANCHOR.replace(year=ANCHOR.year - 1).isoformat()
    young = ANCHOR.replace(year=ANCHOR.year - int(PARAMS["min_driver_age"]) + 1).isoformat()
    s = _summarize(_entity("driver", {"mvrs": [_mvr(order=old)]}), [_field("date_of_birth", young)])
    assert s.driver.mvr_age_days == (ANCHOR - date.fromisoformat(old)).days
    tones = {b.text.split()[0]: b.tone for b in s.driver.badges}
    assert tones["MVR"] == "stop" and tones["Age"] == "warning"


def test_source_rows_mark_disagreeing_documents() -> None:
    def cand(value: str, doc_type: str, superseded: str | None = None) -> CandidateOut:
        return CandidateOut(
            display=value,
            value=value,
            sources=[SourceOut(document_id="d", doc_type=doc_type, title="t")],
            superseded=superseded,
        )

    s = _summarize(
        _entity("driver", {}),
        [],
        license_class=[cand("A", "driver_schedule"), cand("C", "mvr")],
        license_state=[cand("CA", "driver_schedule"), cand("NV", "mvr", superseded="newer MVR")],
    )
    rows = {r.attribute: r for r in s.driver.source_rows}
    assert rows["license_class"].differs
    assert rows["license_class"].columns == {"schedule": ["A"], "mvr": ["C"]}
    assert not rows["license_state"].differs
    assert rows["license_state"].columns["mvr"] == ["NV (superseded)"]


def test_claim_report_lag() -> None:
    claim = _entity("claim", {"driver_reported": False})
    s = _summarize(
        claim, [_field("date_of_loss", "2025-03-01"), _field("date_reported", "2025-04-01")]
    )
    assert s.claim.report_lag_days == 31 and s.claim.reported_late
    assert s.claim.driver_reported is False and s.claim.unit_reported is None


def test_potential_decline_headline_comes_from_the_declining_reading() -> None:
    r = RuleOutcomeRecord(
        reason="California, from the mailing address. Potential decline under the other reading",
        potential_decline=True,
        readings=[
            {
                "id": "r",
                "label": "x",
                "assumption": "A03",
                "outcome": "decline",
                "reason": "56% of miles are outside the program list (largest: Nevada 1 mi)",
            },
        ],
    )
    assert headline(r) == "56% of miles are outside the program list (largest: Nevada 1 mi)"
    r.potential_decline = False
    assert headline(r) == "California, from the mailing address"


def test_commodity_labels_printed_in_capitals_are_shown_in_title_case() -> None:
    from lane.api.summaries import _shares  # pyright: ignore[reportPrivateUsage]

    out = _shares(
        {"items": [{"label": "FRESH PRODUCE / FROZEN FOOD", "percent": "30"}]}, "quote", None
    )
    assert out is not None and out.items[0].label == "Fresh Produce / Frozen Food"
