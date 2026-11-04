"""Resolution rules that need the whole submission: supersession, validators, mappings between
differently shaped forms, derived conflicts, and aggregates for the rules engine.

Every derived candidate names the documents it rests on, so a person can check the reasoning.
"""

import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, cast

from lane.domain import geo
from lane.domain.taxonomy import (
    REEFER_COMMODITIES,
    UM_STATE_MINIMUMS,
    commodity_class,
    commodity_title,
)
from lane.resolve.board import Board, Candidate, Entity, Field, Source, derived_from, resolve_field
from lane.resolve.compare import compare, person_key

# (entity kind, attribute) -> how to resolve it
SUPERSEDE_BY_DATE = {
    ("driver", "license_expiration"),
    ("driver", "license_issued"),
    ("driver", "license_status"),
    ("driver", "license_class"),
    ("driver", "commercial_license"),
}
CONFLICT_TYPES = {
    ("driver", "name"): "identity",
    ("applicant", "legal_name"): "identity",
    ("applicant", "entity_form"): "identity",
    ("agency", "name"): "identity",
    ("agency", "phone"): "identity",
    ("vehicle", "make"): "reference",
    ("vehicle", "year"): "reference",
    ("fleet", "power_units"): "cardinality",
}
HINTS = {
    (
        "driver",
        "date_of_birth",
    ): "The MVR is the state's record of the driver; schedules are typed by the agency.",
    (
        "driver",
        "name",
    ): "Check the license number: the same number on both documents means the same person.",
    (
        "applicant",
        "entity_form",
    ): "Verify the legal entity against state business records and the FMCSA registration before quoting.",
    (
        "applicant",
        "legal_name",
    ): "Use the name on the signed application and the FMCSA registration.",
    ("agency", "name"): "Usually the same agency under two names; confirm with the agent.",
    ("agency", "phone"): "Confirm the agency's contact number.",
    (
        "coverage_term",
        "carrier",
    ): "The carrier's own loss document names the carrier that wrote the policy.",
    (
        "fleet",
        "power_units",
    ): "Ask the agent for a complete schedule; vehicles in claims but not on the schedule suggest it is incomplete.",
    (
        "operations",
        "owner_drives",
    ): "If the owner drives, they must meet the driver criteria or be excluded by name.",
    (
        "operations",
        "refrigerated",
    ): "Reefer breakdown cover only matters for refrigerated trailers; confirm reefer units.",
    ("operations", "farthest_territory"): "Fuel tax returns record where the trucks actually ran.",
    ("operations", "q_unlisted_vehicles"): "A vehicle in a recent claim is not on the schedule.",
    (
        "coverage_history",
        "physical_damage_carried",
    ): "Physical damage may have been written on a separate policy; ask for it.",
    (
        "applicant",
        "years_prior_insurance",
    ): "Documented terms come from the prior insurance table and loss runs.",
}
MVR_HINT_TYPES = {"mvr"}


def finalize(board: Board, *, today: date) -> None:
    add_vehicle_validation(board)
    add_derived(board, today=today)
    reefer_commodities(board)
    for (kind, _key), entity in board.entities.items():
        for attr, f in entity.fields.items():
            resolve_field(
                f,
                supersede_by_date=(kind, attr) in SUPERSEDE_BY_DATE,
                conflict_type=CONFLICT_TYPES.get((kind, attr), "value"),
                hint=_hint(kind, attr, entity),
            )
    reconcile_um(board)
    reconcile_commodities(board)
    merge_contact_fields(board)
    agency_identity(board)
    entity_form_notes(board)


def reefer_commodities(board: Board) -> None:
    """Fresh produce or frozen food among the commodities is evidence of refrigerated units."""
    ops = board.entity("operations", "operations")
    for detail in ("commodities_application", "commodities_quote"):
        listed = cast(dict[str, Any] | None, ops.details.get(detail))
        if not listed:
            continue
        for item in cast(list[dict[str, Any]], listed["items"]):
            label = str(item["label"])
            if not REEFER_COMMODITIES.search(label):
                continue
            share = f" ({item['percent']}%)" if item.get("percent") else ""
            board.add(
                ops,
                "refrigerated",
                True,
                Source(
                    str(listed["document_id"]),
                    "application" if detail == "commodities_application" else "quote_form",
                    str(listed["title"]),
                    tuple(item.get("fact_ids") or ()),
                    note=f"Hauls {label}{share}, which usually needs refrigerated trailers",
                ),
                kind="bool",
                label="Refrigerated operations",
            )


def merge_contact_fields(board: Board) -> None:
    """Phone, fax and email can each have several valid values: kept, never a conflict."""
    from lane.config import get_settings

    multi = set(get_settings().multi_valued_attributes)
    for entity in board.entities.values():
        for attr, f in entity.fields.items():
            if attr in multi and f.state == "conflict":
                shown = ", ".join(str(c.compared.display) for c in f.active)
                f.state, f.resolution, f.conflict = "known", "multiple", None
                f.notes.append(f"Several on file: {shown}")


def agency_identity(board: Board) -> None:
    """Two agency names (and their numbers) are one question: which legal name to record."""
    agency = next(iter(board.of_kind("agency")), None)
    name = agency.fields.get("name") if agency else None
    if agency is None or name is None or name.conflict is None:
        return
    name.conflict["card_title"] = "Agency identity"
    name.conflict["hint"] = (
        "Likely the same agency. Confirm the legal name for the producer record."
    )
    notes: list[str] = []
    for attr in ("phone", "fax", "email"):
        f = agency.fields.get(attr)
        if f is not None and f.candidates:
            values = ", ".join(dict.fromkeys(str(c.compared.display) for c in f.candidates))
            notes.append(f"{f.label} on file: {values}")
    name.conflict["notes"] = notes


def entity_form_notes(board: Board) -> None:
    applicant = board.entity("applicant", "applicant")
    note = applicant.details.pop("entity_form_note", None)
    form = applicant.fields.get("entity_form")
    if note and form is not None and form.conflict is not None:
        form.conflict.setdefault("notes", []).append(note)


def _hint(kind: str, attr: str, entity: Entity) -> str | None:
    if (kind, attr) in (("vehicle", "make"), ("vehicle", "year")):
        decoded = cast(dict[str, Any], entity.details.get("vin_check") or {})
        what = decoded.get("maker") if attr == "make" else decoded.get("model_year")
        return f"The VIN itself decodes to {what}." if what else None
    return HINTS.get((kind, attr))


# --- validators ----------------------------------------------------------------------------


def add_vehicle_validation(board: Board) -> None:
    for e in board.of_kind("vehicle"):
        check = cast(dict[str, Any], e.details.get("vin_check") or {})
        f = e.fields.get("vin")
        if f is not None and check and not check.get("valid"):
            f.invalid = [f"VIN {p}" for p in cast(list[str], check.get("problems", []))]


# --- derived candidates --------------------------------------------------------------------


def facts_on(entity: Entity, document_id: str, *attributes: str) -> tuple[str, ...]:
    """The facts one document gave this entity (only the named attributes, when given): what a
    value worked out from that entity cites, so the lens opens the page they were read on."""
    out: dict[str, None] = {}
    for attr, f in entity.fields.items():
        if attributes and attr not in attributes:
            continue
        for c in f.candidates:
            for src in c.sources:
                if src.document_id == document_id:
                    out.update(dict.fromkeys(src.fact_ids))
    return tuple(out)


def _ids(item: dict[str, Any]) -> tuple[str, ...]:
    return tuple(cast(list[str], item.get("fact_ids") or ()))


def _domicile(board: Board) -> str | None:
    a = board.entity("applicant", "applicant")
    for attr in ("garaging_state", "mailing_state"):
        f = a.fields.get(attr)
        if f and f.candidates:
            return str(f.candidates[0].compared.norm)
    return None


def add_derived(board: Board, *, today: date) -> None:
    ops = board.entity("operations", "operations")
    applicant = board.entity("applicant", "applicant")

    # Owner drives: the application's answer against owners listed on driver schedules.
    contact = applicant.fields.get("contact_name")
    contact_key = (
        person_key(str(contact.candidates[0].compared.norm))
        if contact and contact.candidates
        else None
    )
    contact_facts = tuple(
        fid
        for c in (contact.candidates if contact else [])
        for s in c.sources
        for fid in s.fact_ids
    )
    for d in board.of_kind("driver"):
        marker = d.details.get("owner_marker")
        name_f = d.fields.get("name")
        same_person = bool(
            contact_key
            and name_f
            and any(person_key(str(c.compared.norm)) == contact_key for c in name_f.candidates)
        )
        if marker or (same_person and d.details.get("scheduled")):
            marks = cast(list[dict[str, Any]], d.details.get("owner_markers") or [])
            if not marks and marker:
                marks = [cast(dict[str, Any], marker)]
            if not marks:
                # Matched by name: each schedule that lists the contact as a driver
                marks = [
                    {"document_id": s.document_id, "title": s.title, "fact_ids": list(s.fact_ids)}
                    for c in (name_f.candidates if name_f else [])
                    for s in c.sources
                    if s.doc_type in ("driver_schedule", "application", "quote_form")
                ]
            # Every document that lists the owner as a driver supports "the owner drives". It
            # cites the driver's row there (name, and the owner cell), and when the match is by
            # name, the applicant's contact name too.
            for src_doc in marks:
                board.add(
                    ops,
                    "owner_drives",
                    True,
                    Source(
                        str(src_doc["document_id"]),
                        "derived",
                        str(src_doc["title"]),
                        tuple(
                            dict.fromkeys(
                                _ids(src_doc)
                                + facts_on(d, str(src_doc["document_id"]), "name")
                                + (contact_facts if same_person else ())
                            )
                        ),
                        note=f"{d.label} is the owner and is listed as a driver",
                    ),
                    kind="bool",
                    label="Owner drives",
                )

    # Vehicles found only in loss history mean the "no unlisted vehicles" answer is wrong.
    unlisted = [v for v in board.of_kind("vehicle") if not v.details.get("scheduled")]
    if unlisted:
        note = f"{', '.join(v.label for v in unlisted)} appears in a claim but not on any schedule"
        # One source per document that shows an unlisted vehicle, citing that vehicle's facts
        shown: dict[str, Source] = {}
        for v in unlisted:
            for src in v.sources.values():
                prior = shown.get(src.document_id)
                facts = facts_on(v, src.document_id) + (prior.fact_ids if prior else ())
                shown[src.document_id] = Source(
                    src.document_id, "derived", src.title, tuple(dict.fromkeys(facts)), note=note
                )
        for src in shown.values():
            board.add(
                ops,
                "q_unlisted_vehicles",
                True,
                src,
                kind="bool",
                label="Has vehicles not listed on the application",
            )
    fleet = board.entity("fleet", "fleet")
    fleet.details["found_only_in_losses"] = [v.label for v in unlisted]

    # Documents that name the applicant with different entity forms (Inc on one, LLC on
    # another) contradict a "no change of ownership or name" answer, until someone explains it.
    form = applicant.fields.get("entity_form")
    forms = {c.compared.display: c for c in form.candidates} if form else {}
    change = ops.fields.get("q_ownership_change")
    # Not a conflict of its own: a note on the legal entity conflict, which is the evidence.
    if len(forms) > 1 and change and any(c.compared.norm is False for c in change.candidates):
        applicant.details["entity_form_note"] = (
            f"Inferred from the legal entity conflict: the application answers No to "
            f'"{change.label.lower()}", but the documents name the business with different '
            "entity forms, so ownership, management or the name may have changed"
        )

    aggregate_ifta(board)
    stated = ops.fields.get("farthest_territory")
    domicile = _domicile(board)
    farthest = cast(dict[str, Any], ops.details.get("farthest_state") or {})
    if (
        stated
        and stated.candidates
        and domicile
        and farthest.get("code")
        and domicile in geo.STATES
    ):
        stated_code = str(stated.candidates[0].compared.norm)
        if stated_code in geo.STATES:
            stated_miles = geo.miles_between(domicile, stated_code)
            if float(farthest["distance"]) > stated_miles + 300:
                note = f"Fuel tax returns show {farthest['miles']} miles in {geo.name(farthest['code'])}, about {round(float(farthest['distance'])):,} miles from {geo.name(domicile)}"
                # Each return cites its own miles line for that state
                returns = (
                    Source(
                        src.document_id,
                        src.doc_type,
                        src.title,
                        tuple(q.evidence.get(f"miles:{farthest['code']}", ())) or src.fact_ids,
                        src.as_of,
                    )
                    for (kind, _), q in board.entities.items()
                    if kind == "ifta_quarter"
                    for src in q.sources.values()
                )
                for src in derived_from(returns, "derived", note):
                    board.add(
                        ops,
                        "farthest_territory",
                        farthest["code"],
                        src,
                        kind="state",
                        label="Farthest territory (stated)",
                    )

    coverage_history(board, today=today)


def aggregate_ifta(board: Board) -> None:
    ops = board.entity("operations", "operations")
    quarters = sorted(board.of_kind("ifta_quarter"), key=lambda q: str(q.details.get("end") or ""))
    if not quarters:
        return
    by_state: dict[str, Decimal] = {}
    total = Decimal(0)
    for q in quarters:
        total += Decimal(str(q.details.get("total_miles") or 0))
        for code, miles in cast(dict[str, str], q.details.get("miles_by_state", {})).items():
            by_state[code] = by_state.get(code, Decimal(0)) + Decimal(miles)
    domicile = _domicile(board)
    home = by_state.get(domicile or "", Decimal(0))
    farthest: dict[str, Any] | None = None
    if domicile in geo.STATES:
        for code, miles in by_state.items():
            if code in geo.STATES and miles > 0:
                dist = geo.miles_between(domicile, code)
                if farthest is None or dist > farthest["distance"]:
                    farthest = {"code": code, "distance": round(dist), "miles": f"{miles:,.0f}"}
    ops.details["ifta"] = {
        "quarters": [
            {
                "quarter": q.key,
                "document_id": q.details.get("document_id"),
                "total_miles": q.details.get("total_miles"),
                "penalty": q.details.get("penalty"),
                "interest": q.details.get("interest"),
                "taxable_gallons": q.details.get("taxable_gallons"),
            }
            for q in quarters
        ],
        "annual_miles": str(total),
        "states": len(by_state),
        "miles_by_state": {k: str(v) for k, v in sorted(by_state.items(), key=lambda kv: -kv[1])},
        "domicile": domicile,
        "share_outside_domicile": str(round((total - home) / total * 100, 1))
        if total and domicile
        else None,
    }
    if farthest:
        ops.details["farthest_state"] = farthest


# --- coverage history ----------------------------------------------------------------------


def coverage_history(board: Board, *, today: date) -> None:
    history = board.entity("coverage_history", "history")
    claimed = cast(list[dict[str, Any]], history.details.get("pd_claimed", []))
    evidence = cast(list[dict[str, Any]], history.details.get("pd_evidence", []))
    if claimed and evidence and not any(e["carried"] for e in evidence):
        first = claimed[0]
        board.add(
            history,
            "physical_damage_carried",
            True,
            Source(
                first["document_id"],
                "quote_form",
                first["title"],
                # Every prior-insurance row on that form that lists physical damage
                tuple(
                    fid
                    for c in claimed
                    if c["document_id"] == first["document_id"]
                    for fid in _ids(c)
                ),
                note=f"Prior insurance lists physical damage for {len(claimed)} terms",
            ),
            kind="bool",
            label="Physical damage carried before",
        )
        for ev in evidence:  # every loss run that shows no physical damage
            board.add(
                history,
                "physical_damage_carried",
                False,
                Source(
                    ev["document_id"],
                    "loss_run",
                    ev["title"],
                    _ids(ev),  # the physical damage line on that loss run
                    note=f"{len(evidence)} liability loss runs show no physical damage on the "
                    "liability policy; it may have been written on a separate policy",
                ),
                kind="bool",
                label="Physical damage carried before",
            )

    terms = sorted(
        (
            t
            for t in board.of_kind("coverage_term")
            if t.details.get("line") == "auto_liability" and t.details.get("start")
        ),
        key=lambda t: str(t.details["start"]),
    )
    gaps: list[dict[str, Any]] = []
    covered_days = 0
    for i, t in enumerate(terms):
        start = date.fromisoformat(str(t.details["start"]))
        end = (
            date.fromisoformat(str(t.details["end"]))
            if t.details.get("end")
            else start + timedelta(days=365)
        )
        covered_days += (min(end, today) - start).days if start < today else 0
        if i + 1 < len(terms):
            nxt = date.fromisoformat(str(terms[i + 1].details["start"]))
            if (nxt - end).days > 0:
                gaps.append(
                    {
                        "from": end.isoformat(),
                        "to": nxt.isoformat(),
                        "days": (nxt - end).days,
                        "after_term": t.label,
                    }
                )
    history.details["liability_terms"] = [
        {
            "label": t.label,
            "start": t.details.get("start"),
            "end": t.details.get("end"),
            "evidence": t.details.get("loss_evidence", []),
        }
        for t in terms
    ]
    # Which lines show no coverage at all during each gap (liability, and cargo if it lapsed too)
    for gap in gaps:
        g_from, g_to = date.fromisoformat(gap["from"]), date.fromisoformat(gap["to"])
        uncovered: list[str] = []
        for line, name in (("auto_liability", "liability"), ("cargo", "cargo")):
            spans = [
                (
                    date.fromisoformat(str(t.details["start"])),
                    date.fromisoformat(str(t.details["end"]))
                    if t.details.get("end")
                    else date.fromisoformat(str(t.details["start"])) + timedelta(days=365),
                )
                for t in board.of_kind("coverage_term")
                if t.details.get("line") == line and t.details.get("start")
            ]
            if spans and not any(s < g_to and e > g_from for s, e in spans):
                uncovered.append(name)
        gap["lines"] = uncovered or ["liability"]
    history.details["gaps"] = gaps
    if terms:
        documented = round(Decimal(covered_days) / Decimal(365), 1)
        history.details["documented_years"] = str(documented)
        applicant = board.entity("applicant", "applicant")
        claimed_years = applicant.fields.get("years_prior_insurance")
        if claimed_years and claimed_years.candidates:
            claimed_value = Decimal(str(claimed_years.candidates[0].compared.norm))
            if abs(claimed_value - documented) >= 1:
                c = compare("number", str(documented))
                if c is not None:
                    note = f"Liability terms listed cover about {documented} years"
                    if gaps:
                        note += f", with a {gaps[0]['days']}-day gap"
                    # Each document cites the terms it lists: dates, carrier, policy
                    term_docs = (
                        Source(
                            src.document_id,
                            src.doc_type,
                            src.title,
                            facts_on(t, src.document_id) or src.fact_ids,
                            src.as_of,
                        )
                        for (kind, _), t in board.entities.items()
                        if kind == "coverage_term"
                        for src in t.sources.values()
                    )
                    for src in derived_from(term_docs, "derived", note):
                        board.add_compared(
                            applicant,
                            "years_prior_insurance",
                            c,
                            src,
                            kind="number",
                            label="Years of prior insurance",
                        )


# --- mappings ------------------------------------------------------------------------------


def reconcile_um(board: Board) -> None:
    request = board.entity("coverage_request", "request")
    f = request.fields.get("um_limit")
    domicile = _domicile(board)
    if f is None or f.state != "conflict" or domicile not in UM_STATE_MINIMUMS:
        return
    per_person, per_accident = UM_STATE_MINIMUMS[domicile]
    minimum: Candidate | None = None
    numeric: Candidate | None = None
    for c in f.candidates:
        text = str(c.compared.norm).upper()
        numbers = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", text)]
        if "MINIMUM" in text:
            minimum = c
        elif numbers[:2] == [per_person, per_accident] or numbers[:2] == [
            per_person // 1000,
            per_accident // 1000,
        ]:
            numeric = c
    if minimum and numeric and len(f.candidates) == 2 and f.conflict is not None:
        # Kept as a (low-severity) conflict: "state minimum" is not a limit on a quote, and the
        # minimum can change before binding. The hint says why the two likely agree.
        f.conflict["hint"] = (
            f"Likely the same: the {geo.name(domicile)} minimum is {per_person:,}/{per_accident:,}. "
            "Confirm the limit to quote."
        )


def reconcile_commodities(board: Board) -> None:
    ops = board.entity("operations", "operations")
    app = cast(dict[str, Any] | None, ops.details.get("commodities_application"))
    quote = cast(dict[str, Any] | None, ops.details.get("commodities_quote"))
    if not app or not quote:
        return

    def by_class(block: dict[str, Any]) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for item in cast(list[dict[str, Any]], block["items"]):
            if item.get("percent") is None:
                continue
            key = commodity_class(str(item["label"]))
            out[key] = out.get(key, Decimal(0)) + Decimal(str(item["percent"]))
        return out

    a, q = by_class(app), by_class(quote)
    summary = ", ".join(
        f"{commodity_title(k)} {v.normalize():f}%"
        for k, v in sorted(a.items(), key=lambda kv: -kv[1])
    )
    f = ops.field("commodity_mix", "Commodity mix", "text")
    f.candidates = []
    if a == q:
        c = compare("text", summary)
        if c is not None:
            f.candidates.append(
                Candidate(
                    c,
                    [
                        Source(app["document_id"], "application", app["title"], _block_ids(app)),
                        Source(
                            quote["document_id"], "quote_form", quote["title"], _block_ids(quote)
                        ),
                    ],
                )
            )
        f.state, f.resolution = "known", "mapping"
        f.notes.append(
            f"The quote form lists {len(quote['items'])} commodities and the application {len(app['items'])}; "
            "grouped into cargo classes, the shares match."
        )
    else:
        for block, shares in ((app, a), (quote, q)):
            text = ", ".join(
                f"{commodity_title(k)} {v.normalize():f}%"
                for k, v in sorted(shares.items(), key=lambda kv: -kv[1])
            )
            c = compare("text", text)
            if c is not None:
                f.candidates.append(
                    Candidate(
                        c, [Source(block["document_id"], "", block["title"], _block_ids(block))]
                    )
                )
        f.state = "conflict"
        f.conflict = {
            "type": "value",
            "title": "Commodity mix differs between forms",
            "hint": "Ask the agent which mix is current.",
        }


def _block_ids(block: dict[str, Any]) -> tuple[str, ...]:
    """Every commodity line in a listed block: the mix cites each share it adds up."""
    return tuple(fid for item in cast(list[dict[str, Any]], block["items"]) for fid in _ids(item))


def all_fields(board: Board) -> list[tuple[Entity, Field]]:
    return [(e, f) for e in board.entities.values() for f in e.fields.values()]
