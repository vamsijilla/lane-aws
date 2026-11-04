"""VIN validation and decoding (ISO 3779 / 49 CFR 565), offline.

Length, character set, check digit (position 9), model year (position 10) and the maker from the
World Manufacturer Identifier (positions 1-3) for common heavy-truck and trailer makers.
Production would add NHTSA vPIC lookups behind the same function.
"""

import re
from dataclasses import dataclass, field

_TRANSLIT = {
    **{str(d): d for d in range(10)},
    **dict(zip("ABCDEFGH", range(1, 9), strict=True)),
    **dict(zip("JKLMN", range(1, 6), strict=True)),
    "P": 7,
    "R": 9,
    **dict(zip("STUVWXYZ", range(2, 10), strict=True)),
}
_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"  # 30-year cycle starting 1980 / 2010

# World Manufacturer Identifiers for makers common in trucking fleets.
WMI_MAKERS: dict[str, str] = {
    "4V1": "VOLVO",
    "4V2": "VOLVO",
    "4V4": "VOLVO",
    "4V5": "VOLVO",
    "4VG": "VOLVO",
    "1XP": "PETERBILT",
    "2XP": "PETERBILT",
    "1NP": "PETERBILT",
    "1XK": "KENWORTH",
    "2XK": "KENWORTH",
    "1NK": "KENWORTH",
    "1FU": "FREIGHTLINER",
    "1FV": "FREIGHTLINER",
    "3AK": "FREIGHTLINER",
    "3AL": "FREIGHTLINER",
    "1HS": "INTERNATIONAL",
    "2HS": "INTERNATIONAL",
    "3HS": "INTERNATIONAL",
    "1HT": "INTERNATIONAL",
    "3HA": "INTERNATIONAL",
    "1M1": "MACK",
    "1M2": "MACK",
    "5KJ": "WESTERN STAR",
    "5KK": "WESTERN STAR",
    "1GR": "GREAT DANE",
    "1JJ": "WABASH",
    "3H3": "HYUNDAI TRANSLEAD",
    "1UY": "UTILITY",
    "1DW": "STOUGHTON",
    "5V8": "VANGUARD",
    "1E1": "WILSON",
    "1TT": "TRAIL KING",
}


@dataclass(frozen=True)
class VinCheck:
    vin: str
    valid: bool
    maker: str | None = None
    model_year: int | None = None
    problems: list[str] = field(default_factory=lambda: list[str]())


def clean(vin: str) -> str:
    return re.sub(r"[\s-]", "", vin).upper()


def check(vin: str, *, current_year: int) -> VinCheck:
    v = clean(vin)
    problems: list[str] = []
    if len(v) != 17:
        problems.append(f"{len(v)} characters instead of 17")
    bad = sorted({c for c in v if c not in _TRANSLIT})
    if bad:
        problems.append(f"contains characters a VIN cannot have ({', '.join(bad)})")
    maker = WMI_MAKERS.get(v[:3]) if len(v) >= 3 else None
    year = model_year(v, current_year=current_year) if len(v) == 17 and not bad else None
    if len(v) == 17 and not bad:
        total = sum(_TRANSLIT[c] * w for c, w in zip(v, _WEIGHTS, strict=True))
        expected = "X" if total % 11 == 10 else str(total % 11)
        if v[8] != expected:
            problems.append(f"check digit is {v[8]}, expected {expected}")
    return VinCheck(v, not problems, maker, year, problems)


def model_year(vin: str, *, current_year: int) -> int | None:
    code = vin[9] if len(vin) > 9 else ""
    if code not in _YEAR_CODES:
        return None
    base = 1980 + _YEAR_CODES.index(code)
    # Pick the cycle that is not in the future (allowing next model year).
    while base + 30 <= current_year + 1:
        base += 30
    return base


def edit_distance_one(a: str, b: str) -> bool:
    """True when two VINs differ by one substitution, insertion or deletion (a likely typo)."""
    a, b = clean(a), clean(b)
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) == 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1 :] == short for i in range(len(long_)))
