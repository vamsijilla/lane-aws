"""Missing items in tiers: required before quote, also helpful, or needed to bind."""

from lane.config import Settings

TIERS = ("required", "bind", "helpful")


def tier_of(settings: Settings, code: str) -> str:
    for prefix, tier in settings.missing_tiers.items():
        if code.startswith(prefix) and tier in TIERS:
            return tier
    return "required"
