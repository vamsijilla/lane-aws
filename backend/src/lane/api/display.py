"""Printed text tidied for people to read, in the API only. Facts, quotes and the evidence lens
keep the text exactly as printed; resolution and rules never see these forms."""

import re

from lane.domain import normalize as nz

# Loss run shorthand for the two vehicles in a claim
_VEHICLE_ABBR = {
    "iv": "insured vehicle",
    "cv": "claimant vehicle",
    "ov": "other vehicle",
    "bi": "(bodily injury)",
    "pd": "(property damage)",
}
_ABBR = re.compile(r"\b(iv|cv|ov|bi|pd)\b", re.IGNORECASE)


def tidy(text: str) -> str:
    """One space between words, no ' ,', no decoration (** X **), no dangling dashes."""
    t = " ".join(text.split())
    t = re.sub(r"\*+", "", t)
    t = re.sub(r"\s+,", ",", t)
    t = re.sub(r"(\s*-\s*)+$", "", t)  # '2021 - Peterbilt -'
    t = re.sub(r"^(\s*-\s*)+", "", t)
    t = re.sub(r"\s+-\s+-\s+", " - ", t)
    return t.strip(" ,;")


def _sentence(text: str) -> str:
    """'IV Rear-Ended Cv At Signal' -> 'Insured vehicle rear-ended claimant vehicle at signal'.
    Codes (words with digits) and two-letter state codes keep their case."""
    words = text.split(" ")
    out: list[str] = []
    for w in words:
        keep = any(ch.isdigit() for ch in w) or (
            len(w) == 2 and w.isupper() and w in nz.STATE_CODES
        )
        out.append(w if keep else w.lower())
    s = " ".join(out)
    return s[:1].upper() + s[1:]


_COUNTY = re.compile(r"\b(Co|Cnty|Cty)\.?$", re.IGNORECASE)  # 'Pinal Co' -> 'Pinal County'


def claim(attribute: str, text: str | None) -> str | None:
    """A loss run's claim fields: description in plain words, place names in title case."""
    if not text:
        return text
    t = tidy(text)
    if attribute == "description":
        t = _ABBR.sub(lambda m: _VEHICLE_ABBR[m.group(1).lower()], t)
        return _sentence(t)
    if attribute == "vehicle":
        return " ".join(p.strip() for p in re.split(r"\s+-\s+|\s+-$", t) if p.strip())
    if attribute == "location":
        parts = [p.strip() for p in t.split(",")]
        return ", ".join(
            p if p.upper() in nz.STATE_CODES else _COUNTY.sub("County", nz.display_case(p) or p)
            for p in parts
        )
    return nz.display_case(t) or t


# Trailing status codes and filler an MVR abstract prints after a note ('... NOTICE AC')
_NOTE_CODE = re.compile(r"\s+(AC|IN|ACT)(?=\s*(-\s*\d+)?$)")
_LEADING_DATE = re.compile(r"^\s*\d{1,2}/\d{1,2}/\d{2,4}\s+")
_DATED = re.compile(r"\bdated:?\s*\d{1,2}/\d{1,2}/\d{2,4}\s*-?\s*", re.IGNORECASE)
_MISC = re.compile(r"^(VIOL|ACC|ACCD|CONV)\s+MISC\.?\s*", re.IGNORECASE)
_NOTE_LABEL = {"VIOL": "Violation", "ACC": "Accident", "ACCD": "Accident", "CONV": "Conviction"}


def mvr_note(text: str | None) -> str | None:
    """'VIOL MISC. DATED: 06/07/2023 - THE EVENT IS ACTIVE' -> 'Violation: the event is active';
    '08/19/2026 CDL CLEARANCE NOTICE AC' -> 'CDL clearance notice'."""
    if not text:
        return text
    t = _LEADING_DATE.sub("", tidy(text))
    m = _MISC.match(t)
    label = _NOTE_LABEL.get(m.group(1).upper()) if m else None
    t = _MISC.sub("", t)
    t = _DATED.sub("", t)
    t = _NOTE_CODE.sub("", tidy(t))
    t = _sentence(t)
    t = re.sub(r"\bcdl\b", "CDL", t, flags=re.IGNORECASE)
    return f"{label}: {t[:1].lower() + t[1:]}" if label else t


def mvr_event(description: str | None) -> str | None:
    """'** ACCIDENT **' -> 'Accident'; 'SPEEDING 15-29 MPH OVER LIMIT' -> 'Speeding 15-29 MPH
    over limit'."""
    if not description:
        return description
    t = _NOTE_CODE.sub("", tidy(description))
    t = _sentence(t)
    for keep in ("CDL", "MPH", "DUI", "DWI"):
        t = re.sub(rf"\b{keep}\b", keep, t, flags=re.IGNORECASE)
    return t


def place(text: str | None) -> str | None:
    """'PENNSYLVANIA' -> 'Pennsylvania'; state codes stay as printed."""
    if not text:
        return text
    t = tidy(text)
    return t if t.upper() in nz.STATE_CODES else (nz.display_case(t) or t)


def field[T](kind: str, attribute: str, text: T) -> T | str:
    """A resolved value as shown on the screens (the lens keeps what was printed)."""
    if not isinstance(text, str):
        return text
    if kind == "claim" and attribute in ("description", "location", "vehicle"):
        return claim(attribute, text) or text
    if attribute == "carrier":
        from lane.api.summaries import carrier_key  # one name per carrier

        return carrier_key(text)
    return text


def candidates(
    kind: str, attribute: str, cands: list[dict[str, object]]
) -> list[dict[str, object]]:
    return [{**c, "display": field(kind, attribute, c.get("display"))} for c in cands]


_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_QUARTER = re.compile(r"\b(\d{4})-Q([1-4])\b")
_SHORT_RANGE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\s*-\s*(\d{1,2}/\d{1,2}/\d{2,4})\b")


def dates_in(text: str | None) -> str | None:
    """Labels and titles with dates as people read them: 'Feb 10, 2023', 'Q3 2025',
    '12/15/23-12/15/24' -> 'Dec 15, 2023 to Dec 15, 2024'."""
    if not text:
        return text
    t = _SHORT_RANGE.sub(
        lambda m: (
            f"{nz.long_date(nz.to_date(m.group(1))) or m.group(1)} to "
            f"{nz.long_date(nz.to_date(m.group(2))) or m.group(2)}"
        ),
        text,
    )
    t = _QUARTER.sub(lambda m: f"Q{m.group(2)} {m.group(1)}", t)
    return _ISO.sub(lambda m: nz.long_date(m.group(0)) or m.group(0), t)


_KEEP = frozenset({"LLC", "INC", "USA", "CDL", "MPH", "DOT", "IFTA", "VIN", "PD", "AL", "MTC"})


def source_label(text: str | None) -> str | None:
    """A label printed partly in capitals, for display: 'Paper/Plastic/ HOUSEHOLD GOODS /
    APPLIANCES' -> 'Paper / Plastic / Household Goods / Appliances'. Acronyms stay."""
    if not text:
        return text
    t = re.sub(r"\s*/\s*", " / ", tidy(text))
    return re.sub(
        r"\b[A-Z]{3,}\b",
        lambda m: m.group(0) if m.group(0) in _KEEP else m.group(0).capitalize(),
        t,
    )


def model_label(model_id: str) -> str:
    """'claude-opus-5-5' -> 'Claude Opus 5.5', as people name the model."""
    m = re.fullmatch(r"claude-([a-z]+)-(\d+)-(\d+)", model_id)
    return f"Claude {m.group(1).title()} {m.group(2)}.{m.group(3)}" if m else model_id
