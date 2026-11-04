"""Printed text tidied for the screens; the lens keeps it as printed."""

from lane.api import display as d


def test_claim_text_reads_as_words() -> None:
    assert d.claim("description", "IV Rear-Ended Cv at Signal") == (
        "Insured vehicle rear-ended claimant vehicle at signal"
    )
    assert d.claim("description", "** IV STRUCK CV **") == "Insured vehicle struck claimant vehicle"
    assert d.claim("location", "PINAL COUNTY , AZ") == "Pinal County, AZ"
    assert d.claim("vehicle", "2021 - Peterbilt -") == "2021 Peterbilt"


def test_one_name_per_carrier() -> None:
    assert d.field("coverage_term", "carrier", "Granite Peak Specialty Insurance Company") == (
        d.field("coverage_term", "carrier", "GRANITE PEAK SPECIALTY")
    )


def test_mvr_notes_and_events() -> None:
    assert d.mvr_note("VIOL MISC. DATED: 06/07/2023 - THE EVENT IS ACTIVE") == (
        "Violation: the event is active"
    )
    assert d.mvr_note("08/19/2026 CDL CLEARANCE NOTICE AC") == "CDL clearance notice"
    assert d.mvr_note("05/28/2022 CDL TRANSFER FROM PA AC") == "CDL transfer from PA"
    assert d.mvr_event("** ACCIDENT **") == "Accident"
    assert d.mvr_event("SPEEDING 15-29 MPH OVER LIMIT") == "Speeding 15-29 MPH over limit"
    assert d.place("PENNSYLVANIA") == "Pennsylvania" and d.place("NJ") == "NJ"


def test_claim_places_spell_out_county_and_names_drop_trailing_punctuation() -> None:
    from lane.api.display import claim
    from lane.resolve.compare import compare

    assert claim("location", "PINAL CO, AZ") == "Pinal County, AZ"
    assert claim("location", "Mesa, AZ") == "Mesa, AZ"
    shown = compare("company", "Bayline Insurance Services Inc.")
    assert shown is not None and shown.display == "Bayline Insurance Services Inc"


def test_an_issue_date_is_the_latest_issue_and_mvr_history_gives_the_earliest_cdl() -> None:
    from datetime import date

    from lane.domain.cdl import describe, from_history

    one = (date(2021, 1, 6), "license card")
    # Only an issue date: said as the issue date on record, which may postdate the CDL
    assert describe(one, None, one) == (
        "CDL issue date on record: Jan 6, 2021 (license card; a renewal or duplicate resets "
        "this date, so the CDL may be older)"
    )
    history = (date(2020, 5, 1), "MVR history: CDL issued")
    later = (date(2024, 3, 18), "MVR")
    assert describe(one, history, later) == (
        "Earliest CDL evidence: May 1, 2020 (MVR history: CDL issued). "
        "Latest CDL issue date: Mar 18, 2024 (may be a renewal or duplicate)"
    )
    mvrs = [
        {
            "events": [
                {
                    "kind": "cdl_renewed",
                    "event_date": "06/14/2022",
                    "description": "CDL RENEWAL NOTICE AC",
                }
            ],
            "notes": [{"note_date": "10/04/2021", "text": "CDL CLEARANCE NOTICE AC"}],
        }
    ]
    assert describe(None, from_history(mvrs)) == (
        "Earliest CDL evidence: Oct 4, 2021 (MVR history: CDL clearance notice)"
    )
