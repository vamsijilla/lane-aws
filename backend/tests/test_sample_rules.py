"""The sample submission's resolution and rules, from the fixture: fast, no OCR or models."""

from tests.sample import board, raw, rules


def test_every_candidate_names_at_least_one_source_document() -> None:
    documents = {d["id"] for d in raw()["documents"]}
    bad = [
        f"{e.label}.{attr}: {c.compared.display}"
        for e in board().entities.values()
        for attr, f in e.fields.items()
        for c in f.candidates
        if not c.sources or not all(s.document_id in documents for s in c.sources)
    ]
    assert not bad, bad


def test_the_sample_routes_as_before() -> None:
    _, decision, _ = rules()
    assert decision.headline == "Refer to Small Fleet underwriting" and decision.potential_decline


def test_doc02_and_ins02_agree_on_liability_and_cargo() -> None:
    by_id, _, _ = rules()
    expected = [
        "Full cargo loss run for 2023-24; the letter shows paid losses only, no open claims or reserves",
        "Loss runs from Lakeshore Mutual for 2022-23, liability and cargo",
    ]
    assert by_id["DOC-02"].verdict.items == expected
    assert by_id["INS-02"].verdict.items == expected
    reason = by_id["DOC-02"].verdict.reason
    assert "2 of 3 prior years for auto liability" in reason and "for cargo" in reason
    assert not any(ch == "." for ch in reason)  # no decimals


def test_sensitivity_covers_only_fields_a_rule_judges() -> None:
    by_id, _, _ = rules()
    drv03 = by_id["DRV-03"]
    assert "owner drives" not in drv03.verdict.reason.lower()
    assert {s["field"] for s in drv03.sensitivity} == {"Date of birth"}
    for rid in ("DRV-02", "DRV-04", "DRV-05", "DRV-06"):
        assert all(s["field"] != "Owner drives" for s in by_id[rid].sensitivity), rid
    assert {s["field"] for s in by_id["DRV-01"].sensitivity} == {"Owner drives"}


def test_ins01_notes_that_it_passes_from_the_effective_date() -> None:
    by_id, _, _ = rules()
    ins01 = by_id["INS-01"]
    assert ins01.outcome.value == "refer"
    assert ins01.anchor_note and ins01.anchor_note.startswith(
        "Counting from the effective date: pass."
    )


def test_route01_is_small_fleet_under_4_5_and_6_units() -> None:
    by_id, decision, _ = rules()
    route = by_id["ROUTE-01"]
    assert route.outcome.value == "pass"
    assert [s["value"].split()[0] for s in route.sensitivity] == ["4", "5", "6"]
    assert {s["result"] for s in route.sensitivity} == {"Small Fleet underwriting"}
    final = by_id["ROUTE-02"]
    # The route sums up the rules; the potential decline is DRV-01's, never counted twice
    assert final.outcome.value == "refer" and not final.potential_decline
    assert decision.potential_decline and "Potential decline pending DRV-01" in final.verdict.reason
    assert final.verdict.reason.startswith("Refer to Small Fleet underwriting")
    assert final.verdict.items == decision.reasons
    assert list(by_id)[-2:] == ["ROUTE-01", "ROUTE-02"]


def test_the_agent_request_asks_every_open_conflict_and_missing_item() -> None:
    from lane.actions.build import conflict_details
    from lane.config import get_settings
    from tests.sample import conflicts_of, missing

    b = board()
    conflicts, attributes = conflicts_of(b)
    pairs = conflict_details(conflicts, attributes, get_settings().actions_conflict_questions)
    asks = [text for _key, text in pairs]
    text = "\n".join(asks + [m.detail for m in missing(b)])
    for needed in (
        "Does the owner drive? The documents show No and Yes. If not, name them as an excluded driver.",
        "What is the legal entity form",
        "refrigerated (reefer) units",
        "How many power units",
        "Kevin Brandt",
        "Peterbilt",
    ):
        assert needed in text, needed
    # Every conflict once, except one a deterministic reference settles (the VIN's own decode)
    settled = [c for c in conflicts if c.type == "reference"]
    assert len(asks) == len(conflicts) - len(settled) == len(set(asks))
    # Every ask is a plain question with the values: no "Label: about:" or "(stated)" labels
    for ask in asks:
        assert "?" in ask and "(stated)" not in ask, ask
    assert len({key for key, _t in pairs}) == len(pairs)  # one point per conflict
    # In the request, the unlisted unit and the power unit count are asked by the Peterbilt
    # missing item only, and owner drives by DRV-01's request
    s = get_settings()
    assert s.actions_conflicts_asked_by_missing == {
        "q_unlisted_vehicles": "VEH_UNLISTED",
        "power_units": "VEH_UNLISTED",
    }
    assert s.actions_conflicts_asked_by_rule == {"owner_drives": "DRV-01"}


def test_elig02_domicile_decides_and_the_miles_reading_flags() -> None:
    by_id, _, _ = rules()
    r = by_id["ELIG-02"]
    assert r.outcome.value == "flag" and not r.potential_decline
    assert "56% of IFTA miles are in 23 states" in r.verdict.reason
    assert r.verdict.items == ["Confirm where trucks are dispatched and garaged."]
    assert [x["outcome"] for x in r.readings] == ["flag"]  # the row never says decline


def test_doc04_flags_a_driver_found_only_in_loss_runs() -> None:
    by_id, _, b = rules()
    r = by_id["DOC-04"]
    assert r.outcome.value == "flag" and "Kevin Brandt" in r.verdict.reason
    assert r.spec.missing == ["DRV_UNLISTED"]
    from tests.sample import missing

    assert any(m.code.startswith("DRV_UNLISTED") for m in missing(b))


def _sorted_conflicts() -> list[tuple[str, str, bool]]:
    """(title, severity, blocking) in the order the conflicts page lists them."""
    from lane.config import get_settings
    from lane.resolve.severity import conflict_order, severity
    from tests.sample import conflicts_of

    by_id, _, b = rules()
    blocking = {f for r in by_id.values() for f in r.blocking_conflicts}
    decline_blocking = {f for r in by_id.values() if r.spec.decline for f in r.blocking_conflicts}
    conflicts, attributes = conflicts_of(b)
    from lane.resolve.service import stable_id
    from tests.sample import SID

    entity_kind = {stable_id(SID, kind, key): kind for (kind, key) in b.entities}
    rows = [
        (
            c.title,
            severity(
                get_settings(),
                entity_kind[c.entity_id],
                attributes[c.field_id],
                c.field_id in decline_blocking,
            ),
            c.field_id in blocking,
        )
        for c in conflicts
    ]
    return sorted(rows, key=lambda r: conflict_order(r[2], r[1], r[0]))


def test_conflicts_sort_blocking_then_high_medium_low() -> None:
    rows = _sorted_conflicts()
    assert rows[0] == ("Owner drives", "high", True)  # it changes DRV-01, which can decline
    levels = {t: s for t, s, _ in rows}
    assert {t for t, s in levels.items() if s == "high"} == {
        "Owner drives",
        "Has vehicles not listed on the application",
        "Power units",
        "Years of prior insurance",
    }
    assert {t for t, s in levels.items() if s == "low"} == {
        "Agency identity",
        "Date of birth: Luis Morales",
        "Make: 2020 Volvo (248615)",
        "Uninsured motorist limit",
    }
    order = [s for _, s, blocking in rows if not blocking]
    assert order == sorted(order, key=("high", "medium", "low").index)


def test_contacts_are_multi_valued_and_agency_is_one_identity_card() -> None:
    from tests.sample import conflicts_of

    b = board()
    conflicts, _ = conflicts_of(b)
    titles = {c.title for c in conflicts}
    assert "Agency phone" not in titles and "Agency name" not in titles
    card = next(c for c in conflicts if c.title == "Agency identity")
    assert card.hint == "Likely the same agency. Confirm the legal name for the producer record."
    assert any(
        n.startswith("Agency phone on file: (925) 555-0164, (925) 555-0172") for n in card.notes
    )
    phone = next(e for (k, _), e in b.entities.items() if k == "agency").fields["phone"]
    assert phone.state == "known" and len(phone.active) == 2


def test_ownership_change_is_a_note_on_the_legal_entity_card() -> None:
    from tests.sample import conflicts_of

    conflicts, _ = conflicts_of(board())
    assert not any("ownership" in c.title.lower() for c in conflicts)
    form = next(c for c in conflicts if c.title == "Legal entity form")
    assert form.notes and form.notes[0].startswith("Inferred from the legal entity conflict")


def test_um_limit_is_a_low_conflict() -> None:
    rows = _sorted_conflicts()
    assert ("Uninsured motorist limit", "low", False) in rows


def test_missing_items_are_tiered() -> None:
    from lane.config import get_settings
    from lane.resolve.tiers import tier_of
    from tests.sample import missing

    tiers = {m.code.split(":")[0]: tier_of(get_settings(), m.code) for m in missing(board())}
    assert tiers["APP_SIGNATURE"] == "bind"
    for code in ("APP_AUTHORITY", "APP_BUSINESS_START", "APP_HISTORY", "Q_ENGLISH_PROFICIENCY"):
        assert tiers[code] == "helpful", code
    assert tiers["APP_FEIN"] == "required" and tiers["DRV_UNLISTED"] == "required"


def test_doc01_collapses_stale_runs_into_one_line() -> None:
    by_id, _, _ = rules()
    note = by_id["DOC-01"].anchor_note or ""
    assert note.endswith(
        "Auto liability 2023-24, 2024-25, 2025-26 and cargo 2024-25, 2025-26: "
        "valued 70 days before the effective date"
    )


def test_quarters_and_names_read_the_same_everywhere() -> None:
    by_id, _, b = rules()
    assert (
        by_id["DOC-03"].verdict.reason == "Returns for Q3 2025, Q4 2025, Q1 2026, Q2 2026 were sent"
    )
    labels = {e.label for (k, _), e in b.entities.items() if k == "ifta_quarter"}
    assert "IFTA Q3 2025" in labels
    applicant = next(e for (k, _), e in b.entities.items() if k == "applicant")
    city = applicant.fields["mailing_city"].value
    assert city is not None and city.compared.display == "Lathrop"


def test_no_candidate_names_a_document_twice_and_titles_are_unique() -> None:
    b = board()
    for e in b.entities.values():
        for f in e.fields.values():
            for c in f.candidates:
                ids = [s.document_id for s in c.sources]
                assert len(ids) == len(set(ids)), (e.label, f.label, c.compared.display)
    titles: dict[str, str] = {}
    for e in b.entities.values():
        for src in e.sources.values():
            if src.document_id in {"vin-decode"}:
                continue
            assert titles.setdefault(src.title, src.document_id) == src.document_id, src.title
    from lane.db.models import Document
    from lane.documents.titles import refine_titles
    from tests.sample import raw

    skip = {"created_at", "updated_at"}
    docs = [Document(**{k: v for k, v in d.items() if k not in skip}) for d in raw()["documents"]]
    refine_titles(docs, raw()["extractions"])
    shown = [d.title for d in docs]
    assert len(shown) == len(set(shown)), shown
    assert "Auto liability loss run, Granite Peak Specialty, 2024-25" in shown
    assert "Cargo loss run, Granite Peak Specialty, 2024-25" in shown


def test_no_label_shows_question_marks() -> None:
    from datetime import date

    from lane.resolve.build import term_years

    assert term_years(None, None) == "dates not shown"
    assert term_years(date(2024, 1, 1), None) == "from 2024"
    assert term_years(None, date(2025, 1, 1)) == "to 2025"
    b = board()
    shown = [e.label for e in b.entities.values()] + [
        str(c.compared.display)
        for e in b.entities.values()
        for f in e.fields.values()
        for c in f.candidates
    ]
    assert not [t for t in shown if "?-?" in t or t.strip() == "?"]
