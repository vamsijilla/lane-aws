"""Turn per-page verdicts into logical documents, then mark older copies as superseded.

Pure functions over plain data, so the rules are easy to test and to explain:

Splitting, within one file, a new document starts when
  - it is the first page, or
  - the page says it starts a new document (worksheets of the same type in one workbook
    stay together as tabs of one document), or
  - the document type changes, or
  - both pages show an identifier, the identifiers differ, and so do the subjects' names (a
    closing page may print a report number instead of the license number).

Supersession, across the submission: documents of the same type about the same subject and
period (same license number, same policy) are copies; the most recent one by document date is
active and the rest are superseded. Supersession is shown, never treated as a conflict.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field

from lane.db.models import ConfidenceBand

SUPERSEDABLE = frozenset(
    {"mvr", "drivers_license", "loss_run", "loss_history_letter", "ifta_return"}
)


@dataclass(frozen=True)
class PageCall:
    """The classification of one page, after combining the model with keyword signals."""

    page_id: str
    attachment_id: str
    page_no: int
    doc_type: str
    starts_new_document: bool
    subject_id: str | None
    subject_name: str | None
    period: str | None
    document_date: str | None
    title: str
    confidence: ConfidenceBand
    reasons: tuple[str, ...] = ()
    needs_review: tuple[str, ...] = ()
    is_sheet: bool = False
    own_document: bool = False  # starts a document of its own, even inside one workbook
    ignored: bool = False  # belongs to no document (a hidden lookup sheet)


@dataclass
class LogicalDoc:
    key: str  # stable within a run: "<attachment_id>:<first page>"
    attachment_id: str
    doc_type: str
    pages: list[PageCall]
    superseded_by: str | None = None
    review: list[str] = field(default_factory=lambda: list[str]())

    @property
    def first(self) -> PageCall:
        return self.pages[0]

    def best(self, attr: str) -> str | None:
        """First non-empty value across pages (headers usually sit on the first page)."""
        for page in self.pages:
            value = getattr(page, attr)
            if value:
                return value
        return None

    @property
    def confidence(self) -> ConfidenceBand:
        order = [ConfidenceBand.LOW, ConfidenceBand.MEDIUM, ConfidenceBand.HIGH]
        return min((p.confidence for p in self.pages), key=order.index)


def norm_id(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[^A-Z0-9]", "", value.upper())
    return cleaned or None


def split_pages(pages: list[PageCall]) -> list[LogicalDoc]:
    """Pages must be ordered by attachment then page number."""
    docs: list[LogicalDoc] = []
    for page in pages:
        if page.ignored:
            continue
        current = docs[-1] if docs else None
        if current is None or _starts_new(current, page):
            docs.append(
                LogicalDoc(
                    key=f"{page.attachment_id}:{page.page_no}",
                    attachment_id=page.attachment_id,
                    doc_type=page.doc_type,
                    pages=[page],
                )
            )
        else:
            current.pages.append(page)
    return docs


def _starts_new(current: LogicalDoc, page: PageCall) -> bool:
    if page.attachment_id != current.attachment_id:
        return True
    if page.doc_type != current.doc_type:
        return True
    if page.own_document or current.first.own_document:
        return True
    # Worksheets of one workbook with the same type are tabs of one document.
    if page.starts_new_document and not (page.is_sheet and current.first.is_sheet):
        return True
    prev_id = norm_id(current.best("subject_id"))
    this_id = norm_id(page.subject_id)
    if not (prev_id and this_id and prev_id != this_id):
        return False
    # A continuation page can carry another number (a report or control number). Only split when
    # the subject's name changes too.
    return not same_name(current.best("subject_name"), page.subject_name)


def same_name(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    tokens_a = set(re.findall(r"[A-Z0-9]+", a.upper()))
    tokens_b = set(re.findall(r"[A-Z0-9]+", b.upper()))
    return bool(tokens_a and tokens_b) and (tokens_a <= tokens_b or tokens_b <= tokens_a)


def mark_supersession(docs: list[LogicalDoc]) -> None:
    groups: dict[tuple[str, str, str | None], list[LogicalDoc]] = defaultdict(list)
    for doc in docs:
        subject = norm_id(doc.best("subject_id"))
        if doc.doc_type in SUPERSEDABLE and subject:
            groups[(doc.doc_type, subject, norm_id(doc.best("period")))].append(doc)

    for group in groups.values():
        if len(group) < 2:
            continue
        dated = [d for d in group if d.best("document_date")]
        if len(dated) != len(group):
            for doc in group:
                doc.review.append(
                    "Several copies of this document were sent and their dates could not be "
                    "compared; confirm which one is current"
                )
            continue
        dated.sort(key=lambda d: d.best("document_date") or "")
        newest = dated[-1]
        for doc in dated[:-1]:
            if doc.best("document_date") == newest.best("document_date"):
                doc.review.append("Two copies share the same date; confirm they are identical")
                continue
            doc.superseded_by = newest.key
