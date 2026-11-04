"""US state names, codes and approximate geographic centers, for distance comparisons.

Used to compare a stated "farthest territory" against where fuel tax returns show miles.
Distances are great-circle miles between state centers: coarse, but enough to tell a West Coast
regional operation from one that runs to the East Coast.
"""

import math
import re

# code: (name, latitude, longitude) of an approximate geographic center
STATES: dict[str, tuple[str, float, float]] = {
    "AL": ("Alabama", 32.8, -86.8),
    "AK": ("Alaska", 64.7, -152.0),
    "AZ": ("Arizona", 34.3, -111.7),
    "AR": ("Arkansas", 34.9, -92.4),
    "CA": ("California", 37.2, -119.4),
    "CO": ("Colorado", 39.0, -105.5),
    "CT": ("Connecticut", 41.6, -72.7),
    "DE": ("Delaware", 39.0, -75.5),
    "DC": ("District of Columbia", 38.9, -77.0),
    "FL": ("Florida", 28.6, -82.4),
    "GA": ("Georgia", 32.7, -83.4),
    "HI": ("Hawaii", 20.3, -156.4),
    "ID": ("Idaho", 44.4, -114.6),
    "IL": ("Illinois", 40.0, -89.2),
    "IN": ("Indiana", 39.9, -86.3),
    "IA": ("Iowa", 42.1, -93.5),
    "KS": ("Kansas", 38.5, -98.4),
    "KY": ("Kentucky", 37.5, -85.3),
    "LA": ("Louisiana", 31.1, -92.0),
    "ME": ("Maine", 45.4, -69.2),
    "MD": ("Maryland", 39.0, -76.8),
    "MA": ("Massachusetts", 42.3, -71.8),
    "MI": ("Michigan", 44.3, -85.4),
    "MN": ("Minnesota", 46.3, -94.3),
    "MS": ("Mississippi", 32.7, -89.7),
    "MO": ("Missouri", 38.4, -92.5),
    "MT": ("Montana", 47.0, -109.6),
    "NE": ("Nebraska", 41.5, -99.8),
    "NV": ("Nevada", 39.3, -116.6),
    "NH": ("New Hampshire", 43.7, -71.6),
    "NJ": ("New Jersey", 40.2, -74.7),
    "NM": ("New Mexico", 34.4, -106.1),
    "NY": ("New York", 42.9, -75.5),
    "NC": ("North Carolina", 35.6, -79.4),
    "ND": ("North Dakota", 47.5, -100.5),
    "OH": ("Ohio", 40.3, -82.8),
    "OK": ("Oklahoma", 35.6, -97.5),
    "OR": ("Oregon", 43.9, -120.6),
    "PA": ("Pennsylvania", 40.9, -77.8),
    "RI": ("Rhode Island", 41.7, -71.5),
    "SC": ("South Carolina", 33.9, -80.9),
    "SD": ("South Dakota", 44.4, -100.2),
    "TN": ("Tennessee", 35.9, -86.4),
    "TX": ("Texas", 31.5, -99.3),
    "UT": ("Utah", 39.3, -111.7),
    "VT": ("Vermont", 44.1, -72.7),
    "VA": ("Virginia", 37.5, -78.9),
    "WA": ("Washington", 47.4, -120.5),
    "WV": ("West Virginia", 38.6, -80.6),
    "WI": ("Wisconsin", 44.6, -89.9),
    "WY": ("Wyoming", 43.0, -107.6),
}
_BY_NAME = {name.upper(): code for code, (name, _lat, _lon) in STATES.items()}


def to_code(text: str | None) -> str | None:
    """'Texas', 'TX', 'tx.' -> 'TX'."""
    if not text:
        return None
    t = re.sub(r"[^A-Za-z ]", "", text).strip().upper()
    if t in STATES:
        return t
    return _BY_NAME.get(t)


def miles_between(a: str, b: str) -> float:
    _, lat1, lon1 = STATES[a]
    _, lat2, lon2 = STATES[b]
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def name(code: str) -> str:
    return STATES[code][0]
