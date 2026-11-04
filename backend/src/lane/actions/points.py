"""What an agent's reply answered, question by question.

A point is one underlying question: an entity and a topic. Lines of a request item that share
a key are one point (who drives: the owner's license and whether the owner drives); each
vehicle, each missing document, each conflict is its own. Items for the agent's information are
not points. A reply is measured against what the agent was asked: the latest request a person
marked as sent, or, when none was, the last draft (and the report says so).

- answered: a requested question that is gone now
- answered, not requested: a question switched off in the request and gone now
- still open: a requested question still there
- new: a question that was not in the request at all (often a conflict the reply raised)

Counts are kept per group (before quote, before binding) and per item.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.db.models import AgentRequestItem, EmailDraft, RequestSnapshot

COUNTED_GROUPS = ("before_quote", "before_binding")  # "inform" items ask nothing


@dataclass
class Point:
    key: str
    group: str
    item: str  # the request item's heading
    lines: list[str]
    requested: bool


@dataclass
class PointDiff:
    basis: str  # sent | draft
    basis_by: str | None
    basis_at: datetime | None
    basis_version: int | None
    answered: list[Point] = field(default_factory=lambda: list[Point]())
    not_requested: list[Point] = field(default_factory=lambda: list[Point]())
    still_open: list[Point] = field(default_factory=lambda: list[Point]())
    new: list[Point] = field(default_factory=lambda: list[Point]())

    def as_dict(self) -> dict[str, Any]:
        groups: dict[str, dict[str, Any]] = {}
        for state, pts in (("answered", self.answered), ("open", self.still_open)):
            for p in pts:
                g = groups.setdefault(p.group, {"answered": 0, "requested": 0, "items": {}})
                g["requested"] += 1
                it = g["items"].setdefault(p.item, {"item": p.item, "answered": 0, "points": []})
                if state == "answered":
                    g["answered"] += 1
                    it["answered"] += 1
                it["points"].append({"state": state, "lines": p.lines})
        return {
            "basis": self.basis,
            "basis_by": self.basis_by,
            "basis_at": self.basis_at.isoformat() if self.basis_at else None,
            "basis_version": self.basis_version,
            "answered": len(self.answered),
            "requested": len(self.answered) + len(self.still_open),
            "groups": [
                {
                    "group": name,
                    "answered": groups[name]["answered"],
                    "requested": groups[name]["requested"],
                    "items": list(groups[name]["items"].values()),
                }
                for name in COUNTED_GROUPS
                if name in groups
            ],
            "answered_not_requested": [_row(p) for p in self.not_requested],
            "new": [_row(p) for p in self.new],
        }


def _row(p: Point) -> dict[str, Any]:
    return {"item": p.item, "lines": p.lines}


def item_rows(session: Session, submission_id: str) -> list[dict[str, Any]]:
    """The request as it stands: every item, included or not, with its lines and their keys."""
    return [
        {
            "id": i.id,
            "group": i.group,
            "text": i.text,
            "details": list(i.details),
            "keys": list(i.detail_keys),
            "included": i.included,
            "source_kind": i.source_kind,
        }
        for i in session.scalars(
            select(AgentRequestItem)
            .where(AgentRequestItem.submission_id == submission_id)
            .order_by(AgentRequestItem.ordinal)
        )
    ]


def points_of(rows: list[dict[str, Any]]) -> dict[str, Point]:
    out: dict[str, Point] = {}
    for r in rows:
        group = str(r.get("group", "before_quote"))
        if group not in COUNTED_GROUPS:
            continue
        details = [str(d) for d in r["details"]]
        keys = [str(k) for k in cast(list[Any], r.get("keys") or [])]
        if len(keys) != len(details):  # a request saved before lines had keys
            keys = details
        pairs = list(zip(keys, details, strict=True)) or [(f"I:{r['id']}", str(r["text"]))]
        for k, line in pairs:
            key = f"{r['id']}|{k}"
            point = out.setdefault(key, Point(key, group, str(r["text"]), [], bool(r["included"])))
            point.lines.append(line)
    return out


def baseline(
    session: Session, submission_id: str
) -> tuple[str, str | None, datetime | None, int | None, list[dict[str, Any]]]:
    """What the agent was asked: the latest request marked sent, else the current (last
    drafted) request. Call before the request is rebuilt."""
    snap = session.scalars(
        select(RequestSnapshot)
        .where(RequestSnapshot.submission_id == submission_id)
        .order_by(RequestSnapshot.sent_at.desc())
        .limit(1)
    ).first()
    if snap is not None:
        by = snap.sent_by.removeprefix("user:")
        return "sent", by, snap.sent_at, snap.draft_version, snap.items
    draft = session.scalars(
        select(EmailDraft)
        .where(EmailDraft.submission_id == submission_id)
        .order_by(EmailDraft.version.desc())
        .limit(1)
    ).first()
    return (
        "draft",
        None,
        draft.created_at if draft else None,
        draft.version if draft else None,
        item_rows(session, submission_id),
    )


def diff(
    base: tuple[str, str | None, datetime | None, int | None, list[dict[str, Any]]],
    after: list[dict[str, Any]],
) -> PointDiff:
    basis, by, at, version, before = base
    was, now = points_of(before), points_of(after)
    out = PointDiff(basis, by, at, version)
    for key, p in was.items():
        if key in now:
            if p.requested:
                out.still_open.append(Point(key, p.group, p.item, now[key].lines, True))
        elif p.requested:
            out.answered.append(p)
        else:
            out.not_requested.append(p)
    out.new = [p for key, p in now.items() if key not in was and p.requested]
    return out
