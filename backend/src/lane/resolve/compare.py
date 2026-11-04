"""How values of each kind are compared. Two candidates agree when their keys are equal.

Each comparator returns (key, norm, display): `key` decides agreement, `norm` is what rules
read, `display` is what people see. Formatting differences never create conflicts.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, cast

from lane.domain import geo
from lane.domain import normalize as nz
from lane.domain.taxonomy import entity_form_from_name, entity_form_from_type


@dataclass(frozen=True)
class Compared:
    key: Any
    norm: Any
    display: str


def _text(raw: str) -> Compared | None:
    t = nz.clean_text(raw)
    if not t:
        return None
    return Compared(re.sub(r"[^A-Z0-9]", "", t.upper()), t, nz.display_text(t) or t)


# Words that do not distinguish one company from another ("Summit Specialty" and
# "Summit Specialty Insurance Company" are the same carrier).
_BOILERPLATE = {
    "INSURANCE",
    "COMPANY",
    "CO",
    "GROUP",
    "THE",
    "OF",
    "INC",
    "LLC",
    "CORP",
    "CORPORATION",
}


def _company(raw: str) -> Compared | None:
    t = nz.clean_text(raw)
    if not t:
        return None
    core, _form = entity_form_from_name(t)
    words = [w for w in re.findall(r"[A-Z0-9]+", core) if w not in _BOILERPLATE]
    # Shown without trailing punctuation ("Services Inc." -> "Services Inc"); compared as before
    shown = (nz.display_case(t) or t).rstrip(" .,;:")
    return Compared("".join(words) or re.sub(r"[^A-Z0-9]", "", core), t, shown or t)


def _entity_form(raw: str) -> Compared | None:
    t = nz.clean_text(raw)
    if not t:
        return None
    form = entity_form_from_type(t) or entity_form_from_name(t)[1]
    if form is None:
        return None
    return Compared(form, form, form.title() if form != "LLC" else "LLC")


def person_key(raw: str) -> frozenset[str] | None:
    """The name's words, ignoring order, punctuation and initials. 'GARCIA, ANA' and
    'ANA GARCIA' have the same key; see `same_person` for middle names."""
    t = nz.clean_text(raw)
    if not t:
        return None
    # Apostrophes join a name ("O'NEIL" is ONEIL); other punctuation separates words
    joined = re.sub(r"['\u2019`]", "", t.upper())
    words = frozenset(w for w in re.findall(r"[A-Z]+", joined) if len(w) > 1)
    return words if len(words) >= 2 else None


def same_person(a: object, b: object) -> bool:
    """One name's words contain the other's: 'ANA M GARCIA' and 'GARCIA ANA MARIA'."""
    if not isinstance(a, frozenset) or not isinstance(b, frozenset) or not a or not b:
        return False
    sa, sb = cast(frozenset[str], a), cast(frozenset[str], b)
    return sa <= sb or sb <= sa


def _person(raw: str) -> Compared | None:
    key = person_key(raw)
    t = nz.clean_text(raw)
    if key is None or t is None:
        return None
    return Compared(key, t, nz.display_case(t) or t)


def _date(raw: str) -> Compared | None:
    d = nz.to_date(raw)
    if d is None:
        return None
    return Compared(d, d.isoformat(), d.strftime("%b %-d, %Y"))


def _money(raw: str) -> Compared | None:
    m = nz.money(raw)
    if m is None:
        return None
    return Compared(m, str(m), f"${m:,.2f}" if m % 1 else f"${m:,.0f}")


_COUNTED = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s+([A-Za-z]{3,})")
_JOINED = re.compile(r"\band\b|&|\+|,", re.IGNORECASE)
_EITHER = re.compile(r"\b\d+\s*(?:or|/)\s*\d+\b", re.IGNORECASE)


def compound_count(raw: str) -> bool:
    """A count that states more than one: "3 power units and 2 trailers", "2 or 3 units".
    A total with detail ("5 (3 owned, 2 leased)") and a range are not compound."""
    t = re.sub(r"\([^)]*\)", " ", raw or "")
    if _EITHER.search(t):
        return True
    counted = {n.replace(",", "") for n, _ in _COUNTED.findall(t)}
    return len(counted) >= 2 and bool(_JOINED.search(t))


def _number(raw: str) -> Compared | None:
    if compound_count(raw):
        return None
    found = re.search(r"-?[\d,]*\.?\d+", raw or "")
    if not found:
        return None
    n = nz.number(found.group(0))
    if n is None:
        return None
    return Compared(n, str(n), f"{n:,}")


def _year(raw: str) -> Compared | None:
    found = re.search(r"\b(19|20)\d{2}\b", raw or "")
    if not found:
        return None
    y = int(found.group(0))
    return Compared(y, y, str(y))


def _id(raw: str) -> Compared | None:
    t = re.sub(r"[^A-Z0-9]", "", (raw or "").upper())
    return Compared(t, t, raw.strip()) if t else None


def _phone(raw: str) -> Compared | None:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) < 7:
        return None
    return Compared(digits[-10:], digits[-10:], raw.strip())


def _bool(raw: str) -> Compared | None:
    b = nz.yes_no(raw)
    if b is None:
        return None
    return Compared(b, b, "Yes" if b else "No")


def _state(raw: str) -> Compared | None:
    code = geo.to_code(raw)
    return Compared(code, code, code) if code else None


def _percent(raw: str) -> Compared | None:
    p = nz.percent(raw)
    if p is None:
        return None
    return Compared(p, str(p), f"{p.normalize():f}%")


COMPARATORS = {
    "text": _text,
    "company": _company,
    "entity_form": _entity_form,
    "person": _person,
    "date": _date,
    "money": _money,
    "number": _number,
    "year": _year,
    "id": _id,
    "phone": _phone,
    "bool": _bool,
    "state": _state,
    "percent": _percent,
}


def compare(kind: str, raw: Any) -> Compared | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return Compared(raw, raw, "Yes" if raw else "No")
    if isinstance(raw, (date, Decimal, int, float)) and kind in {"money", "number", "percent"}:
        raw = str(raw)
    return COMPARATORS[kind](str(raw))


# What a person should type for each kind, for correction forms and error messages
EXPECTED = {
    "text": "some text",
    "company": "a company name",
    "entity_form": "a legal form such as LLC or Corporation",
    "person": "a person's name",
    "date": "a date such as 2026-12-15 or 12/15/2026",
    "money": "an amount such as 100000 or $100,000",
    "number": "a number",
    "year": "a four-digit year",
    "id": "letters and digits",
    "phone": "a phone number",
    "bool": "Yes or No",
    "state": "a two-letter state such as CA",
    "percent": "a percentage such as 45%",
}


# Kinds a value has to be readable as. Free text, companies and people always read.
TYPED_KINDS = frozenset({"date", "money", "number", "year", "phone", "state", "percent", "bool"})


def explain_unparsed(kind: str, raw: str) -> str:
    """Why a printed value could not be read, in words for the field's note."""
    if kind == "number" and compound_count(raw):
        return f"'{raw.strip()}' states more than one count; which total applies?"
    return f"'{raw.strip()}' could not be read as {EXPECTED.get(kind, 'a value')}"
