"""Deterministic normalization of values as printed.

The model copies values verbatim; code turns them into numbers, money, percentages and dates.
Every parser returns None rather than guessing when the text is not what it expects.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

_NUM = re.compile(r"^\(?-?\$?\s*-?[\d,]*\.?\d+\)?$")


def number(text: str | None) -> Decimal | None:
    """'1,024' -> 1024, '$2,936.92' -> 2936.92, '-$326.30' -> -326.30, '(12.50)' -> -12.50."""
    if text is None:
        return None
    t = text.strip().replace(" ", "")
    if not t or not _NUM.match(t):
        return None
    negative = t.startswith(("-", "(")) or t.startswith("$-")
    digits = re.sub(r"[^\d.]", "", t)
    try:
        value = Decimal(digits)
    except InvalidOperation:
        return None
    return -value if negative else value


def money(text: str | None) -> Decimal | None:
    return number(text)


def percent(text: str | None) -> Decimal | None:
    """'15%', '15', '0.15' all mean fifteen percent; returns 15."""
    if text is None:
        return None
    t = text.strip().rstrip("%").strip()
    value = number(t)
    if value is None:
        return None
    return value * 100 if 0 < value <= 1 and "." in t else value


_DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%m-%d-%Y",
    "%m-%d-%y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%Y-%m-%dT%H:%M:%S",
)


def to_date(text: str | None) -> date | None:
    """Common US forms. Two-digit years pivot at 70 (79 -> 1979, 24 -> 2024)."""
    if text is None:
        return None
    t = text.strip().rstrip(".").strip()
    t = re.sub(r"\s+", " ", t)
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(t, fmt)
        except ValueError:
            continue
        if fmt.endswith("%y") and parsed.year >= 2070:
            parsed = parsed.replace(year=parsed.year - 100)
        return parsed.date()
    return None


def yes_no(text: str | None) -> bool | None:
    if text is None:
        return None
    t = text.strip().lower()
    if t in {"yes", "y", "true", "x", "checked", "■"}:
        return True
    if t in {"no", "n", "false", "unchecked", "□"}:
        return False
    return None


# A form that says it has nothing to give: not a value, and not something to read as one.
_ABSENT = frozenset(
    {
        "not provided",
        "not stated",
        "not specified",
        "not given",
        "not available",
        "none provided",
        "to be determined",
        "to be advised",
        "to follow",
        "will follow",
        "tbd",
        "tba",
        "pending",
        "unknown",
        "null",
    }
)
_NOT_APPLICABLE = frozenset({"n/a", "na", "not applicable", "none", "nil", "-", "--", "\u2014"})


def is_absent(raw: str | None, kind: str) -> bool:
    """Stated absence ("Not provided", "TBD"). Free text is kept as printed; a yes/no question
    left as "N/A" is unanswered, not absent, so it stays something to flag."""
    if raw is None or kind in ("text", "company", "person"):
        return False
    t = re.sub(r"\s+", " ", raw.strip().rstrip(".")).lower()
    if not t:
        return True
    return t in _ABSENT or (kind != "bool" and t in _NOT_APPLICABLE)


def clean_text(text: str | None) -> str | None:
    if text is None:
        return None
    t = re.sub(r"\s+", " ", text).strip()
    return t or None


_KEEP_UPPER = {
    "LLC", "LLP", "LP", "DBA", "USA", "US", "USDOT", "MC", "II", "III", "IV", "PO", "VIN",
    "CDL", "MPH", "DUI", "DWI", "OWI", "MVR", "DMV", "ID", "AC", "FMCSA", "DOT", "IFTA",
}  # fmt: skip
_LOWER = {"of", "and", "the", "for", "in", "on", "at", "to", "by"}
_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
    "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM",
    "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY",
}  # fmt: skip


STATE_CODES = frozenset(_STATE_CODES)


# A card's legal-presence designation. Never an underwriting signal (docs/SECURITY.md), so it is
# neither extracted nor shown.
_PRESENCE = re.compile(
    r"[,;/\s]*\b(limited[\s-]*term|non[\s-]*domiciled|temporary\s+visitor)\b[,;/\s]*", re.IGNORECASE
)


def drop_presence_markers(text: str | None) -> str | None:
    """'COMMERCIAL DRIVER LICENSE, LIMITED-TERM' -> 'COMMERCIAL DRIVER LICENSE'."""
    if text is None:
        return None
    out = _PRESENCE.sub(" ", text).strip(" ,;/")
    return re.sub(r"\s{2,}", " ", out) or None


def display_case(text: str | None) -> str | None:
    """A name printed in capitals, for display: 'SUMMIT SPECIALTY INC' becomes
    'Summit Specialty Inc', 'DENVER, CO' becomes 'Denver, CO'. Text with any lower case
    is left as printed; so are codes (words with digits), acronyms and state codes after a comma.
    Only for display: comparison keys and quotes keep the text as printed."""
    if text is None or not re.search(r"[A-Z]{3}", text) or re.search(r"[a-z]", text):
        return text
    out: list[str] = []
    after_comma = False
    for i, token in enumerate(re.split(r"(\s+)", text)):
        if not token.strip():
            out.append(token)
            continue
        core = token.strip(",.;:()")
        if (
            any(ch.isdigit() for ch in core)
            or core in _KEEP_UPPER
            or (after_comma and core in _STATE_CODES)
        ):
            out.append(token)
        elif i and core.lower() in _LOWER:
            out.append(token.lower())
        else:
            out.append(
                re.sub(
                    r"[A-Z]+",
                    lambda m: m.group(0)[0] + m.group(0)[1:].lower(),
                    token,
                )
            )
        after_comma = token.endswith(",")
    return "".join(out)


_LEADING_DATE = re.compile(r"^\s*\d{1,2}/\d{1,2}/\d{2,4}\s+")


def display_text(text: str | None, *, drop_leading_date: bool = False) -> str | None:
    """Printed text tidied for reading: one space between words, no decoration ('** X **'),
    capitals in title case, '$30,000' not '$ 30,000'. Evidence keeps the text as printed."""
    if text is None:
        return None
    t = " ".join(text.split())
    if drop_leading_date:
        t = _LEADING_DATE.sub("", t)
    t = re.sub(r"\*+\s*(.*?)\s*\*+", r"\1", t).strip(" *")
    t = re.sub(r"\$\s+(?=\d)", "$", t)
    return display_case(t) or t


def long_date(value: Any) -> str:
    """'Feb 10, 2023', the one date format people read everywhere."""
    d = as_date(value)
    return d.strftime("%b %-d, %Y") if d else str(value)


def quarter(value: str) -> str:
    """'2025-Q3' -> 'Q3 2025'."""
    m = re.fullmatch(r"(\d{4})-Q([1-4])", value.strip())
    return f"Q{m.group(2)} {m.group(1)}" if m else value


def as_date(value: Any) -> date | None:
    """A stored value (ISO string, date, or a date as printed) as a date."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return to_date(str(value))


def as_decimal(value: Any) -> Decimal | None:
    """A stored value (number, or a number as printed) as a Decimal. Booleans are not numbers."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float | Decimal):
        return Decimal(str(value))
    return number(str(value))
