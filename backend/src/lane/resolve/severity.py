"""How much a conflict matters, and the order conflicts are listed in everywhere."""

from lane.config import Settings

LEVELS = ("high", "medium", "low")


def severity(settings: Settings, kind: str, attribute: str, blocks_decline: bool = False) -> str:
    """The configured level; a conflict that changes a decline-capable rule is always high."""
    if blocks_decline:
        return "high"
    level = settings.conflict_severity.get(f"{kind}.{attribute}", "medium")
    return level if level in LEVELS else "medium"


def decline_capable() -> frozenset[str]:
    from lane.rules.service import ruleset

    return frozenset(r.id for r in ruleset().rules if r.decline)


def conflict_order(blocking: bool, level: str, title: str) -> tuple[bool, int, str]:
    """Blocking first (it changes a rule outcome), then high, medium, low, then by title."""
    return (not blocking, LEVELS.index(level), title.casefold())
