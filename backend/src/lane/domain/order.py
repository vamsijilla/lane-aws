"""Stable orders for lists people read and outputs are hashed over.

A list's order must never depend on how rows came back from the database or on set iteration:
the same inputs give the same output, byte for byte.
"""

import re
from collections.abc import Iterable

_PARTS = re.compile(r"(\d+)")


def natural_key(text: str) -> tuple[tuple[int, int | str], ...]:
    """'Unit 2' before 'Unit 10'; case-insensitive; numbers compare as numbers."""
    return tuple((0, int(p)) if p.isdigit() else (1, p.casefold()) for p in _PARTS.split(text) if p)


def stable_items(items: Iterable[str]) -> list[str]:
    """Distinct, in natural order."""
    return sorted(dict.fromkeys(items), key=natural_key)
