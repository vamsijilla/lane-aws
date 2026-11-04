"""Misnamed or just a different format: a file named "MTC 23-24" that holds a loss history
letter is the record its name promises, in another format. Misnamed means the name claims a
different kind of document."""

from lane.config import Settings


def same_family(settings: Settings, a: str, b: str) -> bool:
    return any(a in members and b in members for members in settings.doc_type_families.values())


def format_note(settings: Settings, hint: str | None, doc_type: str) -> str | None:
    """'Different format: letter, not a loss run' when the name and content are one family."""
    if not hint or hint == doc_type or not same_family(settings, hint, doc_type):
        return None
    names = settings.doc_format_names
    return (
        f"Different format: {names.get(doc_type, doc_type.replace('_', ' '))}, "
        f"not a {names.get(hint, hint.replace('_', ' '))}"
    )
