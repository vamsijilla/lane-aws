"""Self-checks on extracted values, named in the field catalog, and recall checks.

Validators catch values that cannot be right: a VIN whose check digit fails, a license issued
after it expires, paid plus reserved that is not incurred. A validator that fails on a value two
reads agree on is the document's own error (shown to the underwriter); on a single read it may
be a misread, and the document escalates.

Recall checks catch rows that were not read at all: a loss run that prints "TOTAL CLAIMS: 3"
but gave two claims, IFTA jurisdiction lines on the page that have no record, MVR event lines
without an event, worksheet rows outside the table that was mapped. Missing rows are never
silent.
"""

import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any, cast

from lane.domain import normalize as nz
from lane.domain import vin as vins
from lane.extract.catalog import entries
from lane.extract.checks import Check

STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
    "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM",
    "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY", "AB", "BC", "MB", "NB", "NL", "NS", "ON", "PE", "QC", "SK",
}  # fmt: skip

# License number formats by state, from AAMVA's published patterns (the common ones).
LICENSE_FORMATS: dict[str, str] = {
    "CA": r"[A-Z]\d{7}",
    "NJ": r"[A-Z]\d{14}",
    "TX": r"\d{8}",
    "FL": r"[A-Z]\d{12}",
    "NY": r"\d{9}|[A-Z]\d{7}|[A-Z]\d{18}|\d{8}|\d{16}|[A-Z]{8}",
    "AZ": r"[A-Z]\d{8}|\d{9}",
    "WI": r"[A-Z]\d{13}",
    "IL": r"[A-Z]\d{11,12}",
    "PA": r"\d{8}",
    "OH": r"[A-Z]{2}\d{6}|\d{8}",
    "GA": r"\d{7,9}",
    "NV": r"\d{9,10}|\d{12}|X\d{8}",
    "UT": r"\d{4,10}",
    "WA": r"[A-Z*]{5,7}[A-Z0-9*]{5}",
}

Data = dict[str, Any]


def _v(node: Any) -> str | None:
    """A scalar's value: {"value": ...} for PDFs, the cell dict for sheets, or a plain string."""
    if isinstance(node, dict):
        value = cast(dict[str, Any], node).get("value")
        return None if value in (None, "") else str(value)
    return None if node in (None, "") else str(node)


def _rows(data: Data, key: str) -> list[Data]:
    items = data.get(key)
    if not isinstance(items, list):
        return []
    return [cast(Data, i) for i in cast(list[Any], items) if isinstance(i, dict)]


def _state_code(text: str | None) -> str | None:
    if not text:
        return None
    t = text.strip().upper()
    if t in STATES:
        return t
    from lane.domain import geo

    return geo.to_code(t)


# --- single-value validators ---------------------------------------------------------------


def _vin(value: str, _ctx: Data, today: date) -> str | None:
    c = vins.check(value, current_year=today.year)
    return None if c.valid else f"VIN {c.vin}: {'; '.join(c.problems)}"


def _dot(value: str, _ctx: Data, _today: date) -> str | None:
    digits = re.sub(r"\D", "", value)
    return None if 1 <= len(digits) <= 8 else f"USDOT {value!r} is not 1 to 8 digits"


def _mc(value: str, _ctx: Data, _today: date) -> str | None:
    digits = re.sub(r"\D", "", value)
    return None if 1 <= len(digits) <= 7 else f"MC {value!r} is not 1 to 7 digits"


def _phone(value: str, _ctx: Data, _today: date) -> str | None:
    digits = re.sub(r"\D", "", value)
    return None if len(digits) in (10, 11) else f"Phone {value!r} is not a 10-digit number"


def _state(value: str, _ctx: Data, _today: date) -> str | None:
    t = value.strip().upper()
    if t in STATES or (t.replace(" ", "").isalpha() and len(t) > 3):
        return None
    return f"{value!r} is not a state"


def _license(value: str, ctx: Data, _today: date) -> str | None:
    state = _state_code(
        _v(ctx.get("license_state")) or _v(ctx.get("state")) or ctx.get("_doc_state")
    )
    pattern = LICENSE_FORMATS.get(state or "")
    if pattern is None:
        return None  # a format we do not know is not an error
    clean = re.sub(r"[\s-]", "", value.upper())
    return None if re.fullmatch(pattern, clean) else f"License {value!r} is not a {state} format"


def _issue_before_expiry(_value: str, ctx: Data, _today: date) -> str | None:
    issued = nz.to_date(_v(ctx.get("issue_date")))
    expires = nz.to_date(_v(ctx.get("expiration_date")))
    if issued and expires and issued >= expires:
        return f"Issued {issued.isoformat()} on or after it expires {expires.isoformat()}"
    return None


def _age_at_order(_value: str, ctx: Data, today: date) -> str | None:
    dob = nz.to_date(_v(ctx.get("date_of_birth")))
    order = nz.to_date(_v(ctx.get("order_date"))) or today
    if dob is None:
        return None
    age = order.year - dob.year - ((order.month, order.day) < (dob.month, dob.day))
    return None if 16 <= age <= 100 else f"Age {age} on the order date is not plausible"


def _report_after_loss(_value: str, ctx: Data, _today: date) -> str | None:
    loss = nz.to_date(_v(ctx.get("date_of_loss")))
    reported = nz.to_date(_v(ctx.get("date_reported")))
    if loss and reported and reported < loss:
        return f"Reported {reported.isoformat()} before the loss on {loss.isoformat()}"
    return None


def _paid_reserved_incurred(_value: str, ctx: Data, _today: date) -> str | None:
    def num(*keys: str) -> Decimal | None:
        for k in keys:
            n = nz.money(_v(ctx.get(k)) or "")
            if n is not None:
                return n
        return None

    paid = num("paid", "total_paid", "losses_paid")
    reserved = num("reserves", "total_reserves", "reserved")
    incurred = num("incurred", "total_incurred")
    if paid is None or incurred is None:
        return None
    if abs(paid + (reserved or Decimal(0)) - incurred) > Decimal("0.05"):
        return f"Paid {paid:,} plus reserved {reserved or 0:,} is not incurred {incurred:,}"
    return None


def _vin_year(value: str, ctx: Data, today: date) -> str | None:
    vin = _v(ctx.get("vin"))
    year = nz.number(value)
    if not vin or year is None:
        return None
    c = vins.check(vin, current_year=today.year)
    if c.model_year and c.valid and int(year) != c.model_year:
        return f"Year {int(year)} does not match the VIN's model-year character ({c.model_year})"
    return None


VALIDATORS: dict[str, Callable[[str, Data, date], str | None]] = {
    "vin": _vin,
    "dot": _dot,
    "mc": _mc,
    "phone": _phone,
    "state": _state,
    "license_format": _license,
    "issue_before_expiry": _issue_before_expiry,
    "age_at_order": _age_at_order,
    "report_after_loss": _report_after_loss,
    "paid_reserved_incurred": _paid_reserved_incurred,
    "vin_year": _vin_year,
}


def run_validators(doc_type: str, data: Data, today: date) -> list[Check]:
    """Every catalog validator on every value it names, with the path of the value."""
    out: list[Check] = []
    for e in entries(doc_type):
        if not e.spec.validators:
            continue
        for path, value, ctx in _values(data, e.path):
            for name in e.spec.validators:
                fn = VALIDATORS.get(name)
                if fn is None or value is None:
                    continue
                problem = fn(value, ctx, today)
                out.append(
                    Check(
                        f"validator:{name}",
                        problem is None,
                        problem or f"{path}: {name.replace('_', ' ')} holds",
                        [path],
                    )
                )
    return out


def _values(data: Data, pattern: str) -> list[tuple[str, str | None, Data]]:
    """(path, value, the record or document it sits in) for a catalog path pattern."""
    parts = pattern.split(".")

    def walk(node: Any, i: int, path: str, ctx: Data) -> list[tuple[str, str | None, Data]]:
        if i == len(parts):
            return [(path, _v(node), ctx)]
        part = parts[i]
        key = part.removesuffix("[]")
        child: Any = cast(Data, node).get(key) if isinstance(node, dict) else None
        if part.endswith("[]"):
            out: list[tuple[str, str | None, Data]] = []
            for n, rec in enumerate(cast(list[Any], child) if isinstance(child, list) else []):
                if isinstance(rec, dict):
                    out += walk(rec, i + 1, f"{path}{key}[{n}].", cast(Data, rec))
            return out
        if i == len(parts) - 1:
            return [(f"{path}{key}", _v(child), ctx)]
        return walk(
            child, i + 1, f"{path}{key}.", cast(Data, child) if isinstance(child, dict) else ctx
        )

    return walk(data, 0, "", data)


# --- recall --------------------------------------------------------------------------------


# An event line starts with its code and then its date ("VIOL 06/07/2023 ..."); headers and
# annotation lines ("VIOL MISC. DATED: ...") do not
_EVENT_LINE = re.compile(
    r"^\s*(VIOL|CONV|ACCD?|ACCIDENT|FTA|SUSP|SUSPENSION|REVOK|DUI|DWI)\s+\d{1,2}/\d{1,2}/\d{2,4}\b",
    re.IGNORECASE,
)


def _jurisdiction_line(line: str) -> bool:
    """A jurisdiction row: starts with a state or province (name or code), then figures."""
    from lane.domain import geo

    words = line.split()
    if not words or not re.search(r"\d[\d,]{2,}", line):
        return False
    first = re.sub(r"[^A-Za-z ]", "", words[0])
    two = re.sub(r"[^A-Za-z ]", "", " ".join(words[:2]))
    folded = first.replace("l", "I") if first[:1] == "l" else first  # OCR reads "Iowa" as "lowa"
    return bool(geo.to_code(two) or geo.to_code(first) or geo.to_code(folded))


def recall_checks(
    doc_type: str, data: Data, page_text: str, *, sheet_rows: dict[str, int] | None = None
) -> list[Check]:
    """Rows the document shows against the records read. A mismatch escalates."""
    out: list[Check] = []
    if doc_type == "loss_run":
        printed = nz.number(_v(data.get("total_claims")) or "")
        none_stated = bool(_v(data.get("no_claims_statement")))
        read = len(_rows(data, "claims"))
        if printed is not None:
            ok = int(printed) == read
            out.append(
                Check(
                    "recall:claims",
                    ok,
                    f"The header says {int(printed)} claims and {read} were read"
                    if not ok
                    else f"All {read} claims in the header were read",
                    ["claims"],
                )
            )
        elif none_stated and read:
            out.append(
                Check(
                    "recall:claims",
                    False,
                    f"The run states no claims but {read} were read",
                    ["claims"],
                )
            )
    elif doc_type == "ifta_return":
        rows = [r for r in _rows(data, "rows") if _v(r.get("jurisdiction"))]
        lines = sum(1 for ln in page_text.splitlines() if _jurisdiction_line(ln))
        if lines:
            ok = len(rows) >= lines
            out.append(
                Check(
                    "recall:ifta_rows",
                    ok,
                    f"{lines} jurisdiction lines on the pages and {len(rows)} records read"
                    if not ok
                    else f"{len(rows)} jurisdiction records for {lines} printed lines",
                    ["rows"],
                )
            )
    elif doc_type == "mvr":
        events = len(_rows(data, "events"))
        lines = sum(1 for ln in page_text.splitlines() if _EVENT_LINE.match(ln))
        if lines:
            ok = events >= lines
            out.append(
                Check(
                    "recall:mvr_events",
                    ok,
                    f"{lines} event lines on the record and {events} events read"
                    if not ok
                    else f"{events} events for {lines} printed event lines",
                    ["events"],
                )
            )
    for key, filled in (sheet_rows or {}).items():
        read = len(_rows(data, key))
        ok = read >= filled
        out.append(
            Check(
                "recall:sheet_rows",
                ok,
                f"{filled} filled rows under the {key.replace('_', ' ')} header and {read} records read"
                if not ok
                else f"All {read} filled rows of the {key.replace('_', ' ')} were read",
                [key],
            )
        )
    return out
