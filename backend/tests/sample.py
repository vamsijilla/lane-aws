"""The sample submission, rebuilt in memory from tests/fixtures/sample_submission.json: board,
rule view and rule results, with no OCR, models or database."""

import json
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

from lane.db.models import Document
from lane.documents.titles import refine_titles
from lane.resolve.board import Board
from lane.resolve.build import Builder
from lane.resolve.derive import finalize
from lane.resolve.missing import MissingItem, find_missing
from lane.resolve.service import _jsonable, stable_id  # pyright: ignore[reportPrivateUsage]
from lane.rules.engine import Decision, evaluate
from lane.rules.model import RuleResult
from lane.rules.service import ruleset
from lane.rules.view import EntityView, FieldView, SubmissionView

FIXTURE = Path(__file__).with_name("fixtures") / "sample_submission.json"
SID = "sample"


@cache
def raw() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


def received() -> date:
    return date.fromisoformat(raw()["received"])


def board() -> Board:
    data = raw()
    skip = {"created_at", "updated_at"}
    docs = [Document(**{k: v for k, v in d.items() if k not in skip}) for d in data["documents"]]
    refine_titles(docs, data["extractions"])  # as the extract step does
    facts = {(d, p): fid for d, p, fid, _g in data["facts"]}
    ungrounded = frozenset(fid for _d, _p, fid, g in data["facts"] if g == "none")
    b = Builder(docs, data["extractions"], facts, today=received(), ungrounded=ungrounded).build()
    finalize(b, today=received())
    return b


def view_of(b: Board) -> tuple[SubmissionView, dict[str, str]]:
    """The rule view the stored rows would give, and the open conflicts (field id -> title)."""
    view = SubmissionView()
    conflicts: dict[str, str] = {}
    for (kind, key), e in b.entities.items():
        eid = stable_id(SID, kind, key)
        fields: dict[str, FieldView] = {}
        for attr, f in e.fields.items():
            fid = stable_id(eid, attr)
            v = f.value
            fv = FieldView(
                fid,
                f.label,
                f.state,
                _jsonable(v.compared.norm) if v else None,
                v.compared.display if v else None,
                [c.as_dict() for c in f.candidates],
            )
            fields[attr] = fv
            view.fields_by_id[fid] = fv
            if f.state == "conflict" and f.conflict:
                conflicts[fid] = f.label
        details = _jsonable(e.details)
        view.entities.append(
            EntityView(eid, kind, key, e.label, details, sorted(e.found_in), fields, view)
        )
    return view, conflicts


def rules() -> tuple[dict[str, RuleResult], Decision, Board]:
    b = board()
    view, conflicts = view_of(b)
    results, decision = evaluate(view, ruleset(), received=received(), conflicts=conflicts)
    return {r.spec.id: r for r in results}, decision, b


def missing(b: Board) -> list[MissingItem]:
    return find_missing(b)


def conflicts_of(b: Board) -> tuple[list[Any], dict[str, str]]:
    """Unsaved Conflict rows as the resolver would store them, and field id -> attribute."""
    from lane.db.models import Conflict

    rows: list[Conflict] = []
    attributes: dict[str, str] = {}
    for (kind, key), e in b.entities.items():
        eid = stable_id(SID, kind, key)
        for attr, f in e.fields.items():
            fid = stable_id(eid, attr)
            attributes[fid] = attr
            if f.state == "conflict" and f.conflict:
                title = str(
                    f.conflict.get("card_title")
                    or (f.label if len(b.of_kind(kind)) <= 1 else f"{f.label}: {e.label}")
                )
                rows.append(
                    Conflict(
                        field_id=fid,
                        title=title,
                        entity_id=eid,
                        candidates=[c.as_dict() for c in f.active],
                        resolution=None,
                        hint=f.conflict.get("hint"),
                        notes=list(f.conflict.get("notes") or []),
                        blocking_rules=[],
                    )
                )
    return sorted(rows, key=lambda c: c.title), attributes
