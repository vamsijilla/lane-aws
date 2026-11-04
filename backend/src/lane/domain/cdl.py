"""The earliest evidence of a CDL: the oldest issue date any document shows.

A renewal re-issues a license, so the newest MVR can show a recent issue date while an older
license card shows the original. CDL experience counts from the earliest date any document
gives, and says which document that is.
"""

import re
from datetime import date
from typing import Any, cast

from lane.domain import normalize as nz

SOURCE_NAMES = {"drivers_license": "license card", "mvr": "MVR", "driver_schedule": "driver list"}


def _issued(
    issued_candidates: list[dict[str, Any]], card: dict[str, Any] | None
) -> list[tuple[date, str]]:
    found: list[tuple[date, str]] = []
    for c in issued_candidates:
        d = nz.as_date(c.get("value")) or nz.as_date(c.get("display"))
        if d is None:
            continue
        sources = cast(list[dict[str, Any]], c.get("sources") or [{}])
        for s in sources:
            doc_type = str(s.get("doc_type", ""))
            found.append((d, SOURCE_NAMES.get(doc_type, str(s.get("title") or "a document"))))
    if card and (d := nz.as_date(card.get("issued"))):
        found.append((d, "license card"))
    return found


def earliest_cdl(
    issued_candidates: list[dict[str, Any]], card: dict[str, Any] | None
) -> tuple[date, str] | None:
    """(date, 'license card') from every issue date, superseded ones included."""
    found = _issued(issued_candidates, card)
    return min(found) if found else None


def latest_cdl_issue(
    issued_candidates: list[dict[str, Any]], card: dict[str, Any] | None
) -> tuple[date, str] | None:
    """The newest issue date any document shows (a renewal or a duplicate re-issues a CDL)."""
    found = _issued(issued_candidates, card)
    return max(found) if found else None


def from_history(mvrs: list[dict[str, Any]]) -> tuple[date, str] | None:
    """The oldest CDL event in MVR history (a renewal or clearance notice shows a CDL was held
    by then): (date, 'MVR history: CDL clearance notice')."""
    found: list[tuple[date, str]] = []
    for m in mvrs:
        for e in cast(list[dict[str, Any]], m.get("events") or []):
            if str(e.get("kind") or "").startswith("cdl") and (
                d := nz.as_date(e.get("event_date"))
            ):
                found.append((d, f"MVR history: {_cdl_note(str(e.get('description') or 'CDL'))}"))
        for n in cast(list[dict[str, Any]], m.get("notes") or []):
            text = str(n.get("text") or "")
            if "CDL" in text.upper() and (d := nz.as_date(n.get("note_date"))):
                found.append((d, f"MVR history: {_cdl_note(text)}"))
    return min(found) if found else None


def _cdl_note(text: str) -> str:
    """'CDL CLEARANCE NOTICE AC' -> 'CDL clearance notice'."""
    t = re.sub(r"\s+(AC|IN|ACT)(\s*-\s*\d+)?$", "", " ".join(text.split()))
    head, _, rest = t.partition(" ")
    return f"{head} {rest.lower()}".strip() if head.upper() == "CDL" else t.capitalize()


def describe(
    found: tuple[date, str] | None,
    history: tuple[date, str] | None = None,
    latest: tuple[date, str] | None = None,
) -> str | None:
    """The earliest evidence of a CDL, from an issue date or MVR history, whichever is older.
    A later issue date is shown beside it as the latest issue, which may be a renewal or a
    duplicate.
    'CDL issue date on record: Jun 4, 2023 (MVR; a renewal or duplicate resets this date, so the
    CDL may be older)'
    'Earliest CDL evidence: Oct 4, 2021 (MVR history: CDL clearance notice). Latest CDL issue
    date: Mar 18, 2024 (may be a renewal or duplicate)'"""
    candidates = [x for x in (found, history) if x is not None]
    if not candidates:
        return None
    earliest = min(candidates)
    if earliest is found and history is None:
        # Only an issue date: a renewal or duplicate re-issues the card, so the CDL may be older
        return (
            f"CDL issue date on record: {nz.long_date(earliest[0])} ({earliest[1]}; a renewal "
            "or duplicate resets this date, so the CDL may be older)"
        )
    text = f"Earliest CDL evidence: {nz.long_date(earliest[0])} ({earliest[1]})"
    if latest is not None and latest[0] > earliest[0]:
        text += (
            f". Latest CDL issue date: {nz.long_date(latest[0])} (may be a renewal or duplicate)"
        )
    return text
