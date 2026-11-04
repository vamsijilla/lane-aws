"""What is missing or incomplete, in the words an underwriter would put in a request to the agent.

These are facts about the submission, not decisions: the rules engine (P6) decides which ones
block a quote under the guideline.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from lane.domain import normalize as nz
from lane.resolve.board import Board, Entity
from lane.resolve.unread import UnreadSource, names


@dataclass
class MissingItem:
    code: str
    title: str
    detail: str
    category: str  # application | drivers | vehicles | loss_history | operations
    entity_kind: str | None = None
    entity_key: str | None = None
    document_ids: list[str] = field(default_factory=lambda: list[str]())


def _empty(e: Entity, attr: str) -> bool:
    f = e.fields.get(attr)
    return f is None or f.state == "missing"


# Which document types could hold what a missing item asks for, by code prefix. A file we could
# not read only casts doubt on the items it could hold; an unopened file could hold any.
_APPLICANT = ("application", "quote_form", "email_body")
_LOSSES = ("loss_run", "loss_history_letter")
HELD_BY: dict[str, tuple[str, ...]] = {
    "APP_": _APPLICANT,
    "Q_": _APPLICANT,
    "VEH_": ("vehicle_schedule", "application", "quote_form"),
    "DRV_": ("driver_schedule", "mvr", "drivers_license", "application"),
    "LOSS_": _LOSSES,
    "LAPSE": (*_LOSSES, "application", "quote_form"),
    "IFTA_": ("ifta_return",),
}


def _held_by(code: str) -> tuple[str, ...] | None:
    return next((types for prefix, types in HELD_BY.items() if code.startswith(prefix)), None)


def _caveated(items: list[MissingItem], unread: Sequence[UnreadSource]) -> list[MissingItem]:
    """Never claim absence flatly while a file that could hold the answer was not read; ask for
    a readable copy of each such file instead."""
    if not unread:
        return items
    for item in items:
        types = _held_by(item.code)
        doubt = [u for u in unread if types is None or u.could_hold(types)]
        if doubt:
            item.detail = (
                f"{item.detail.rstrip('.')}. {names(doubt)} could not be read and may hold it."
            )
    out = list(items)
    for u in unread:
        may = [i.title for i in items if (t := _held_by(i.code)) is None or u.could_hold(t)]
        hold = f" It may hold: {', '.join(dict.fromkeys(may))}." if may else ""
        out.append(
            MissingItem(
                f"UNREADABLE:{u.filename}",
                "A file could not be read",
                f"{u.filename} could not be read ({u.reason}). Ask for a readable copy.{hold}",
                "documents",
            )
        )
    return out


def find_missing(board: Board, unread: Sequence[UnreadSource] = ()) -> list[MissingItem]:
    out: list[MissingItem] = []
    applicant = board.entity("applicant", "applicant")
    ops = board.entity("operations", "operations")

    for attr, code, title, detail in (
        ("fein", "APP_FEIN", "FEIN or SSN", "The application has no FEIN or SSN."),
        (
            "garaging_address",
            "APP_GARAGING",
            "Garaging address",
            "The application's garaging address is blank.",
        ),
        (
            "date_of_authority",
            "APP_AUTHORITY",
            "Date of authority",
            "The date operating authority was granted is blank.",
        ),
        (
            "business_start",
            "APP_BUSINESS_START",
            "First date in business",
            "The first date in business is blank.",
        ),
        (
            "signature",
            "APP_SIGNATURE",
            "Signed and dated application",
            "The application is not signed.",
        ),
    ):
        if _empty(applicant, attr):
            out.append(MissingItem(code, title, detail, "application", "applicant", "applicant"))

    history = cast(dict[str, Any], applicant.details.get("history_application") or {})
    rows = cast(list[dict[str, Any]], history.get("rows", []))
    if rows and not any(r.get("premium") or r.get("power_units") for r in rows):
        out.append(
            MissingItem(
                "APP_HISTORY",
                "Historical premium and power units",
                f"The historical premium and power units table is blank on the {history['title']}.",
                "application",
                "applicant",
                "applicant",
                document_ids=[str(history["document_id"])],
            )
        )

    for q in cast(list[dict[str, Any]], ops.details.get("unanswered_questions", [])):
        out.append(
            MissingItem(
                f"Q_{str(q['key']).upper()}",
                "Unanswered question",
                f"{q['question']} (left blank on the {q['title']})",
                "operations",
                document_ids=[str(q["document_id"])],
            )
        )

    scheduled = [v for v in board.of_kind("vehicle") if v.details.get("scheduled")]
    no_value = [v.label for v in scheduled if _empty(v, "stated_value")]
    if no_value:
        out.append(
            MissingItem(
                "VEH_STATED_VALUE",
                "Stated value for each vehicle",
                f"No stated value for {len(no_value)} of {len(scheduled)} scheduled units"
                + (
                    " (only cost new is given)."
                    if all(not _empty(v, "cost_new") for v in scheduled)
                    else "."
                ),
                "vehicles",
            )
        )
    for v in board.of_kind("vehicle"):
        vin_field = v.fields.get("vin")
        if vin_field and vin_field.state == "invalid":
            out.append(
                MissingItem(
                    f"VEH_VIN:{v.key}",
                    "Valid 17-character VIN",
                    f"{v.label}: {'; '.join(vin_field.invalid)}.",
                    "vehicles",
                    "vehicle",
                    v.key,
                )
            )
        if not v.details.get("scheduled"):
            out.append(
                MissingItem(
                    f"VEH_UNLISTED:{v.key}",
                    "Vehicle in a claim but not on the schedule",
                    f"{v.label} appears in loss history. Is it still operated, is it a tractor or a straight truck, and why is it not scheduled?",
                    "vehicles",
                    "vehicle",
                    v.key,
                    list(v.sources),
                )
            )

    for d in board.of_kind("driver"):
        if d.details.get("scheduled") and not d.details.get("mvrs"):
            out.append(
                MissingItem(
                    f"DRV_MVR:{d.key}",
                    "Current MVR",
                    f"No MVR was sent for {d.label}.",
                    "drivers",
                    "driver",
                    d.key,
                )
            )
        if not d.details.get("scheduled"):
            out.append(
                MissingItem(
                    f"DRV_UNLISTED:{d.key}",
                    "Driver in a claim but not on the schedule",
                    f"Does {d.label} still drive? If so, send their MVR; if not, confirm they "
                    "no longer drive.",
                    "drivers",
                    "driver",
                    d.key,
                    list(d.sources),
                )
            )

    for t in board.of_kind("coverage_term"):
        evidence = cast(list[dict[str, Any]], t.details.get("loss_evidence", []))
        if not evidence:
            out.append(
                MissingItem(
                    f"LOSS_TERM:{t.key}",
                    "Loss history for a listed term",
                    f"{t.label}: listed as prior insurance, but no loss run was sent.",
                    "loss_history",
                    "coverage_term",
                    t.key,
                )
            )
        elif all(not e.get("complete") for e in evidence):
            statement = next((e.get("statement") for e in evidence if e.get("statement")), None)
            scope = next(
                (e.get("scope") for e in evidence if e.get("scope")), "part of the history"
            )
            out.append(
                MissingItem(
                    f"LOSS_PARTIAL:{t.key}",
                    "Complete loss run",
                    f"{t.label}: only a letter was sent"
                    + (f' ("{statement}")' if statement else "")
                    + (
                        ". It covers paid losses only and does not show open claims or reserves."
                        if "PAID" in str(scope).upper()
                        else f". It covers {scope}; a full loss run is needed."
                    ),
                    "loss_history",
                    "coverage_term",
                    t.key,
                    [str(e["document_id"]) for e in evidence],
                )
            )

    history = board.entity("coverage_history", "history")
    for gap in cast(list[dict[str, Any]], history.details.get("gaps", [])):
        out.append(
            MissingItem(
                f"LAPSE:{gap['from']}",
                "Explanation of a coverage gap",
                f"No coverage shown ({' and '.join(gap.get('lines') or ['liability'])}) from "
                f"{nz.long_date(gap['from'])} to {nz.long_date(gap['to'])} ({gap['days']} days).",
                "loss_history",
            )
        )

    quarters = cast(
        dict[str, Any], board.entity("operations", "operations").details.get("ifta", {})
    ).get("quarters", [])
    if len(quarters) < 4:
        out.append(
            MissingItem(
                "IFTA_QUARTERS",
                "Four quarters of IFTA returns",
                f"{len(quarters)} quarter(s) were sent.",
                "operations",
            )
        )
    return _caveated(out, unread)
