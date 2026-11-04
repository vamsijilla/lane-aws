"""Document titles people can tell apart: every document's title is unique in a submission.

Classification titles a document from its first page ("Loss run, Summit Specialty,
2024-2025"). Extraction then knows more: a loss run's line. Titles are refined after
extraction, and any two documents still sharing a title are told apart by their date, by being
an older record, or by their pages.
"""

import re
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.db.models import Document, DocumentExtraction
from lane.domain import normalize as nz
from lane.resolve.build import loss_run_line

LINE_TITLES = {
    "auto_liability": "Auto liability",
    "cargo": "Cargo",
    "physical_damage": "Physical damage",
}


def _short_years(title: str) -> str:
    """'..., 2024-2025' -> '..., 2024-25'."""
    return re.sub(r"\b(\d{4})-(\d{2})(\d{2})\b", r"\1-\3", title)


def refine_titles(docs: list[Document], extractions: dict[str, Any]) -> None:
    """Name each loss run's line, then make every title unique. `docs` in document order."""
    for doc in docs:
        data = extractions.get(doc.id)
        if not data:
            continue
        built = _from_data(doc.doc_type, data)
        if built:
            doc.title = built
            continue
        name = LINE_TITLES.get(loss_run_line(data) or "") if doc.doc_type == "loss_run" else None
        if name and doc.title.startswith("Loss run"):
            doc.title = _short_years(f"{name} loss run{doc.title[len('Loss run') :]}")
    _unique(docs)


def _val(data: dict[str, Any], key: str) -> str | None:
    v = data.get(key)
    if isinstance(v, dict):
        v = v.get("value")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return str(v) if v not in (None, "") else None  # pyright: ignore[reportUnknownArgumentType]


def _from_data(doc_type: str, data: dict[str, Any]) -> str | None:
    """The same title whatever the classifier called the document, from what extraction read:
    'Cargo loss run, Summit Specialty, 2024-25'; 'IFTA return, Q3 2025'."""
    if doc_type == "loss_run":
        from lane.api.summaries import carrier_key  # one name per carrier, as everywhere

        line = LINE_TITLES.get(loss_run_line(data) or "")
        start = nz.to_date(_val(data, "period_start"))
        end = nz.to_date(_val(data, "period_end"))
        carrier = carrier_key(_val(data, "carrier"))
        if not (line and start and end):
            return None
        years = f"{start.year}-{end.year % 100:02d}"
        return (
            f"{line} loss run, {nz.display_case(carrier) or carrier}, {years}"
            if carrier
            else (f"{line} loss run, {years}")
        )
    if doc_type == "mvr":
        from lane.domain import geo

        code = geo.to_code(_val(data, "license_state"))
        who = _val(data, "driver_name")
        if not (code and who):
            return None
        last, _, first = who.partition(",")
        person = f"{last.strip()}, {first.strip()}" if first else who.strip()
        return f"MVR, {geo.name(code)} driver record, {nz.display_case(person) or person}"
    if doc_type == "ifta_return":
        end = nz.to_date(_val(data, "period_end"))
        return f"IFTA return, Q{(end.month - 1) // 3 + 1} {end.year}" if end else None
    return None


def refine_submission_titles(session: Session, submission_id: str) -> None:
    docs = list(
        session.scalars(
            select(Document)
            .where(Document.submission_id == submission_id)
            .order_by(Document.ordinal)
        )
    )
    extractions = {
        x.document_id: x.data
        for x in session.scalars(
            select(DocumentExtraction).where(DocumentExtraction.submission_id == submission_id)
        )
        if x.data
    }
    refine_titles(docs, extractions)


def _unique(docs: list[Document]) -> None:
    """No two documents share a title: add the date, 'older record', or the pages."""
    groups: dict[str, list[Document]] = defaultdict(list)
    for d in docs:
        groups[d.title].append(d)
    for title, same in groups.items():
        if len(same) < 2:
            continue
        dates = [d.document_date for d in same]
        if all(dates) and len(set(dates)) == len(same):
            for d in same:
                d.title = f"{title}, {nz.long_date(d.document_date)}"
            continue
        for n, d in enumerate(same, start=1):
            if d.superseded_by_id and sum(1 for x in same if x.superseded_by_id) == 1:
                d.title = f"{title} (older record)"
            elif d.page_from != d.page_to:
                d.title = f"{title} (pages {d.page_from}-{d.page_to})"
            else:
                d.title = f"{title} ({n} of {len(same)})"
    # A last pass in case two refinements met: number what is still shared
    seen: dict[str, int] = {}
    for d in docs:
        seen[d.title] = seen.get(d.title, 0) + 1
        if seen[d.title] > 1:
            d.title = f"{d.title} ({seen[d.title]})"
