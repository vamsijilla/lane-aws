"""Typed per-kind summaries of resolved entities: the numbers and flags each screen shows.

The UI only displays these. Thresholds come from the ruleset params (or settings, for display
cues the guideline does not set), and windows count back from the decision anchor (A02), so a
screen can never disagree with the rule that judged the same value."""

import re
from datetime import date
from decimal import Decimal
from typing import Any, Literal, cast

from pydantic import ConfigDict, Field

from lane.api import display
from lane.api.models import CandidateOut, Out
from lane.config import Settings
from lane.db.models import ResolvedEntity, ResolvedField
from lane.domain import normalize as nz
from lane.domain.ages import age_on
from lane.domain.cdl import describe, earliest_cdl, from_history, latest_cdl_issue
from lane.rules import points

Tone = Literal["neutral", "guide", "warning", "stop", "interstate"]


class Badge(Out):
    tone: Tone
    text: str


# --- drivers -------------------------------------------------------------------------------


class MvrEventOut(Out):
    kind: str | None = None
    event_date: str | None = None
    description: str | None = None
    points: int | None = None
    location: str | None = None


class MvrNoteOut(Out):
    note_date: str | None = None
    text: str | None = None
    flagged: bool = False  # a downgrade, suspension or revocation


class LicenseCardOut(Out):
    document_id: str | None
    issued: str | None
    expiration: str | None


class MvrOut(Out):
    document_id: str
    title: str
    order_date: str | None
    record_type: str | None
    events: list[MvrEventOut]
    notes: list[MvrNoteOut]


class SourceRow(Out):
    """One attribute as each kind of document states it, for the side-by-side table."""

    attribute: str
    columns: dict[str, list[str]]  # column key -> values; superseded ones end "(superseded)"
    differs: bool


class DriverSummary(Out):
    scheduled: bool
    owner: bool
    claims: list[str]  # claim numbers this person appears in
    reported_to_prior_carrier: bool | None
    age: int | None
    earliest_cdl: str | None  # "Earliest CDL evidence: ..." or "CDL issue date on record: ..."
    points_3y: int
    points_12m: int
    mvr: MvrOut | None
    mvr_age_days: int | None
    older_mvrs: int
    license_card: LicenseCardOut | None
    badges: list[Badge]
    source_rows: list[SourceRow]


_FLAGGED_NOTE = re.compile(r"downgrade|suspen|revok", re.IGNORECASE)


def _notes(raw: list[Any]) -> list[MvrNoteOut]:
    """License history notes, each once (abstracts repeat them per section)."""
    out: dict[tuple[str, str], MvrNoteOut] = {}
    for n in (_dict(x) for x in raw):
        date_, text = n.get("note_date"), n.get("text")
        out.setdefault(
            (str(date_ or ""), str(text or "")),
            MvrNoteOut(
                note_date=date_,
                # The date has its own column; the note reads as words
                text=display.mvr_note(text),
                flagged=bool(_FLAGGED_NOTE.search(text or "")),
            ),
        )
    return list(out.values())


# Which documents feed each column of the driver table
DRIVER_COLUMNS: dict[str, tuple[str, ...]] = {
    "schedule": ("driver_schedule", "application", "quote_form", "schedule_pdf", "email_body"),
    "mvr": ("mvr",),
    "license": ("drivers_license",),
}


# --- vehicles, coverage, claims ------------------------------------------------------------


class VinCheckOut(Out):
    valid: bool
    maker: str | None = None
    model_year: int | None = None
    problems: list[str] = Field(default_factory=lambda: list[str]())


class VehicleSummary(Out):
    scheduled: bool
    vin_check: VinCheckOut | None
    notes: list[str]


class TermSummary(Out):
    line: str
    start: str | None
    end: str | None
    carrier_key: str
    listed_on_application: bool
    loss_history: Literal["loss_run", "letter", "none"]
    letter_scope: str | None
    notes: list[str] = Field(default_factory=lambda: list[str]())  # e.g. a form's dates 1 day off


class GapOut(Out):
    from_: str = Field(alias="from")
    to: str
    days: int
    after_term: str | None = None
    lines: list[str] = Field(default_factory=lambda: ["liability"])  # no coverage shown on these

    model_config = ConfigDict(
        populate_by_name=True,
        serialize_by_alias=True,
        json_schema_serialization_defaults_required=True,
    )


class HistorySummary(Out):
    gaps: list[GapOut]
    documented_years: float | None


class ClaimSummary(Out):
    line: str | None
    report_lag_days: int | None
    reported_late: bool  # lag over the configured days, or flagged late on the loss run
    driver_reported: bool | None
    unit_reported: bool | None
    # The scheduled unit the claim's VIN names, with its settled year and make ("2020 Volvo
    # (settled by VIN decode)"); None when the VIN matches no scheduled unit
    unit: str | None = None


# --- operations ----------------------------------------------------------------------------


class ShareItemOut(Out):
    label: str
    percent: float | None
    value: float | None
    fact_ids: list[str]
    above_cargo_limit: bool


class ShareListOut(Out):
    source: Literal["application", "quote"]
    document_id: str | None
    title: str | None
    items: list[ShareItemOut]


class FarthestOut(Out):
    code: str
    distance: int
    miles: int | None


class UnansweredOut(Out):
    key: str | None
    question: str
    document_id: str | None


class OperationsSummary(Out):
    domicile: str | None
    annual_miles: int
    share_outside_home_pct: float | None
    miles_by_state: dict[str, int]  # largest first
    farthest_state: FarthestOut | None
    radius: list[ShareListOut]
    commodities: list[ShareListOut]
    unanswered_questions: list[UnansweredOut]


class IftaQuarterSummary(Out):
    quarter: str
    total_miles: int
    taxable_gallons: int
    mpg: float | None
    outside_home_miles: int | None
    outside_home_pct: float | None
    penalty_and_interest: float
    total_due: float | None
    change_pct: float | None  # against the previous quarter sent
    dropped: bool  # fell by more than the configured share


class Summaries(Out):
    driver: DriverSummary | None = None
    vehicle: VehicleSummary | None = None
    term: TermSummary | None = None
    history: HistorySummary | None = None
    claim: ClaimSummary | None = None
    operations: OperationsSummary | None = None
    ifta: IftaQuarterSummary | None = None


# --- helpers -------------------------------------------------------------------------------


def _int(v: Any) -> int | None:
    n = nz.number(str(v)) if v is not None and v != "" else None
    return int(n) if n is not None else None


def _float(v: Any) -> float | None:
    n = nz.money(str(v)) if v is not None and v != "" else None
    return float(n) if n is not None else None


def _date(v: Any) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return nz.to_date(str(v))


def _dict(v: Any) -> dict[str, Any]:
    return cast(dict[str, Any], v) if isinstance(v, dict) else {}


def _list(v: Any) -> list[Any]:
    return cast(list[Any], v) if isinstance(v, list) else []


_CARRIER_NOISE = re.compile(
    r"\b(insurance|ins|company|co|inc|corp|corporation|llc|casualty)\b\.?", re.IGNORECASE
)


def carrier_key(name: str | None) -> str:
    """'ACME CASUALTY INSURANCE COMPANY' and 'Acme' are one carrier."""
    if not name:
        return "Carrier not shown"
    words = re.sub(r"\s+", " ", _CARRIER_NOISE.sub("", name)).strip()
    return words.title() if words else name.strip().title()


class _Fields:
    """An entity's fields: raw values for computing, masked candidates for showing."""

    def __init__(self, raw: list[ResolvedField], shown: dict[str, list[CandidateOut]]):
        self.raw = {f.attribute: f for f in raw}
        self.shown = shown

    def value(self, attribute: str) -> Any:
        f = self.raw.get(attribute)
        return f.value if f is not None and f.state == "known" else None

    def display(self, attribute: str) -> str | None:
        f = self.raw.get(attribute)
        if f is None or f.state == "missing":
            return None
        if f.state == "conflict":
            return " or ".join(
                str(c.display or "") for c in self.shown.get(attribute, []) if not c.superseded
            )
        return f.value_display

    def first_display(self, attribute: str) -> str | None:
        f = self.raw.get(attribute)
        if f is None or f.state == "missing":
            return None
        if f.state == "conflict":
            cands = [c for c in self.shown.get(attribute, []) if not c.superseded]
            return str(cands[0].display) if cands and cands[0].display else None
        return f.value_display


# --- builders ------------------------------------------------------------------------------


def _driver_age(f: _Fields, on: date) -> int | None:
    """Age on the application date; for conflicting dates of birth, only when every reading
    agrees."""
    raw = f.raw.get("date_of_birth")
    if raw is None or raw.state == "missing":
        return None
    values = (
        [raw.value]
        if raw.state == "known"
        else [c.get("value") for c in raw.candidates if not c.get("superseded")]
    )
    ages = {age_on(d, on) for v in values if (d := _date(v)) is not None}
    return ages.pop() if len(ages) == 1 else None


def _source_rows(f: _Fields) -> list[SourceRow]:
    rows: list[SourceRow] = []
    for attribute, cands in f.shown.items():
        cols: dict[str, list[str]] = {}
        for c in cands:
            for s in c.sources:
                col = next((k for k, types in DRIVER_COLUMNS.items() if s.doc_type in types), None)
                if col is None:
                    continue
                v = str(c.display or "")
                v = f"{v} (superseded)" if c.superseded else v
                if v not in cols.setdefault(col, []):
                    cols[col].append(v)
        current = {v for vs in cols.values() for v in vs if not v.endswith("(superseded)")}
        rows.append(SourceRow(attribute=attribute, columns=cols, differs=len(current) > 1))
    return rows


def driver(
    e: ResolvedEntity, f: _Fields, params: dict[str, Any], anchor: date, ages_on: date
) -> DriverSummary:
    d = e.details
    mvrs = sorted(
        (_dict(m) for m in _list(d.get("mvrs"))),
        key=lambda m: str(m.get("order_date") or ""),
        reverse=True,
    )
    latest = mvrs[0] if mvrs else None
    events = points.events(mvrs)  # the same events the point rule counts
    pts = points.count(events, anchor)
    order = _date(latest.get("order_date")) if latest else None
    mvr_age = (anchor - order).days if order else None
    card = _dict(d.get("license_card"))
    age = _driver_age(f, ages_on)

    max3, max1 = int(params["max_points_3y"]), int(params["max_points_12m"])
    badges: list[Badge] = []
    cdl = f.raw.get("commercial_license")
    if cdl is not None and cdl.state == "known":
        cls = f.display("license_class")
        badges.append(
            Badge(tone="guide", text=f"CDL class {cls or ''}".strip())
            if cdl.value is True or cdl.value_display == "Yes"
            else Badge(tone="stop", text=f"No CDL, class {cls or 'unknown'}")
        )
    if age is not None:
        outside = age < int(params["min_driver_age"]) or age >= int(params["medical_exam_age"])
        badges.append(Badge(tone="warning" if outside else "neutral", text=f"Age {age}"))
    over = pts.three_years > max3 or pts.twelve_months > max1
    text = f"{pts.three_years} {'point' if pts.three_years == 1 else 'points'} in 3 years"
    if pts.twelve_months:
        text += f", {pts.twelve_months} in 12 months"
    badges.append(Badge(tone="warning" if over else "neutral", text=text))
    if pts.outside:
        n = sum(p for _, p in pts.outside)
        badges.append(
            Badge(
                tone="neutral", text=f"{n} {'point' if n == 1 else 'points'} just outside 3 years"
            )
        )
    if mvr_age is not None:
        stale = mvr_age > int(params["mvr_max_age_days"])
        badges.append(Badge(tone="stop" if stale else "neutral", text=f"MVR {mvr_age} days old"))
    if len(mvrs) > 1:
        badges.append(Badge(tone="neutral", text=f"{len(mvrs) - 1} older MVR superseded"))

    reported = d.get("reported_to_prior_carrier")
    issued = f.raw["license_issued"].candidates if "license_issued" in f.raw else []
    return DriverSummary(
        scheduled=bool(d.get("scheduled")),
        owner=bool(d.get("owner_marker")),
        claims=[str(c) for c in _list(d.get("claims"))],
        reported_to_prior_carrier=reported if isinstance(reported, bool) else None,
        age=age,
        earliest_cdl=describe(
            earliest_cdl(issued, card or None),
            from_history(_list(d.get("mvrs"))),
            latest_cdl_issue(issued, card or None),
        )
        if f.raw.get("commercial_license") is None or f.raw["commercial_license"].value is not False
        else None,
        points_3y=pts.three_years,
        points_12m=pts.twelve_months,
        mvr=MvrOut(
            document_id=str(latest.get("document_id") or ""),
            title=str(latest.get("title") or ""),
            order_date=latest.get("order_date"),
            record_type=nz.display_text(latest.get("record_type")),
            events=[
                MvrEventOut(
                    kind=x.get("kind"),
                    event_date=x.get("event_date"),
                    description=display.mvr_event(x.get("description")),
                    points=_int(x.get("points")),
                    location=display.place(x.get("location")),
                )
                for x in events
            ],
            notes=_notes(_list(latest.get("notes"))),
        )
        if latest
        else None,
        mvr_age_days=mvr_age,
        older_mvrs=max(len(mvrs) - 1, 0),
        license_card=LicenseCardOut(
            document_id=card.get("document_id"),
            issued=card.get("issued"),
            expiration=card.get("expiration"),
        )
        if card
        else None,
        badges=badges,
        source_rows=_source_rows(f),
    )


def vehicle(e: ResolvedEntity) -> VehicleSummary:
    check = _dict(e.details.get("vin_check"))
    return VehicleSummary(
        scheduled=bool(e.details.get("scheduled")),
        vin_check=VinCheckOut(
            valid=bool(check.get("valid")),
            maker=check.get("maker"),
            model_year=_int(check.get("model_year")),
            problems=[str(p) for p in _list(check.get("problems"))],
        )
        if check
        else None,
        notes=[str(n) for n in _list(e.details.get("notes"))],
    )


def term(e: ResolvedEntity, f: _Fields) -> TermSummary:
    evidence = [_dict(x) for x in _list(e.details.get("loss_evidence"))]
    letter = next((x for x in evidence if x.get("kind") == "letter"), None)
    history: Literal["loss_run", "letter", "none"] = (
        "loss_run" if any(x.get("complete") for x in evidence) else "letter" if letter else "none"
    )
    return TermSummary(
        line=str(e.details.get("line") or "unknown"),
        start=e.details.get("start"),
        end=e.details.get("end"),
        carrier_key=carrier_key(f.first_display("carrier")),
        listed_on_application=bool(e.details.get("listed_on_application")),
        loss_history=history,
        letter_scope=str(letter.get("scope") or "partial")
        if letter and history == "letter"
        else None,
        notes=[str(n) for n in _list(e.details.get("notes"))],
    )


def history(e: ResolvedEntity) -> HistorySummary:
    years = e.details.get("documented_years")
    return HistorySummary(
        gaps=[GapOut.model_validate(_dict(g)) for g in _list(e.details.get("gaps"))],
        documented_years=float(years) if years is not None else None,
    )


def claim(
    e: ResolvedEntity, f: _Fields, settings: Settings, units: dict[str, str] | None = None
) -> ClaimSummary:
    loss, reported = _date(f.value("date_of_loss")), _date(f.value("date_reported"))
    lag = (reported - loss).days if loss and reported else None
    d = e.details

    def flag(key: str) -> bool | None:
        v = d.get(key)
        return v if isinstance(v, bool) else None

    return ClaimSummary(
        line=d.get("line"),
        report_lag_days=lag,
        reported_late=bool(d.get("late_reported"))
        or (lag is not None and lag > settings.claim_report_lag_days),
        driver_reported=flag("driver_reported"),
        unit_reported=flag("unit_reported"),
        unit=(units or {}).get(str(f.value("vin") or "").upper()),
    )


def unit_names(
    vehicles: list[ResolvedEntity], fields: dict[str, list[ResolvedField]]
) -> dict[str, str]:
    """VIN -> the scheduled unit as the board settled it, for the claims that name it."""
    return _unit_names([(v, _Fields(fields.get(v.id, []), {})) for v in vehicles])


def _unit_names(vehicles: list[tuple[ResolvedEntity, _Fields]]) -> dict[str, str]:
    """VIN -> the unit as the board settled it: '2020 Volvo (settled by VIN decode)'."""
    out: dict[str, str] = {}
    for v, f in vehicles:
        vin = str(f.value("vin") or v.key or "").upper()
        name = " ".join(x for x in (f.display("year"), f.display("make")) if x)
        make = f.raw.get("make")
        if vin and name:
            settled = make is not None and make.resolution == "reference"
            out[vin] = f"{name} (settled by VIN decode)" if settled else name
    return out


def _shares(
    raw: Any, source: Literal["application", "quote"], cargo_limit: float | None
) -> ShareListOut | None:
    data = _dict(raw)
    if not data:
        return None
    return ShareListOut(
        source=source,
        document_id=data.get("document_id"),
        title=data.get("title"),
        items=[
            ShareItemOut(
                label=display.source_label(str(it.get("label") or "")) or "",
                percent=_float(it.get("percent")),
                value=_float(it.get("value")),
                fact_ids=[str(x) for x in _list(it.get("fact_ids"))],
                above_cargo_limit=bool(
                    cargo_limit and (v := _float(it.get("value"))) and v > cargo_limit
                ),
            )
            for it in (_dict(x) for x in _list(data.get("items")))
        ],
    )


def operations(e: ResolvedEntity, cargo_limit: float | None) -> OperationsSummary:
    d = e.details
    ifta = _dict(d.get("ifta"))
    far = _dict(d.get("farthest_state"))
    by_state = {k: _int(v) or 0 for k, v in _dict(ifta.get("miles_by_state")).items()}
    return OperationsSummary(
        domicile=ifta.get("domicile"),
        annual_miles=_int(ifta.get("annual_miles")) or 0,
        share_outside_home_pct=_float(ifta.get("share_outside_domicile")),
        miles_by_state=dict(sorted(by_state.items(), key=lambda kv: -kv[1])),
        farthest_state=FarthestOut(
            code=str(far["code"]),
            distance=int(far.get("distance") or 0),
            miles=_int(far.get("miles")),
        )
        if far.get("code")
        else None,
        radius=[
            s
            for s in (
                _shares(d.get("radius_application"), "application", cargo_limit),
                _shares(d.get("radius_quote"), "quote", cargo_limit),
            )
            if s
        ],
        commodities=[
            s
            for s in (
                _shares(d.get("commodities_application"), "application", cargo_limit),
                _shares(d.get("commodities_quote"), "quote", cargo_limit),
            )
            if s
        ],
        unanswered_questions=[
            UnansweredOut(
                key=q.get("key"),
                question=str(q.get("question") or ""),
                document_id=q.get("document_id"),
            )
            for q in (_dict(x) for x in _list(d.get("unanswered_questions")))
        ],
    )


def ifta_quarters(
    quarters: list[ResolvedEntity], home: str | None, settings: Settings
) -> dict[str, IftaQuarterSummary]:
    out: dict[str, IftaQuarterSummary] = {}
    previous: int | None = None
    for q in sorted(quarters, key=lambda q: q.key):
        d = q.details
        miles = _int(d.get("total_miles")) or 0
        gallons = _int(d.get("taxable_gallons")) or 0
        by_state = _dict(d.get("miles_by_state"))
        change = round((miles - previous) / previous * 100, 1) if previous else None
        outside = miles - (_int(by_state.get(home)) or 0) if home else None
        out[q.id] = IftaQuarterSummary(
            quarter=q.key,
            total_miles=miles,
            taxable_gallons=gallons,
            mpg=round(miles / gallons, 2) if gallons else None,
            outside_home_miles=outside,
            outside_home_pct=round(outside / miles * 100, 1)
            if outside is not None and miles
            else None,
            penalty_and_interest=float(
                (nz.money(str(d.get("penalty") or 0)) or Decimal(0))
                + (nz.money(str(d.get("interest") or 0)) or Decimal(0))
            ),
            total_due=_float(d.get("total_due")),
            change_pct=change,
            dropped=change is not None and change < -settings.ifta_drop_warning_pct,
        )
        previous = miles or previous
    return out


def summarize(
    entities: list[ResolvedEntity],
    fields: dict[str, list[ResolvedField]],
    shown: dict[str, dict[str, list[CandidateOut]]],
    *,
    params: dict[str, Any],
    anchor: date,
    ages_on: date,
    settings: Settings,
    units: dict[str, str] | None = None,
) -> dict[str, Summaries]:
    """One Summaries per entity id, with only the member for its kind set."""

    def fields_of(e: ResolvedEntity) -> _Fields:
        return _Fields(fields.get(e.id, []), shown.get(e.id, {}))

    ops = next((e for e in entities if e.kind == "operations"), None)
    request = next((e for e in entities if e.kind == "coverage_request"), None)
    cargo_limit = _float(fields_of(request).value("cargo_limit")) if request else None
    home = _dict(ops.details.get("ifta")).get("domicile") if ops else None
    ifta = ifta_quarters([e for e in entities if e.kind == "ifta_quarter"], home, settings)
    if units is None:
        units = _unit_names([(v, fields_of(v)) for v in entities if v.kind == "vehicle"])
    out: dict[str, Summaries] = {}
    for e in entities:
        match e.kind:
            case "driver":
                out[e.id] = Summaries(driver=driver(e, fields_of(e), params, anchor, ages_on))
            case "vehicle":
                out[e.id] = Summaries(vehicle=vehicle(e))
            case "coverage_term":
                out[e.id] = Summaries(term=term(e, fields_of(e)))
            case "coverage_history":
                out[e.id] = Summaries(history=history(e))
            case "claim":
                out[e.id] = Summaries(claim=claim(e, fields_of(e), settings, units))
            case "operations":
                out[e.id] = Summaries(operations=operations(e, cargo_limit))
            case "ifta_quarter":
                out[e.id] = Summaries(ifta=ifta[e.id])
            case _:
                out[e.id] = Summaries()
    return out
