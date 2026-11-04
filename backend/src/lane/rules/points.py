"""Driver points in the guideline's windows, shared by the rule and the driver card."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, cast

from lane.domain import normalize as nz

THREE_YEARS = timedelta(days=round(365.25 * 3))
ONE_YEAR = timedelta(days=365)


@dataclass
class Points:
    three_years: int = 0
    twelve_months: int = 0
    # Points just outside the 3-year window, worth a note (assumption A11)
    outside: list[tuple[date, int]] = field(default_factory=lambda: list[tuple[date, int]]())


def event_date(e: dict[str, Any]) -> date | None:
    """Points count from the violation date, else the conviction date (A11)."""
    for key in ("event_date", "conviction_date"):
        raw = e.get(key)
        if not raw:
            continue
        try:
            return date.fromisoformat(str(raw)[:10])
        except ValueError:
            if (d := nz.to_date(str(raw))) is not None:
                return d
    return None


def events(mvrs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every event across a driver's records, once: two abstracts of one license (a 3-year and
    a 7-year, or two order dates) repeat the same violations."""
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for m in mvrs:
        for e in cast(list[dict[str, Any]], m.get("events") or []):
            when = event_date(e)
            key = (
                when.isoformat() if when else "",
                " ".join(str(e.get("description") or e.get("kind") or "").upper().split()),
                str(e.get("points") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(e)
    return out


def count(events: Iterable[dict[str, Any]], anchor: date) -> Points:
    out = Points()
    for e in events:
        when = event_date(e)
        pts = nz.number(str(e.get("points") or ""))
        if when is None or pts is None:
            continue
        if when >= anchor - THREE_YEARS:
            out.three_years += int(pts)
        if when >= anchor - ONE_YEAR:
            out.twelve_months += int(pts)
        elif int(pts) and when < anchor - THREE_YEARS:
            out.outside.append((when, int(pts)))
    return out
