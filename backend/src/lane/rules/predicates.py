"""One predicate per guideline rule. Predicates read resolved facts only and never call a model.

Each returns a Verdict with a plain-language reason that names the facts it used. Thresholds come
from the ruleset's params; the predicates hold only the logic.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Any, cast

from lane.domain import geo
from lane.domain import normalize as nz
from lane.domain.ages import age_on
from lane.domain.cdl import earliest_cdl
from lane.rules import points
from lane.rules.model import Outcome, Verdict
from lane.rules.view import EntityView, SubmissionView

BORDER_STATES = {"CA", "AZ", "NM", "TX"}


@dataclass
class RuleContext:
    view: SubmissionView
    params: dict[str, Any]
    anchor: date
    anchor_name: str  # "application date" | "effective date" | "received date"
    effective: date | None
    open_conflicts: list[str]  # conflict titles
    teams: dict[str, str] = field(default_factory=lambda: dict[str, str]())  # key -> label
    # Conflicts code settled from a deterministic reference (a VIN's own decode): not open
    settled_conflicts: list[str] = field(default_factory=lambda: list[str]())


Predicate = Callable[[RuleContext], Verdict]
REGISTRY: dict[str, Predicate] = {}


def predicate(fn: Predicate) -> Predicate:
    REGISTRY[fn.__name__] = fn
    return fn


def _ifta(ops: EntityView) -> dict[str, Any]:
    return cast(dict[str, Any], ops.details.get("ifta") or {})


def _d(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return nz.to_date(str(value))


def _years(start: date, end: date) -> Decimal:
    return (Decimal((end - start).days) / Decimal("365.25")).quantize(Decimal("0.1"))


def _ages_on(ctx: RuleContext) -> date:
    """Ages always count from the application date, whichever date the rules count from."""
    return ctx.view.date_of("applicant", "application_date") or ctx.anchor


def _n(count: int, noun: str) -> str:
    """'1 driver', '2 drivers'."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _fmt(d: date) -> str:
    return d.strftime("%b %-d, %Y")


# --- submission requirements -------------------------------------------------------------


@predicate
def has_fein(ctx: RuleContext) -> Verdict:
    a = ctx.view.one("applicant")
    if a and a.value("fein"):
        return Verdict(Outcome.PASS, f"FEIN {a.display('fein')} is on the application")
    return Verdict(Outcome.REFER, "No FEIN or SSN on the application")


@predicate
def has_mc(ctx: RuleContext) -> Verdict:
    a = ctx.view.one("applicant")
    if a and a.value("mc_number"):
        return Verdict(Outcome.PASS, f"MC {a.display('mc_number')} is on the application")
    return Verdict(Outcome.REFER, "No MC or docket number")


def _loss_evidence(view: SubmissionView) -> list[tuple[EntityView, dict[str, Any]]]:
    out: list[tuple[EntityView, dict[str, Any]]] = []
    for term in view.all("coverage_term"):
        for ev in cast(list[dict[str, Any]], term.details.get("loss_evidence", [])):
            out.append((term, ev))
    return out


def _terms_phrase(labels: list[str]) -> str:
    """'Auto liability 2023-24, 2024-25 and cargo 2024-25' from term labels."""
    by_line: dict[str, list[str]] = {}
    for label in labels:
        line, _, years = label.rpartition(" ")
        by_line.setdefault(line or label, []).append(_short_years(years) if line else "")
    parts = [
        f"{line} {', '.join(sorted(y for y in years if y))}".strip()
        for line, years in sorted(by_line.items())
    ]
    joined = parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1].lower()}"
    return joined


@predicate
def loss_runs_current(ctx: RuleContext) -> Verdict:
    max_age = int(ctx.params["loss_run_max_age_days"])
    runs = [
        (t, ev, _d(ev.get("run_date")))
        for t, ev in _loss_evidence(ctx.view)
        if ev.get("kind") == "loss_run"
    ]
    if not runs:
        return Verdict(Outcome.REFER, "No loss runs were sent")
    undated = [t.label for t, _ev, d in runs if d is None]
    stale = [
        (t.label, (ctx.anchor - d).days)
        for t, _ev, d in runs
        if d and (ctx.anchor - d).days > max_age
    ]
    if stale:
        # One line per age, not one per run: "Auto liability 2023-24, 2024-25, 2025-26 and
        # cargo 2024-25, 2025-26: valued 70 days before the effective date"
        by_age: dict[int, list[str]] = {}
        for label, days in stale:
            by_age.setdefault(days, []).append(label)
        return Verdict(
            Outcome.REFER,
            f"{_n(len(stale), 'loss run')} older than {max_age} days at the {ctx.anchor_name}",
            [
                f"{_terms_phrase(labels)}: valued {days} days before the {ctx.anchor_name}"
                for days, labels in sorted(by_age.items(), reverse=True)
            ],
        )
    if undated:
        return Verdict(Outcome.UNKNOWN, "Some loss runs show no valuation date", undated)
    oldest = min(d for _t, _ev, d in runs if d)
    age = (ctx.anchor - oldest).days
    reason = f"Valued {_fmt(oldest)}, {age} days before the {ctx.anchor_name}"
    if ctx.effective and (ctx.effective - oldest).days > max_age:
        days = (ctx.effective - oldest).days
        return Verdict(
            Outcome.FLAG,
            f"{reason}; they will be {days} days old by the requested effective date",
            [
                f"Updated loss runs before binding (current ones will be {days} days old on {_fmt(ctx.effective)})"
            ],
        )
    return Verdict(Outcome.PASS, reason)


LINE_NAMES = {"auto_liability": "auto liability", "cargo": "cargo"}
NO_RUN = "no loss run sent"
LINE_SHORT = {"auto_liability": "liability", "cargo": "cargo"}
_COMPANY = re.compile(r"\b(insurance|company|co|inc|corp|corporation|casualty)\b\.?", re.IGNORECASE)


def _terms(view: SubmissionView, line: str) -> list[EntityView]:
    terms = [
        t
        for t in view.all("coverage_term")
        if t.details.get("line") == line and t.details.get("start")
    ]
    return sorted(terms, key=lambda t: str(t.details["start"]))


def _al_terms(view: SubmissionView) -> list[EntityView]:
    return _terms(view, "auto_liability")


def _has_complete_run(term: EntityView) -> bool:
    return any(
        ev.get("kind") == "loss_run" and ev.get("complete")
        for ev in cast(list[dict[str, Any]], term.details.get("loss_evidence", []))
    )


def _loss_gap(term: EntityView) -> str | None:
    """What is missing for a term's losses, worded the same in every rule; None when complete."""
    evidence = cast(list[dict[str, Any]], term.details.get("loss_evidence", []))
    if not evidence:
        return NO_RUN
    if any(e.get("complete") for e in evidence):
        return None
    scope = str(next((e.get("scope") for e in evidence if e.get("scope")), ""))
    what = (
        "paid losses only, no open claims or reserves"
        if "paid" in scope.lower()
        else scope or "part of the losses"
    )
    return f"only a letter, partial ({what})"


def _short_years(label: str) -> str:
    """'2022-2023' -> '2022-23'."""
    m = re.fullmatch(r"(\d{4})-(\d{2})(\d{2})", label)
    return f"{m.group(1)}-{m.group(3)}" if m else label


def _loss_gap_items(terms: list[EntityView]) -> list[str]:
    """One line per carrier, period and gap, naming every line of coverage:
    'Loss runs from Summit Mutual for 2022-23, liability and cargo'."""
    groups: dict[tuple[str, str, str], list[str]] = {}
    for t in terms:
        gap = _loss_gap(t)
        if gap is None:
            continue
        carrier = t.display("carrier") or ""
        carrier = " ".join(_COMPANY.sub("", carrier).split()).title()
        years = _short_years(t.label.split()[-1])
        line = LINE_SHORT.get(str(t.details.get("line")), str(t.details.get("line")))
        groups.setdefault((carrier, years, gap), []).append(line)
    items: list[str] = []
    for (carrier, years, gap), lines in groups.items():
        lines = sorted(set(lines), key=list(LINE_SHORT.values()).index)
        what = " and ".join(lines)
        # Phrased as requests: what to send, and why when something was sent
        source = f" from {carrier}" if carrier else ""
        if gap == NO_RUN:
            items.append(f"Loss runs{source} for {years}, {what}")
        else:
            shows = gap.split("(", 1)[-1].rstrip(")") if "(" in gap else gap
            items.append(f"Full {what} loss run{source} for {years}; the letter shows {shows}")
    return items


def _whole_years(years: Decimal) -> str:
    """No decimals: '2', or 'about 2' when the terms do not line up with whole years."""
    whole = int(years.to_integral_value())
    return str(whole) if abs(years - whole) <= Decimal("0.1") else f"about {whole}"


@predicate
def loss_run_years(ctx: RuleContext) -> Verdict:
    """The expiring term and the prior years, for every line with terms (liability, cargo)."""
    required = int(ctx.params["loss_run_prior_years"])
    lines = [ln for ln in LINE_NAMES if _terms(ctx.view, ln)]
    if not lines:
        return Verdict(Outcome.REFER, "No liability terms or loss runs were found")
    short: list[str] = []
    full: list[str] = []
    gap_terms: list[EntityView] = []
    for line in lines:
        terms = _terms(ctx.view, line)
        expiring = next(
            (
                t
                for t in terms
                if (s := _d(t.details["start"]))
                and (e := _d(t.details.get("end")))
                and s <= ctx.anchor < e
            ),
            terms[-1],
        )
        expiring_start = _d(expiring.details["start"]) or ctx.anchor
        window_start = expiring_start - timedelta(days=round(365.25 * required))
        prior = [
            t
            for t in terms
            if t is not expiring and (_d(t.details.get("end")) or expiring_start) > window_start
        ]
        covered = Decimal(0)
        for t in prior:
            start = max(_d(t.details["start"]) or window_start, window_start)
            end = min(_d(t.details.get("end")) or expiring_start, expiring_start)
            if _has_complete_run(t):
                covered += _years(start, end)
        gaps = [t for t in [expiring, *prior] if _loss_gap(t) is not None]
        gap_terms.extend(gaps)
        name = LINE_NAMES[line]
        if covered >= required - Decimal("0.2") and not gaps:
            full.append(name)
        else:
            short.append(f"{_whole_years(covered)} of {required} prior years for {name}")
    items = _loss_gap_items(gap_terms)
    if not short:
        return Verdict(
            Outcome.PASS,
            f"Expiring term plus {required} prior years of loss runs for {' and '.join(full)}",
        )
    return Verdict(
        Outcome.REFER,
        f"Loss runs cover the expiring term and {'; '.join(short)}",
        items or [f"Loss runs for {required} prior years"],
    )


@predicate
def ifta_quarters(ctx: RuleContext) -> Verdict:
    needed = int(ctx.params["ifta_quarters"])
    lag = int(ctx.params["ifta_filing_lag_days"])
    ops = ctx.view.one("operations")
    quarters = cast(list[dict[str, Any]], _ifta(ops).get("quarters", [])) if ops else []
    have = {str(q["quarter"]) for q in quarters}
    # The most recent quarter whose return was already due at the anchor date.
    d = ctx.anchor
    q_end = date(d.year, ((d.month - 1) // 3) * 3 + 1, 1) - timedelta(days=1)
    if (ctx.anchor - q_end).days < lag:
        q_end = date(q_end.year, ((q_end.month - 1) // 3) * 3 + 1, 1) - timedelta(days=1)
    expected: list[str] = []
    for _ in range(needed):
        expected.append(f"{q_end.year}-Q{(q_end.month - 1) // 3 + 1}")
        q_end = date(q_end.year, ((q_end.month - 1) // 3) * 3 + 1, 1) - timedelta(days=1)
    missing = [q for q in expected if q not in have]
    if not missing:
        shown = ", ".join(nz.quarter(q) for q in reversed(expected))
        return Verdict(Outcome.PASS, f"Returns for {shown} were sent")
    return Verdict(
        Outcome.REFER,
        f"{len(missing)} of the {needed} most recent IFTA quarters missing",
        [f"IFTA return for {nz.quarter(q)}" for q in missing],
    )


def _drivers(
    ctx: RuleContext, *, only_driving: bool, judges_owner: bool = False
) -> list[EntityView]:
    """Scheduled drivers; with only_driving, without an owner who does not drive.

    Whether the owner drives only scopes the list for most driver rules; it is an input (and so
    gets sensitivity analysis) only for a rule that judges it (`judges_owner`, the CDL rule)."""
    drivers = [d for d in ctx.view.all("driver") if d.scheduled]
    if not only_driving:
        return drivers
    # Excluded by name in a signed exclusion: listed, but not a driver of the insured units
    drivers = [d for d in drivers if not d.details.get("excluded")]
    ops = ctx.view.one("operations")
    if ops is None:
        owner_drives = None
    elif judges_owner:
        owner_drives = ops.value("owner_drives")
    else:
        owner_drives = ops.peek("owner_drives")
    if owner_drives is False:
        drivers = [d for d in drivers if not d.details.get("owner_marker")]
    return drivers


LOSS_HISTORY = frozenset({"loss_run", "loss_history_letter"})


# Where the trucks are garaged and run: one answer settles every rule that needs it
GARAGING = ("garaging", "The garaging address and routes")


def _loss_history_only(ctx: RuleContext, kind: str) -> list[str]:
    """Drivers or units a claim names but no schedule, application or MVR does."""
    return [
        e.label
        for e in ctx.view.all(kind)
        if not e.scheduled and e.found_in and set(e.found_in) <= LOSS_HISTORY
    ]


def _unless_unlisted(
    ctx: RuleContext,
    verdict: Verdict,
    kind: str,
    unknown: str | None = None,
    *,
    name_always: bool = False,
) -> Verdict:
    """A pass that cannot see everyone: someone found only in loss history may still drive (or
    a unit may still run), so the rule is unknown until the agent says, never a pass. With
    name_always, an outcome that is not a pass still names them."""
    unlisted = _loss_history_only(ctx, kind)
    if not unlisted:
        return verdict
    what = unknown or (
        "driving status and license unknown" if kind == "driver" else "vehicle type unknown"
    )
    why = f"{', '.join(unlisted)}, found only in loss history: {what}"
    # One question per person or unit, shared by every rule that waits on them
    waiting = [
        (
            f"{kind}:{name}",
            f"{name}'s license and driving record"
            if kind == "driver"
            else f"The vehicle type of {name}",
        )
        for name in unlisted
    ]
    if verdict.outcome is not Outcome.PASS:
        if not name_always:
            return verdict
        return Verdict(
            verdict.outcome,
            f"{verdict.reason} Also: {why}.",
            verdict.items,
            verdict.evidence,
            waiting,
        )
    # The ask names who is unknown, not everyone the rule covers
    return Verdict(
        Outcome.UNKNOWN,
        f"{why}. {verdict.reason}",
        verdict.items or unlisted,
        verdict.evidence,
        waiting,
    )


@predicate
def mvrs_current(ctx: RuleContext) -> Verdict:
    max_age = int(ctx.params["mvr_max_age_days"])
    problems: list[str] = []
    dated: list[date] = []
    # An excluded driver does not drive the insured units: no MVR is needed for them
    for d in [x for x in _drivers(ctx, only_driving=False) if not x.details.get("excluded")]:
        mvrs = cast(list[dict[str, Any]], d.details.get("mvrs", []))
        dates = [x for x in (_d(m.get("order_date")) for m in mvrs) if x]
        if not dates:
            problems.append(f"MVR for {d.label}")
            continue
        newest = max(dates)
        dated.append(newest)
        if (ctx.anchor - newest).days > max_age:
            problems.append(
                f"Current MVR for {d.label} (latest is {(ctx.anchor - newest).days} days old)"
            )
    if problems:
        return Verdict(
            Outcome.REFER,
            f"{_n(len(problems), 'scheduled driver')} without a current MVR",
            problems,
        )
    if not dated:
        return Verdict(Outcome.REFER, "No scheduled drivers were found")
    reason = f"MVRs for all {len(dated)} scheduled drivers, ordered within {max_age} days"
    # Drivers named in loss runs but on no schedule may still drive, with no MVR sent
    unlisted = [d for d in ctx.view.all("driver") if not d.scheduled and "loss_run" in d.found_in]
    if unlisted:
        names = ", ".join(d.label for d in unlisted)
        return Verdict(
            Outcome.FLAG,
            f"{reason}; {names} drove in a reported claim but {'is' if len(unlisted) == 1 else 'are'} "
            "not on the driver schedule",
            [f"{d.label}: on a loss run, not scheduled; no MVR" for d in unlisted],
        )
    return Verdict(Outcome.PASS, reason)


@predicate
def vehicle_schedule_complete(ctx: RuleContext) -> Verdict:
    vehicles = [v for v in ctx.view.all("vehicle") if v.scheduled]
    if not vehicles:
        return Verdict(Outcome.REFER, "No vehicle schedule was found")
    items: list[str] = []
    for v in vehicles:
        gaps: list[str] = []
        vin = v.field("vin")
        if vin is None or vin.state == "missing":
            gaps.append("the VIN")
        elif vin.state == "invalid":
            gaps.append("a valid 17-character VIN")
        if not v.value("year"):
            gaps.append("the model year")
        if not v.value("make") and (v.field("make") is None or v.field("make").state != "conflict"):  # type: ignore[union-attr]
            gaps.append("the make")
        if not v.value("stated_value"):
            gaps.append("the stated value")
        if gaps:
            asks = gaps[0] if len(gaps) == 1 else f"{', '.join(gaps[:-1])} and {gaps[-1]}"
            items.append(f"{v.label}: send {asks}")
    if items:
        return Verdict(
            Outcome.REFER, f"{len(items)} of {len(vehicles)} scheduled units are incomplete", items
        )
    return Verdict(Outcome.PASS, f"All {len(vehicles)} units have year, make, VIN and stated value")


# --- appetite and eligibility --------------------------------------------------------------


@predicate
def eligible_units(ctx: RuleContext) -> Verdict:
    bad_words = [str(w) for w in ctx.params["ineligible_units"]]
    found: list[str] = []
    untyped: list[str] = []
    for v in ctx.view.all("vehicle"):
        # Read only what the unit has: a scheduled unit's model is its type; a unit with
        # neither is the one fact this rule is missing
        text = " ".join(
            str(x)
            for x in (
                v.value(attr) if v.peek_field(attr) else None for attr in ("vehicle_type", "model")
            )
            if x
        ).lower()
        if not text:
            ctx.view.absent.add(f"{v.label}: Vehicle type")
            untyped.append(v.label)
            continue
        if any(w in text for w in bad_words):
            found.append(f"{v.label} ({text})")
    ops = ctx.view.one("applicant")
    operation = str(ops.value("operation") or "").lower() if ops else ""
    if any(w in operation for w in bad_words):
        found.append(f"Operation described as {operation!r}")
    if found:
        return Verdict(Outcome.DECLINE, "Ineligible unit types are scheduled", found)
    scheduled_untyped = [
        label
        for label in untyped
        if any(v.label == label and v.scheduled for v in ctx.view.all("vehicle"))
    ]
    if scheduled_untyped:
        return Verdict(
            Outcome.UNKNOWN, "Unit type is not stated for some scheduled units", scheduled_untyped
        )
    return _unless_unlisted(ctx, Verdict(Outcome.PASS, "Scheduled units are tractors"), "vehicle")


def _domicile(ctx: RuleContext) -> tuple[str | None, str]:
    a = ctx.view.one("applicant")
    if a is None:
        return None, ""
    for attr, label in (
        ("garaging_state", "garaging address"),
        ("mailing_state", "mailing address"),
    ):
        code = a.value(attr)
        if code:
            return str(code), label
    return None, ""


@predicate
def available_state(ctx: RuleContext) -> Verdict:
    state, source = _domicile(ctx)
    if state is None:
        return Verdict(
            Outcome.UNKNOWN,
            "The account's home state is not shown",
            ["Garaging address"],
            waiting_on=[GARAGING],
        )
    available = [str(s) for s in ctx.params["available_states"]]
    selective = [str(s) for s in ctx.params["selective_states"]]
    basis = f"{geo.name(state) if state in geo.STATES else state}, from the {source}"
    if state in selective:
        return Verdict(Outcome.REFER, f"{basis}: accepted on a selective basis only")
    if state not in available:
        return Verdict(Outcome.DECLINE, f"{basis}: not an available state")
    note = " (no garaging address was given)" if source != "garaging address" else ""
    return Verdict(Outcome.PASS, f"{basis}{note}")


@predicate
def operations_in_listed_states(ctx: RuleContext) -> Verdict:
    """The other reading of "non-listed states": where the trucks run, not where the account is
    based. Measured by IFTA miles outside the program's available states."""
    ops = ctx.view.one("operations")
    miles = {
        s: Decimal(str(m))
        for s, m in cast(
            dict[str, Any], _ifta(ops).get("miles_by_state", {}) if ops else {}
        ).items()
    }
    total = sum(miles.values(), Decimal(0))
    if not total:
        return Verdict(Outcome.UNKNOWN, "No IFTA mileage by state to check where the trucks run")
    listed = {str(s) for s in ctx.params["available_states"]}
    outside = {s: m for s, m in miles.items() if s not in listed and m > 0}
    share = sum(outside.values(), Decimal(0)) / total
    limit = Decimal(str(ctx.params["non_listed_miles_max_share"]))
    top = ", ".join(
        f"{geo.name(s) if s in geo.STATES else s} {m:,.0f} mi"
        for s, m in sorted(outside.items(), key=lambda kv: -kv[1])[:5]
    )
    if not outside:
        return Verdict(Outcome.PASS, "Every IFTA mile is in a program state")
    reason = (
        f"{share * 100:.0f}% of IFTA miles are in {_n(len(outside), 'state')} outside the program list"
        f" (largest: {top})"
    )
    if share > limit:
        return Verdict(
            Outcome.DECLINE,
            reason,
            ["Confirm where the trucks are dispatched and garaged, and the states they run in"],
        )
    return Verdict(Outcome.FLAG, f"{reason}, within the {limit * 100:.0f}% tolerance")


@predicate
def texas_north_of_i10(ctx: RuleContext) -> Verdict:
    state, _ = _domicile(ctx)
    if state != "TX":
        return Verdict(Outcome.NA, "Not a Texas account")
    return Verdict(
        Outcome.UNKNOWN,
        "Texas account: where it operates relative to I-10 is not shown",
        ["Terminal and routes relative to I-10"],
        waiting_on=[GARAGING],
    )


@predicate
def prohibited_operations(ctx: RuleContext) -> Verdict:
    words = [str(w) for w in ctx.params["prohibited_commodities"]]
    ops = ctx.view.one("operations")
    hits: list[str] = []
    answered: list[str] = []
    if ops:
        for key, label in (
            ("q_hazmat", "hazardous materials"),
            ("q_intermodal", "intermodal containers"),
        ):
            f = ops.field(key)
            if f is None:
                continue
            if f.state == "conflict":
                return Verdict(Outcome.REFER, f"Forms disagree on {label}")
            if f.value is True:
                hits.append(f"Hauls {label}")
            elif f.value is False:
                answered.append(label)
        for block_key in ("commodities_application", "commodities_quote"):
            block = cast(dict[str, Any] | None, ops.details.get(block_key))
            for item in cast(list[dict[str, Any]], (block or {}).get("items", [])):
                label = str(item.get("label", ""))
                if any(re.search(rf"\b{re.escape(w)}", label, re.IGNORECASE) for w in words):
                    hits.append(f"Commodity: {label}")
    if hits:
        return Verdict(Outcome.DECLINE, "Prohibited cargo or operations", sorted(set(hits)))
    reason = "No prohibited cargo or operations declared"
    if answered:
        reason += f" (self-reported: no {', no '.join(answered)})"
    lithium_risk = ops and any(
        re.search(r"appliance|electronic|household", str(i.get("label")), re.IGNORECASE)
        for b in ("commodities_application", "commodities_quote")
        for i in cast(
            list[dict[str, Any]],
            (cast(dict[str, Any] | None, ops.details.get(b)) or {}).get("items", []),
        )
    )
    if lithium_risk:
        return Verdict(
            Outcome.FLAG,
            reason + "; appliance and household-goods loads can include lithium batteries",
            ["Confirm no lithium battery cargo (appliances and electronics are hauled)"],
        )
    return Verdict(Outcome.PASS, reason)


@predicate
def border_distance(ctx: RuleContext) -> Verdict:
    state, source = _domicile(ctx)
    ops = ctx.view.one("operations")
    miles = cast(dict[str, str], _ifta(ops).get("miles_by_state", {})) if ops else {}
    border_miles = {s: m for s, m in miles.items() if s in BORDER_STATES and Decimal(m) > 0}
    if state not in BORDER_STATES and not border_miles:
        return Verdict(Outcome.PASS, "Neither based in nor running in a border state")
    where = ", ".join(
        f"{geo.name(s)} {Decimal(m):,.0f} mi"
        for s, m in sorted(border_miles.items(), key=lambda sm: (-Decimal(sm[1]), sm[0]))
    )
    reason = (
        f"Based in {geo.name(state) if state in geo.STATES else 'an unknown state'} ({source})"
        if state
        else "Home state unknown"
    )
    if where:
        reason += f"; IFTA shows miles in border states ({where})"
    return Verdict(
        Outcome.UNKNOWN,
        reason
        + ". Distance from the Mexico border cannot be checked without a garaging address and routes",
        [
            "Garaging address, and whether any terminal or regular route is within 50 miles of the Mexico border"
        ],
        waiting_on=[GARAGING],
    )


@predicate
def safer_record(ctx: RuleContext) -> Verdict:
    a = ctx.view.one("applicant")
    dot = a.display("dot_number") if a else None
    target = f"USDOT {dot}" if dot else "the carrier"
    return Verdict(
        Outcome.UNKNOWN,
        f"No FMCSA SAFER data in this prototype; production looks up {target} automatically",
        [],
    )


@predicate
def new_venture(ctx: RuleContext) -> Verdict:
    ops, a = ctx.view.one("operations"), ctx.view.one("applicant")
    is_new = ops.value("new_venture") if ops else None
    years = a.value("years_experience") if a else None
    if is_new is False or (years is not None and Decimal(str(years)) >= 2):
        detail = f"{years} years in business" if years is not None else "not a new venture"
        return Verdict(Outcome.NA, f"Not a new venture ({detail})")
    if is_new is None and years is None:
        return Verdict(Outcome.UNKNOWN, "Whether this is a new venture is not shown")
    form = a.value("entity_form") if a else None
    if form == "CORPORATION":
        return Verdict(
            Outcome.REFER, "New venture organized as a corporation: subject to underwriter review"
        )
    return Verdict(
        Outcome.REFER,
        "New venture: needs 2 years of documented CDL experience and a letter of experience",
        ["Resume or letter of experience", "Proof of 2 years of CDL experience"],
    )


# --- coverage ------------------------------------------------------------------------------


@predicate
def no_liability_deductible(ctx: RuleContext) -> Verdict:
    r = ctx.view.one("coverage_request")
    text = str(r.value("auto_liability_deductible") or "") if r else ""
    amount = nz.money(re.sub(r"[^\d.,$-]", "", text)) if text else None
    if amount and amount > 0:
        return Verdict(
            Outcome.REFER, f"An auto liability deductible of ${amount:,.0f} was requested"
        )
    shown = f" ({text})" if text else ""
    return Verdict(Outcome.PASS, f"No liability deductible requested{shown}")


@predicate
def no_physical_damage(ctx: RuleContext) -> Verdict:
    r = ctx.view.one("coverage_request")
    f = r.field("physical_damage_requested") if r else None
    if f is not None and (f.value is True or f.state == "conflict"):
        return Verdict(
            Outcome.FLAG,
            "Physical damage was requested; the program does not offer it",
            [
                "Physical damage is not available in this program; tell the agent to place it elsewhere"
            ],
        )
    return Verdict(Outcome.PASS, "Physical damage was not requested")


# --- drivers -------------------------------------------------------------------------------


@predicate
def drivers_hold_cdl(ctx: RuleContext) -> Verdict:
    drivers = _drivers(ctx, only_driving=True, judges_owner=True)
    if not drivers:
        return Verdict(Outcome.UNKNOWN, "No scheduled drivers were found")
    failing: list[str] = []
    unknown: list[str] = []
    for d in drivers:
        cdl = d.value("commercial_license")
        status = str(d.value("license_status") or "").upper()
        expires = _d(d.value("license_expiration"))
        if cdl is False:
            cls = d.display("license_class")
            failing.append(
                f"{d.label} holds a non-commercial license" + (f" (class {cls})" if cls else "")
            )
        elif status and not status.startswith("VALID"):
            failing.append(f"{d.label}: license status {status}")
        elif expires and expires < ctx.anchor:
            failing.append(f"{d.label}: license expired {_fmt(expires)}")
        elif cdl is None:
            unknown.append(d.label)
    if failing:
        owner_note = ""
        if any(d.details.get("owner_marker") for d in drivers):
            owner_note = " An owner is listed as a driver; if the owner does not drive, exclude them by name."
        return _unless_unlisted(
            ctx,
            Verdict(
                Outcome.DECLINE,
                f"{_n(len(failing), 'scheduled driver')} {'does' if len(failing) == 1 else 'do'} not meet the CDL requirement.{owner_note}",
                failing,
            ),
            "driver",
            name_always=True,
        )
    if unknown:
        return _unless_unlisted(
            ctx,
            Verdict(Outcome.UNKNOWN, "CDL status is not shown for some drivers", unknown),
            "driver",
            name_always=True,
        )
    return _unless_unlisted(
        ctx,
        Verdict(Outcome.PASS, f"All {len(drivers)} drivers hold a valid commercial license"),
        "driver",
    )


def _cdl_years(d: EntityView, anchor: date) -> Decimal | None:
    stated = d.value("experience_stated")
    best: Decimal | None = None
    if stated:
        m = re.search(r"\d+(\.\d+)?", str(stated))
        if m:
            best = Decimal(m.group(0))
    card = cast(dict[str, Any] | None, d.details.get("license_card"))
    issued = d.field("license_issued")
    earliest = earliest_cdl(issued.candidates if issued else [], card)
    if earliest:
        years = _years(earliest[0], anchor)
        best = years if best is None else max(best, years)
    return best


@predicate
def driver_experience(ctx: RuleContext) -> Verdict:
    minimum = Decimal(str(ctx.params["min_cdl_years"]))
    short: list[str] = []
    unknown: list[str] = []
    no_cdl: list[str] = []
    for d in _drivers(ctx, only_driving=True):
        if d.value("commercial_license") is False:
            no_cdl.append(d.label)  # no CDL at all: covered by DRV-01, not counted twice
            continue
        years = _cdl_years(d, ctx.anchor)
        if years is None:
            unknown.append(d.label)
        elif years < minimum:
            short.append(f"{d.label}: about {years} years")
    if short:
        return Verdict(
            Outcome.REFER,
            f"{_n(len(short), 'driver')} with less than {minimum} years of CDL experience",
            short,
        )
    if unknown:
        return Verdict(
            Outcome.UNKNOWN, "CDL experience is not documented for some drivers", unknown
        )
    aside = (
        f"; {', '.join(no_cdl)} {'holds' if len(no_cdl) == 1 else 'hold'} no CDL (see DRV-01)"
        if no_cdl
        else ""
    )
    return _unless_unlisted(
        ctx,
        Verdict(
            Outcome.PASS,
            f"Every driver with a CDL shows at least {minimum} years (stated experience or CDL "
            f"issue date){aside}",
        ),
        "driver",
        "CDL experience unknown",
    )


@predicate
def driver_age(ctx: RuleContext) -> Verdict:
    minimum = int(ctx.params["min_driver_age"])
    on = _ages_on(ctx)
    young: list[str] = []
    unknown: list[str] = []
    for d in _drivers(ctx, only_driving=True):
        dob = _d(d.value("date_of_birth"))
        if dob is None:
            unknown.append(d.label)
        elif age_on(dob, on) < minimum:
            young.append(f"{d.label}: {age_on(dob, on)}")
    if young:
        return Verdict(Outcome.REFER, f"{_n(len(young), 'driver')} under {minimum}", young)
    if unknown:
        return Verdict(Outcome.UNKNOWN, "Date of birth is not shown for some drivers", unknown)
    return Verdict(Outcome.PASS, f"All drivers are {minimum} or older")


@predicate
def senior_medical(ctx: RuleContext) -> Verdict:
    age = int(ctx.params["medical_exam_age"])
    on = _ages_on(ctx)
    seniors = [
        f"{d.label}: {age_on(dob, on)}"
        for d in _drivers(ctx, only_driving=True)
        if (dob := _d(d.value("date_of_birth"))) and age_on(dob, on) >= age
    ]
    if seniors:
        return Verdict(
            Outcome.REFER,
            f"{_n(len(seniors), 'driver')} aged {age} or older need a DOT medical report",
            seniors,
        )
    return Verdict(Outcome.NA, f"No drivers are {age} or older")


def _events(d: EntityView) -> list[dict[str, Any]]:
    return points.events(cast(list[dict[str, Any]], d.details.get("mvrs", [])))


_COUNTED_KINDS = re.compile(r"violation|accident|conviction|suspension|revocation", re.IGNORECASE)


def _counted_facts(ctx: RuleContext) -> list[str]:
    """The MVR lines a driving-record rule weighs: violations, accidents and anything with
    points; not renewals, address changes or the order date."""
    out: list[str] = []
    for d in _drivers(ctx, only_driving=True):
        for e in _events(d):
            text = f"{e.get('kind') or ''} {e.get('description') or ''}"
            if nz.number(str(e.get("points") or "")) or _COUNTED_KINDS.search(text):
                out += [str(x) for x in cast(list[Any], e.get("fact_ids") or [])]
    return out


@predicate
def driver_points(ctx: RuleContext) -> Verdict:
    max3, max1 = int(ctx.params["max_points_3y"]), int(ctx.params["max_points_12m"])
    over: list[str] = []
    notes: list[str] = []
    for d in _drivers(ctx, only_driving=True):
        pts = points.count(_events(d), ctx.anchor)
        notes.extend(
            f"{d.label}: {n} points on {_fmt(when)}, just outside the 3-year window"
            for when, n in pts.outside
        )
        if pts.three_years > max3 or pts.twelve_months > max1:
            over.append(
                f"{d.label}: {pts.three_years} points in 3 years, {pts.twelve_months} in 12 months"
            )
    cited = _counted_facts(ctx)
    if over:
        return Verdict(Outcome.REFER, "Drivers over the point guideline", over, cited)
    reason = f"No driver exceeds {max3} points in 3 years or {max1} in 12 months"
    passed = Verdict(
        Outcome.PASS, reason + (f". Note: {'; '.join(notes)}" if notes else ""), [], cited
    )
    return _unless_unlisted(ctx, passed, "driver")


@predicate
def unacceptable_history(ctx: RuleContext) -> Verdict:
    patterns = cast(list[dict[str, Any]], ctx.params["unacceptable_history"])
    hits: list[str] = []
    for d in _drivers(ctx, only_driving=True):
        for e in _events(d):
            text = f"{e.get('description') or ''} {e.get('kind') or ''}"
            when = _d(e.get("event_date")) or _d(e.get("conviction_date"))
            for p in patterns:
                if not re.search(str(p["pattern"]), text, re.IGNORECASE):
                    continue
                years = p.get("years")
                if (
                    years
                    and when
                    and when < ctx.anchor - timedelta(days=round(365.25 * int(years)))
                ):
                    continue
                hits.append(
                    f"{d.label}: {p['label']} ({text.strip()}{', ' + _fmt(when) if when else ''})"
                )
    cited = _counted_facts(ctx)
    if hits:
        return Verdict(Outcome.DECLINE, "Unacceptable driving history", hits, cited)
    return _unless_unlisted(
        ctx,
        Verdict(Outcome.PASS, "No unacceptable driving history on the MVRs", [], cited),
        "driver",
    )


# --- prior insurance -----------------------------------------------------------------------


@predicate
def continuous_coverage(ctx: RuleContext) -> Verdict:
    years = int(ctx.params["continuous_years"])
    max_lapse = int(ctx.params["max_lapse_days"])
    terms = _al_terms(ctx.view)
    if not terms:
        return Verdict(Outcome.UNKNOWN, "No prior liability terms are listed")
    window_start = ctx.anchor - timedelta(days=round(365.25 * years))
    intervals = sorted(
        (s, e or s + timedelta(days=365))
        for t in terms
        if (s := _d(t.details["start"])) is not None
        for e in (_d(t.details.get("end")),)
    )
    uncovered = 0
    cursor = window_start
    for s, e in intervals:
        if s > cursor:
            uncovered += (min(s, ctx.anchor) - cursor).days
        cursor = max(cursor, e)
        if cursor >= ctx.anchor:
            break
    if cursor < ctx.anchor:
        uncovered += (ctx.anchor - cursor).days
    # Lapses within the most recent years only: one that ended before the window is history
    lapses = [
        (e1, s2, (s2 - e1).days)
        for (_s1, e1), (s2, _e2) in pairwise(intervals)
        if (s2 - e1).days > max_lapse and s2 > window_start
    ]
    items = [f"Explain the {days}-day gap from {_fmt(a)} to {_fmt(b)}" for a, b, days in lapses]
    if uncovered > max_lapse:
        return Verdict(
            Outcome.REFER,
            f"{uncovered} days without liability coverage in the {years} years before the {ctx.anchor_name}",
            items or [f"Coverage history for the {years} years before the {ctx.anchor_name}"],
        )
    if lapses:
        return Verdict(
            Outcome.REFER,
            f"Continuous for the {years} years before the {ctx.anchor_name}, but a lapse over {max_lapse} days within them must be explained",
            items,
        )
    return Verdict(
        Outcome.PASS,
        f"Continuous liability coverage for the {years} years before the {ctx.anchor_name}",
    )


@predicate
def losses_for_every_term(ctx: RuleContext) -> Verdict:
    listed = [t for t in ctx.view.all("coverage_term") if t.details.get("listed_on_application")]
    items = _loss_gap_items(listed)
    if items:
        gaps = sum(1 for t in listed if _loss_gap(t) is not None)
        return Verdict(
            Outcome.REFER, f"{_n(gaps, 'listed term')} without a complete loss run", items
        )
    return Verdict(Outcome.PASS, "Every listed term has a loss run")


# --- routing -------------------------------------------------------------------------------


def power_unit_counts(view: SubmissionView) -> list[tuple[int, str]]:
    """Every power-unit count the documents support, each with where it comes from: each stated
    count, and each count plus units found only elsewhere (a claim, not a schedule)."""
    sources: dict[int, list[str]] = {}
    fleet = view.one("fleet")
    f = fleet.field("power_units") if fleet else None
    extra = sum(1 for v in view.all("vehicle") if not v.scheduled)
    scheduled = sum(1 for v in view.all("vehicle") if v.scheduled)
    for cand in f.candidates if f else []:
        try:
            n = int(float(str(cand.get("value"))))
        except (TypeError, ValueError):
            continue
        for src in cand.get("sources", []):
            name = str(src.get("title", "")).split(",")[0].lower()
            sources.setdefault(n, []).append(name)
            if extra:
                sources.setdefault(n + extra, []).append(
                    f"{name} plus {extra} found only in loss history"
                )
    if extra and scheduled + extra not in sources:
        sources[scheduled + extra] = [f"schedule plus {extra} found only in loss history"]
    # Documents that state the count first, then derived counts, each alphabetically
    return [
        (n, f"{n} ({'; '.join(sorted(set(s), key=lambda x: (' plus ' in x, x)))})")
        for n, s in sorted(sources.items())
    ]


def team_for(units: int, params: dict[str, Any]) -> str:
    return "small_fleet" if units <= int(params["small_fleet_max_units"]) else "fleet"


@predicate
def route_team(ctx: RuleContext) -> Verdict:
    counts = power_unit_counts(ctx.view)
    small_max = int(ctx.params["small_fleet_max_units"])
    if not counts:
        return Verdict(Outcome.UNKNOWN, "Power unit count is not known", ["Number of power units"])
    teams = {team_for(n, ctx.params) for n, _ in counts}
    notes = ", ".join(note for _, note in counts)
    if len(teams) == 1:
        team = teams.pop()
        label = ctx.teams.get(team, team)
        if len(counts) == 1:
            return Verdict(Outcome.PASS, f"{label}: {notes}")
        return Verdict(Outcome.PASS, f"{label} under every count the documents give: {notes}")
    fleet = ctx.teams.get("fleet", "fleet")
    return Verdict(
        Outcome.REFER,
        f"Counts differ across the {small_max}-unit line ({notes}); sent to {fleet}",
        ["Confirm the number of power units"],
    )


def route_rows(ctx: RuleContext) -> list[dict[str, Any]]:
    """ROUTE-01's sensitivity: the team under each count."""
    fleet = ctx.view.one("fleet")
    f = fleet.peek_field("power_units") if fleet else None
    counts = power_unit_counts(ctx.view)
    stable = len({team_for(n, ctx.params) for n, _ in counts}) == 1
    return [
        {
            "field_id": f.id if f else "",
            "field": "Power units",
            "value": note,
            "outcome": Outcome.PASS.value if stable else Outcome.REFER.value,
            "result": ctx.teams.get(team_for(n, ctx.params), team_for(n, ctx.params)),
        }
        for n, note in counts
    ]


@predicate
def final_route(ctx: RuleContext) -> Verdict:
    """Filled in by the engine once every other rule has run."""
    return Verdict(Outcome.UNKNOWN, "The route is decided after every other rule")


# --- selective -----------------------------------------------------------------------------


@predicate
def premium_per_unit(ctx: RuleContext) -> Verdict:
    return Verdict(
        Outcome.UNKNOWN,
        f"Premium is not known at intake, so the ${int(ctx.params['min_premium_per_unit']):,} per unit test cannot run yet",
    )


@predicate
def selective_units(ctx: RuleContext) -> Verdict:
    hits = [
        v.label
        for v in ctx.view.all("vehicle")
        if re.search(
            r"box|van|car hauler|auto hauler",
            " ".join(str(x) for x in (v.value("vehicle_type"), v.value("model")) if x),
            re.IGNORECASE,
        )
    ]
    ops = ctx.view.one("operations")
    for b in ("commodities_application", "commodities_quote"):
        for i in cast(
            list[dict[str, Any]],
            (cast(dict[str, Any] | None, ops.details.get(b) if ops else None) or {}).get(
                "items", []
            ),
        ):
            if re.search(
                r"auto(mobile)? haul|car haul|vehicles", str(i.get("label")), re.IGNORECASE
            ):
                hits.append(f"Commodity: {i.get('label')}")
    if hits:
        return Verdict(Outcome.REFER, "Selective exposures are present", hits)
    return Verdict(Outcome.NA, "No auto hauling, box trucks or transit vans")


@predicate
def no_open_conflicts(ctx: RuleContext) -> Verdict:
    settled = (
        f". Settled by code, not counted: {', '.join(ctx.settled_conflicts)} (VIN decode)"
        if ctx.settled_conflicts
        else ""
    )
    if ctx.open_conflicts:
        return Verdict(
            Outcome.REFER,
            f"{_n(len(ctx.open_conflicts), 'fact')} conflicting between documents, unresolved"
            + settled,
            list(ctx.open_conflicts),
        )
    return Verdict(Outcome.PASS, "Documents agree" + settled)
