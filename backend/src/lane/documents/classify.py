"""Page classification: ask the model, check it against keyword signals, decide a confidence.

The model reads content (text layer, image, worksheet cells). Keyword signals act as an
independent check on pages that have text. Agreement raises confidence; disagreement sends the
page to review. If the model is unavailable, keywords carry text pages and image pages wait for
a human, so the run degrades instead of failing.
"""

import hashlib
from dataclasses import dataclass
from typing import Any, get_args

from lane.config import Settings
from lane.db.models import ConfidenceBand, Page, PageKind
from lane.documents.layout import sheet_to_text, words_to_text
from lane.documents.signals import KeywordVote, keyword_vote
from lane.documents.split import PageCall
from lane.documents.templates import Match
from lane.documents.tier1 import Decision
from lane.documents.types import DOC_TYPE_LABELS, DocType, PageDocType, PageVerdict
from lane.llm.gateway import ImagePart, LlmRequest, Part, TextPart
from lane.llm.prompts import load_prompt

CLASSIFIER_VERSION = "4"  # 4: deterministic tier 1 before known layouts and the model
PROMPT_NAME = "classify_page"

# Pairs that are related views of the same paperwork, not contradictions.
MODEL_TYPES = frozenset(get_args(PageDocType))  # the types the model can return
COMPATIBLE = frozenset(
    {
        (DocType.APPLICATION, DocType.DRIVER_SCHEDULE),
        (DocType.APPLICATION, DocType.VEHICLE_SCHEDULE),
        (DocType.APPLICATION, DocType.QUOTE_FORM),
        (DocType.QUOTE_FORM, DocType.APPLICATION),
        (DocType.LOSS_HISTORY_LETTER, DocType.LOSS_RUN),
    }
)


@dataclass(frozen=True)
class PageInput:
    page: Page
    filename: str
    page_count: int  # pages in the same file
    text: str  # text layer, worksheet cells or email text; empty for images
    image_png: bytes | None
    layout: dict[str, Any] | None = None  # word boxes or cells, for layout templates


def page_text(page: Page, layout: dict[str, object] | None, settings: Settings) -> str:
    if layout is None:
        return ""
    if page.kind is PageKind.SHEET:
        return sheet_to_text(layout, max_cells=settings.classify_sheet_cells)
    return words_to_text(layout)[: settings.classify_text_chars]


def build_request(item: PageInput, settings: Settings) -> LlmRequest[PageVerdict]:
    page = item.page
    where = (
        f"worksheet {page.page_no} of {item.page_count}, named {page.sheet_name!r}"
        if page.kind is PageKind.SHEET
        else f"page {page.page_no} of {item.page_count}"
    )
    parts: list[Part] = [
        TextPart(
            f"Filename (untrusted hint): {item.filename}\n"
            f"Position: {where}\n"
            f"How the page is stored: {page.kind.value}"
            + (", hidden from the sender" if page.sheet_hidden else "")
        )
    ]
    if item.text:
        parts.append(TextPart(item.text, label="page text"))
    if item.image_png:
        parts.append(ImagePart(item.image_png, label=f"{where} of {item.filename}"))
    return LlmRequest(
        purpose="classify_page",
        subject_id=page.id,
        model=settings.llm_classify_model,
        effort=settings.llm_classify_effort,
        prompt=load_prompt(PROMPT_NAME),
        parts=parts,
        output_type=PageVerdict,
        max_tokens=2000,
    )


def email_body_call(page: Page) -> PageCall:
    return PageCall(
        page_id=page.id,
        attachment_id=page.attachment_id,
        page_no=page.page_no,
        doc_type=DocType.EMAIL_BODY.value,
        starts_new_document=True,
        subject_id=None,
        subject_name=None,
        period=None,
        document_date=None,
        title="Email from the agent",
        confidence=ConfidenceBand.HIGH,
        reasons=("Body of the submission email",),
    )


def fuse(
    item: PageInput, verdict: PageVerdict | None, error: str | None
) -> tuple[PageCall, KeywordVote]:
    page = item.page
    vote = keyword_vote(item.text) if item.text else KeywordVote(None, 0.0, None, {})
    reasons: list[str] = []
    review: list[str] = []

    if verdict is None:
        doc_type = vote.doc_type or DocType.OTHER
        confidence = ConfidenceBand.LOW
        if vote.doc_type:
            reasons.append("AI classification unavailable; typed from keywords on the page")
        else:
            reasons.append("AI classification unavailable and the page has no readable text")
        review.append("Confirm the document type of this page")
        if error:
            reasons.append(error)
        return _call(page, doc_type, None, ConfidenceBand.LOW, reasons, review), vote

    model_type = DocType(verdict.doc_type)
    band = ConfidenceBand(verdict.confidence)
    if vote.doc_type is None:
        reasons.append(
            "Image only; no text to cross-check" if not item.text else "Keywords inconclusive"
        )
        confidence = band
    elif vote.doc_type == model_type:
        reasons.append("AI reading and keyword signals agree")
        confidence = (
            ConfidenceBand.HIGH if band is not ConfidenceBand.LOW else ConfidenceBand.MEDIUM
        )
    elif (model_type, vote.doc_type) in COMPATIBLE:
        reasons.append(
            f"AI reads {DOC_TYPE_LABELS[model_type].lower()}; keywords point to the related "
            f"{DOC_TYPE_LABELS[vote.doc_type].lower()}"
        )
        confidence = ConfidenceBand.LOW if band is ConfidenceBand.LOW else ConfidenceBand.MEDIUM
    else:
        confidence = ConfidenceBand.LOW
        reasons.append(
            f"AI reads {DOC_TYPE_LABELS[model_type].lower()} but keywords suggest "
            f"{DOC_TYPE_LABELS[vote.doc_type].lower()}"
        )
        review.append("AI and keyword signals disagree on the document type")
    if band is ConfidenceBand.LOW and not review:
        review.append("AI was unsure of the document type")
    if page.quality.value == "poor":
        confidence = ConfidenceBand.LOW
        reasons.append("Page image is poor quality")
    return _call(page, model_type, verdict, confidence, reasons, review), vote


def _call(
    page: Page,
    doc_type: DocType,
    verdict: PageVerdict | None,
    confidence: ConfidenceBand,
    reasons: list[str],
    review: list[str],
) -> PageCall:
    return PageCall(
        page_id=page.id,
        attachment_id=page.attachment_id,
        page_no=page.page_no,
        doc_type=doc_type.value,
        starts_new_document=verdict.starts_new_document if verdict else page.page_no == 1,
        subject_id=verdict.subject_id if verdict else None,
        subject_name=verdict.subject_name if verdict else None,
        period=verdict.period if verdict else None,
        document_date=verdict.document_date if verdict else None,
        title=verdict.title if verdict else DOC_TYPE_LABELS[doc_type],
        confidence=confidence,
        is_sheet=page.kind is PageKind.SHEET,
        reasons=tuple(reasons),
        needs_review=tuple(review),
    )


# --- tier 1: deterministic signals ---------------------------------------------------------


def settled_by_tier1(item: PageInput, decision: Decision | None) -> bool:
    """A worksheet tier 1 types (headers with data, or a lookup sheet) needs no model call:
    a sheet has no subject or period to read. Other pages still go to the model, for those
    fields, and its type is a check."""
    return decision is not None and item.page.kind is PageKind.SHEET


def fuse_tier1(
    item: PageInput,
    decision: Decision,
    match: Match | None,
    verdict: PageVerdict | None,
    error: str | None,
) -> tuple[PageCall, KeywordVote]:
    """Tier 1 types the page. A known layout or the model that disagrees sends the document to
    review, with both types named; nothing is picked silently."""
    page = item.page
    vote = keyword_vote(item.text) if item.text else KeywordVote(None, 0.0, None, {})
    is_sheet = page.kind is PageKind.SHEET
    if decision.ignored:
        return (
            PageCall(
                page_id=page.id,
                attachment_id=page.attachment_id,
                page_no=page.page_no,
                doc_type=DocType.OTHER.value,
                starts_new_document=False,
                subject_id=None,
                subject_name=None,
                period=None,
                document_date=None,
                title="Lookup values",
                confidence=ConfidenceBand.HIGH,
                reasons=(decision.reason,),
                is_sheet=is_sheet,
                ignored=True,
            ),
            vote,
        )
    doc_type = decision.doc_type
    assert doc_type is not None
    reasons = [decision.reason]
    review: list[str] = []
    confidence = ConfidenceBand.HIGH
    if match is not None and match.template.doc_type != doc_type:
        if doc_type in match.template.also_reads_as:
            reasons.append(f"Known layout '{match.template.name}' is known to read as this type")
        else:
            confidence = ConfidenceBand.LOW
            review.append(
                f"Tier 1 signals say {DOC_TYPE_LABELS[doc_type].lower()} but the known layout "
                f"'{match.template.name}' says {DOC_TYPE_LABELS[match.template.doc_type].lower()}"
            )
    if verdict is not None:
        model_type = DocType(verdict.doc_type)
        if model_type == doc_type:
            reasons.append("AI reading agrees")
        elif model_type is DocType.OTHER and doc_type.value not in MODEL_TYPES:
            # The model has no word for this type; "other" is its way of agreeing
            confidence = ConfidenceBand.MEDIUM
            reasons.append(
                f"AI reads it as other; {DOC_TYPE_LABELS[doc_type].lower()} is typed by tier 1 only"
            )
        elif (doc_type, model_type) in COMPATIBLE or (model_type, doc_type) in COMPATIBLE:
            confidence = ConfidenceBand.MEDIUM
            reasons.append(f"AI reads the related {DOC_TYPE_LABELS[model_type].lower()}")
        else:
            confidence = ConfidenceBand.LOW
            review.append(
                f"Tier 1 signals say {DOC_TYPE_LABELS[doc_type].lower()} but AI reads "
                f"{DOC_TYPE_LABELS[model_type].lower()}"
            )
        if ConfidenceBand(verdict.confidence) is ConfidenceBand.LOW and not review:
            review.append("AI was unsure of the document type")
    elif not is_sheet:
        # Deterministic evidence alone, with no model to cross-check it
        confidence = ConfidenceBand.MEDIUM
        reasons.append("AI classification unavailable; typed by tier 1 signals alone")
        if error:
            reasons.append(error)
    if page.quality.value == "poor":
        confidence = ConfidenceBand.LOW
        reasons.append("Page image is poor quality")
    return (
        PageCall(
            page_id=page.id,
            attachment_id=page.attachment_id,
            page_no=page.page_no,
            doc_type=doc_type.value,
            # A tab of another type starts a new document (the splitter sees the type change);
            # tabs of one type stay one document
            starts_new_document=True
            if is_sheet
            else (verdict.starts_new_document if verdict else page.page_no == 1),
            subject_id=verdict.subject_id if verdict else None,
            subject_name=verdict.subject_name if verdict else None,
            period=verdict.period if verdict else None,
            document_date=verdict.document_date if verdict else None,
            title=verdict.title if verdict else DOC_TYPE_LABELS[doc_type],
            confidence=confidence,
            reasons=tuple(reasons),
            needs_review=tuple(review),
            is_sheet=is_sheet,
        ),
        vote,
    )


# --- known layouts -------------------------------------------------------------------------


def settled_by_layout(item: PageInput, match: Match | None) -> bool:
    """A complete, unchallenged layout match types the page with no model call."""
    if match is None or match.missing:
        return False
    vote = keyword_vote(item.text) if item.text else KeywordVote(None, 0.0, None, {})
    return _keywords_allow(match.template.doc_type, vote.doc_type)


def _keywords_allow(layout_type: DocType, keyword_type: DocType | None) -> bool:
    return (
        keyword_type is None
        or keyword_type == layout_type
        or (layout_type, keyword_type) in COMPATIBLE
        or (keyword_type, layout_type) in COMPATIBLE
    )


def fuse_layout(
    item: PageInput, match: Match, verdict: PageVerdict | None, error: str | None
) -> tuple[PageCall, KeywordVote]:
    """Type a page from its known layout. The model is consulted only when the layout could not
    read a field its type needs, the keywords object, or the page was sampled for audit; when
    it disagrees with the layout, the page goes to review."""
    page = item.page
    vote = keyword_vote(item.text) if item.text else KeywordVote(None, 0.0, None, {})
    layout_type = match.template.doc_type
    reasons = [match.reason]
    review: list[str] = []
    confidence = ConfidenceBand.HIGH
    known = set(match.template.also_reads_as)
    if vote.doc_type == layout_type:
        reasons.append("Keyword signals agree")
    elif vote.doc_type is not None and vote.doc_type in known:
        reasons.append(
            f"Keywords suggest {DOC_TYPE_LABELS[vote.doc_type].lower()}, which this layout is "
            "known to read as"
        )
    elif not _keywords_allow(layout_type, vote.doc_type) and vote.doc_type is not None:
        confidence = ConfidenceBand.LOW
        review.append(
            f"Known layout says {DOC_TYPE_LABELS[layout_type].lower()} but keywords suggest "
            f"{DOC_TYPE_LABELS[vote.doc_type].lower()}"
        )
    fields = dict(match.fields)
    if verdict is not None:
        model_type = DocType(verdict.doc_type)
        if model_type != layout_type and model_type in known:
            reasons.append(
                f"AI reads {DOC_TYPE_LABELS[model_type].lower()}, which this layout is known to "
                "read as"
            )
        elif model_type != layout_type:
            confidence = ConfidenceBand.LOW
            review.append(
                f"Known layout says {DOC_TYPE_LABELS[layout_type].lower()} but AI reads "
                f"{DOC_TYPE_LABELS[model_type].lower()}"
            )
        elif match.missing:
            reasons.append(f"AI read what the layout could not: {', '.join(match.missing)}")
        for name in ("subject_id", "subject_name", "period", "document_date"):
            fields.setdefault(name, getattr(verdict, name) or "")
    elif match.missing:
        confidence = ConfidenceBand.MEDIUM
        reasons.append(f"Could not read {', '.join(match.missing)} from the layout")
        if error:
            reasons.append(error)
    if page.quality.value == "poor":
        confidence = ConfidenceBand.LOW
        reasons.append("Page image is poor quality")
    first = match.first_page
    return (
        PageCall(
            page_id=page.id,
            attachment_id=page.attachment_id,
            page_no=page.page_no,
            doc_type=layout_type.value,
            starts_new_document=first if first is not None else page.page_no == 1,
            subject_id=fields.get("subject_id") or None,
            subject_name=fields.get("subject_name") or None,
            period=fields.get("period") or None,
            document_date=fields.get("document_date") or None,
            title=match.title or (verdict.title if verdict else DOC_TYPE_LABELS[layout_type]),
            confidence=confidence,
            is_sheet=page.kind is PageKind.SHEET,
            reasons=tuple(reasons),
            needs_review=tuple(review),
            own_document=match.template.own_document,
            ignored=match.template.ignore,
        ),
        vote,
    )


def audited(page_id: str, rate: float) -> bool:
    """A stable sample of layout-typed pages that the model also reads, to measure templates."""
    if rate <= 0:
        return False
    return int(hashlib.sha256(page_id.encode()).hexdigest()[:8], 16) % 10_000 < rate * 10_000


def layout_signals(match: Match, verdict: PageVerdict | None) -> dict[str, Any]:
    return {
        "method": "layout",
        "template": match.template.id,
        "template_name": match.template.name,
        "score": match.score,
        "hits": match.hits,
        "of": match.of,
        "landmarks": [
            {"text": lm.text, "box": list(lm.box) if lm.box else None} for lm in match.landmarks
        ],
        "missing": list(match.missing),
        "audit": {
            "model_type": verdict.doc_type,
            "agrees": verdict.doc_type == match.template.doc_type.value
            or verdict.doc_type in {d.value for d in match.template.also_reads_as},
        }
        if verdict is not None
        else None,
    }
