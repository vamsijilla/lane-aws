"""Ages, always counted from the application date (A02): the age a driver was when the agent
applied. The same date in rules, summaries, masking and the model's brief, so a driver's age
never reads differently on two screens.

Dates of birth are personal data. When documents disagree on one, the masked view shows only
the parts that differ ("Month and day differ: Oct 3 vs Oct 30, year hidden"), so the conflict
is visible without revealing the date.
"""

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.db.models import ResolvedEntity, ResolvedField, Submission

# Month and day read as one part ("Oct 3"); the year as the other.
MONTH_DAY, YEAR = "month and day", "year"


def age_on(dob: date, on: date) -> int:
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


def to_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def application_date(session: Session, submission_id: str) -> date | None:
    """The applicant's application date, else the day the email arrived."""
    value = session.scalar(
        select(ResolvedField.value)
        .join(ResolvedEntity, ResolvedField.entity_id == ResolvedEntity.id)
        .where(
            ResolvedField.submission_id == submission_id,
            ResolvedEntity.kind == "applicant",
            ResolvedField.attribute == "application_date",
            ResolvedField.state == "known",
        )
    )
    if (d := to_date(value)) is not None:
        return d
    sub = session.get(Submission, submission_id)
    return sub.email.received_at.date() if sub else None


def differing_parts(dates: list[date]) -> list[str]:
    parts: list[str] = []
    if len({(d.month, d.day) for d in dates}) > 1:
        parts.append(MONTH_DAY)
    if len({d.year for d in dates}) > 1:
        parts.append(YEAR)
    return parts


def _shown(d: date, parts: list[str]) -> str:
    """Only the parts that differ: 'Oct 3' or '1991'. Never both: that is the whole date."""
    if parts == [MONTH_DAY]:
        return f"{d.strftime('%b')} {d.day}"
    if parts == [YEAR]:
        return str(d.year)
    return ""


def _hidden(parts: list[str]) -> str:
    return YEAR if parts == [MONTH_DAY] else MONTH_DAY


def masked_candidate(d: date, parts: list[str], on: date) -> str:
    """'Oct 3, ••••': the parts that differ, the rest hidden. The screens caption the row
    "Years hidden for privacy" once instead of saying so in every cell."""
    shown = _shown(d, parts)
    if parts == [MONTH_DAY]:
        return f"{shown}, ••••"
    if parts == [YEAR]:
        return f"••• ••, {shown}"
    return f"age {age_on(d, on)} (date hidden)"


def difference(dates: list[date], on: date | None = None) -> str | None:
    """'Month and day differ: Oct 3 vs Oct 30, year hidden'; None when they do not differ."""
    distinct = sorted(set(dates))
    parts = differing_parts(distinct)
    if len(distinct) < 2 or not parts:
        return None
    if len(parts) == 2:
        ages = " vs ".join(f"age {age_on(d, on)}" for d in distinct) if on else "ages hidden"
        return f"Every part of the date differs ({ages}); the dates stay hidden"
    verb = "differ" if parts == [MONTH_DAY] else "differs"
    values = " vs ".join(_shown(d, parts) for d in distinct)
    return f"{parts[0].capitalize()} {verb}: {values}, {_hidden(parts)} hidden"
