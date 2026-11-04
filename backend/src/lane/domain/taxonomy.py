"""Industry vocabularies that let different forms be compared.

Forms ask the same things in different words and buckets. These tables map them onto shared
keys: cargo classes, operational questions, entity forms. They hold trucking-industry language,
not anything from one submission.
"""

import re

# Cargo classes. A commodity line maps to the first class whose pattern it matches.
COMMODITY_CLASSES: list[tuple[str, str, str]] = [
    ("food", "Food and groceries", r"produce|grocer|canned|frozen|food|meat|dairy|reefer"),
    ("beverages", "Beverages", r"beverage|beer|wine|liquor|water|soda|drink"),
    (
        "paper_household",
        "Paper, packaging and household goods",
        r"paper|plastic|packag|household|appliance|furniture|amazon|walmart|general freight",
    ),
    ("building", "Building materials and metals", r"building|lumber|steel|iron|pipe|hardware"),
    ("machinery", "Machinery and auto parts", r"machinery|auto parts|motor|tools|equipment"),
    ("agriculture", "Agricultural products", r"grain|hay|straw|agricult|livestock"),
    ("intermodal", "Intermodal containers", r"intermodal|container"),
    ("hazmat", "Hazardous materials", r"hazard|hazmat|chemical|explosive|flammable"),
]


def commodity_class(label: str) -> str:
    for key, _title, pattern in COMMODITY_CLASSES:
        if re.search(pattern, label, re.IGNORECASE):
            return key
    return "other"


def commodity_title(key: str) -> str:
    return next((title for k, title, _p in COMMODITY_CLASSES if k == key), "Other")


# Operational questions: shared key, label, pattern over the question text.
QUESTIONS: list[tuple[str, str, str]] = [
    ("hazmat", "Hauls hazardous materials", r"hazard"),
    ("intermodal", "Hauls intermodal containers", r"intermodal"),
    ("cross_border", "Operates across the border", r"cross[- ]border|mexico|canada"),
    ("doubles_triples", "Pulls doubles or triples", r"double|triple"),
    ("oversize", "Hauls oversize or overweight loads", r"oversize|overweight"),
    ("flatbed", "Pulls flatbeds", r"flatbed"),
    ("team_driving", "Uses team or slip-seat driving", r"\bteam\b|slip[- ]seat|hot[- ]seat"),
    (
        "unlisted_vehicles",
        "Has vehicles not listed on the application",
        r"not listed|vehicles owned or operated",
    ),
    ("filings_required", "Needs regulatory filings", r"\bfilings?\b"),
    ("leases_to_others", "Leases or rents vehicles to others", r"\blease\b|\brent\b|\bloan\b"),
    ("subhaulers", "Hires sub-haulers", r"sub-?hauler"),
    ("freight_broker", "Acts as a broker or forwarder", r"broker|forwarder|arrange loads"),
    ("passengers", "Carries passengers", r"passenger"),
    (
        "other_trucking_business",
        "Has other trucking businesses",
        r"other business|other trucking company|interest in any other",
    ),
    ("ownership_change", "Changed ownership, management or name", r"change.*(owner|name)"),
    ("workers_comp", "Carries workers compensation", r"workers comp"),
    ("dot_compliance", "Complies with DOT driver rules", r"DOT regulations|driver employment"),
    ("english_proficiency", "Drivers meet English proficiency rules", r"english language"),
    ("escort", "Requires an escort", r"\bescort\b"),
]


def question_key(text: str) -> str | None:
    for key, _label, pattern in QUESTIONS:
        if re.search(pattern, text, re.IGNORECASE):
            return key
    return None


QUESTION_LABELS = {key: label for key, label, _p in QUESTIONS}

# Legal entity forms as written in names, mapped to the form a checkbox would say.
ENTITY_SUFFIXES: list[tuple[str, str]] = [
    (r"\bL\.?L\.?C\.?$", "LLC"),
    (r"\b(INC|INCORPORATED)\.?$", "CORPORATION"),
    (r"\b(CORP|CORPORATION|CO)\.?$", "CORPORATION"),
    (r"\bL\.?P\.?$", "PARTNERSHIP"),
]
ENTITY_TYPES: dict[str, str] = {
    "corporation": "CORPORATION",
    "llc": "LLC",
    "sole proprietorship": "SOLE PROPRIETORSHIP",
    "partnership": "PARTNERSHIP",
}


def entity_form_from_name(name: str) -> tuple[str, str | None]:
    """'ACME HAULING INC' -> ('ACME HAULING', 'CORPORATION'); suffix-free names keep None."""
    n = re.sub(r"[.,]", " ", name.upper())
    n = re.sub(r"\s+", " ", n).strip()
    for pattern, form in ENTITY_SUFFIXES:
        if re.search(pattern, n):
            return re.sub(pattern, "", n).strip(), form
    return n, None


def entity_form_from_type(text: str) -> str | None:
    return ENTITY_TYPES.get(text.strip().lower())


# Uninsured motorist minimums where "state minimum" is written instead of a number.
# Source: state financial responsibility laws (California raised its minimum to 30/60 on
# 2025-01-01). States not listed cannot be compared automatically.
UM_STATE_MINIMUMS: dict[str, tuple[int, int]] = {"CA": (30_000, 60_000)}

# Commodities that usually need a refrigerated trailer: evidence the account runs reefer units.
# "Fresh produce" or frozen food only; a mixed "produce/groceries/canned" category does not count
REEFER_COMMODITIES = re.compile(r"\bfresh produce\b|\bfrozen\b", re.IGNORECASE)
