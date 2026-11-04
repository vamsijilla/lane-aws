"""Extraction: from a logical document to typed data and grounded facts.

PDF and image documents: the model reads the pages and copies values verbatim with a quote and
page; code normalizes the values and finds each one on the page's word boxes.
Worksheets: the model points at cells; code reads the values from the cells.
Then arithmetic checks run, and every fact gets a confidence band with its reasons.
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, cast

from pydantic import BaseModel

from lane.config import Settings
from lane.db.models import ConfidenceBand, Document, ExtractionStatus, Page, PageKind, QualityBand
from lane.documents.layout import sheet_to_text, words_to_text
from lane.domain import normalize as nz
from lane.extract import schemas as s
from lane.extract.checks import Check, run_checks
from lane.extract.grounding import PageWords
from lane.extract.sheets import CellValue, Sheet, Workbook
from lane.llm.gateway import ImagePart, LlmRequest, LlmResult, Part, TextPart
from lane.llm.prompts import Prompt, load_prompt

EXTRACTOR_VERSION = "1"
MIN_GROUNDING_RATE = 0.8
MIN_OCR_CONFIRMATION = 0.6


def ocr_confirmation(facts: list["FactDraft"]) -> float | None:
    """Share of image-read values that OCR also found, or None when there is no image text."""
    if not facts:
        return None
    return sum(1 for f in facts if f.grounding.startswith("ocr_")) / len(facts)


PDF_SCHEMAS: dict[str, tuple[type[BaseModel], str]] = {
    "mvr": (s.MvrExtraction, "extract_mvr"),
    "drivers_license": (s.LicenseExtraction, "extract_drivers_license"),
    "loss_run": (s.LossRunExtraction, "extract_loss_run"),
    "loss_history_letter": (s.LossLetterExtraction, "extract_loss_history_letter"),
    "ifta_return": (s.IftaExtraction, "extract_ifta_return"),
    "quote_form": (s.QuoteFormExtraction, "extract_quote_form"),
    "email_body": (s.EmailExtraction, "extract_email_body"),
    "application": (s.ApplicationPdfExtraction, "extract_application_pdf"),
    "driver_schedule": (s.DriverListExtraction, "extract_schedule_pdf"),
    "vehicle_schedule": (s.VehicleListExtraction, "extract_schedule_pdf"),
}
SHEET_SCHEMAS: dict[str, tuple[type[BaseModel], str]] = {
    "application": (s.ApplicationSheetExtraction, "extract_application"),
    "driver_schedule": (s.DriverSheetExtraction, "extract_driver_schedule"),
    "vehicle_schedule": (s.VehicleSheetExtraction, "extract_vehicle_schedule"),
}


def extractable(doc: Document) -> bool:
    return doc.doc_type in PDF_SCHEMAS or doc.doc_type in SHEET_SCHEMAS


@dataclass
class DocPage:
    page: Page
    layout: dict[str, Any] | None
    image_png: bytes | None
    text: str  # text layer, email text, or worksheet listing


@dataclass
class DocInput:
    doc: Document
    pages: list[DocPage]

    @property
    def is_sheet(self) -> bool:
        return all(p.page.kind is PageKind.SHEET for p in self.pages)

    @property
    def is_image(self) -> bool:
        return any(p.page.kind in (PageKind.SCAN, PageKind.PHOTO) for p in self.pages)


@dataclass
class FactDraft:
    path: str
    raw: str | None
    norm: Any
    page_id: str | None
    page_no: int | None
    quote: str | None
    method: str  # sheet | text | vision
    grounding: str  # exact | fuzzy | cell | image | none
    bbox: tuple[float, float, float, float] | None = None
    cell_ref: str | None = None
    confidence: ConfidenceBand = ConfidenceBand.MEDIUM
    reasons: list[str] = field(default_factory=lambda: list[str]())
    # Extraction v2
    tier: str | None = None
    verified: bool = False
    verification: str | None = None
    second_read: str | None = None
    reads: list[dict[str, Any]] | None = None


@dataclass
class ExtractionOutcome:
    status: ExtractionStatus
    method: str
    data: dict[str, Any] | None
    facts: list[FactDraft]
    checks: list[Check]
    escalation_reasons: list[str]
    error: str | None = None
    # Extraction v2: reasons to re-read with the stronger model without asking, and the
    # expected / found / verified / missing fields
    auto_escalate: list[str] = field(default_factory=lambda: list[str]())
    coverage: dict[str, Any] | None = None


# --- requests ------------------------------------------------------------------------------


def _combined_prompt(type_prompt: str) -> Prompt:
    base, specific = load_prompt("extract_document"), load_prompt(type_prompt)
    text = f"{base.text}\n\n{specific.text}"
    return Prompt(
        name=type_prompt,
        version=f"{base.version}.{specific.version}",
        text=text,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
    )


def build_request(item: DocInput, *, model: str, effort: str) -> LlmRequest[BaseModel]:
    doc = item.doc
    schema, prompt_name = (SHEET_SCHEMAS if item.is_sheet else PDF_SCHEMAS)[doc.doc_type]
    parts: list[Part] = [
        TextPart(
            f"File: {doc.attachment.filename} (name is an untrusted hint)\n"
            f"Document: {doc.title}\n"
            f"Pages: {doc.page_from} to {doc.page_to}"
        )
    ]
    for p in item.pages:
        header = (
            f"Worksheet {p.page.sheet_name!r}"
            if p.page.kind is PageKind.SHEET
            else f"Page {p.page.page_no}"
        )
        parts.append(TextPart(header))
        if p.text:
            parts.append(TextPart(p.text, label=f"{header} text"))
        if p.image_png is not None and p.page.kind is not PageKind.SHEET:
            parts.append(ImagePart(p.image_png, label=header))
    return LlmRequest(
        purpose=f"extract_{doc.doc_type}",
        subject_id=doc.id,
        model=model,
        effort=effort,
        prompt=_combined_prompt(prompt_name),
        parts=parts,
        output_type=schema,
        max_tokens=16000,
    )


def page_text_for_extraction(
    page: Page, layout: dict[str, Any] | None, email_text: str | None
) -> str:
    if page.kind is PageKind.EMAIL_BODY:
        return email_text or ""
    if layout is None:
        return ""
    if page.kind is PageKind.SHEET:
        return sheet_to_text(layout, max_cells=100_000)
    if page.kind in (PageKind.SCAN, PageKind.PHOTO):
        # The model reads the image alone; OCR stays an independent second reading.
        return ""
    return words_to_text(layout)


# --- normalization -------------------------------------------------------------------------

_DATE_KEYS = re.compile(r"(date|_start|_end|_begin|^period_begin|^period_end|birth|hire)")
_MONEY_KEYS = re.compile(
    r"(paid|reserve|recover|incurred|^tax$|interest|^total$|penalty|_due$|deductible|amount|"
    r"cost_new|stated_value|average_value|max_value|^value$|_tiv$)"
)
_NUMBER_KEYS = re.compile(
    r"(miles|gallons|^mpg$|^rate$|points|^units$|_claims$|^total_claims$|^years|^gvw$|^year$)"
)
_PERCENT_KEYS = re.compile(r"(percent|_share$)")


def normalize_value(path: str, raw: Any) -> Any:
    key = path.rsplit(".", 1)[-1].split("[")[0]
    text = None if raw is None else str(raw)
    if isinstance(raw, bool):
        return raw
    if _PERCENT_KEYS.search(key):
        out: Decimal | date | None = nz.percent(text)
    elif _DATE_KEYS.search(key):
        out = nz.to_date(text)
    elif _MONEY_KEYS.search(key):
        out = nz.money(text)
    elif _NUMBER_KEYS.search(key):
        out = nz.number(text)
    else:
        return nz.clean_text(text)
    if out is None:
        return None
    return out.isoformat() if isinstance(out, date) else str(out)


# --- PDF and image documents ---------------------------------------------------------------

ROW_META = {"page", "quote"}


type Leaf = tuple[str, str, int | None, str | None]


def _walk(data: Any, path: str, page: int | None, quote: str | None) -> list[Leaf]:
    """Leaves as (path, value, page, quote). Val dicts carry their own page and quote; table
    rows lend theirs to every field."""
    out: list[Leaf] = []
    if isinstance(data, list):
        items = cast(list[Any], data)
        for i, item in enumerate(items):
            out.extend(_walk(item, f"{path}[{i}]", page, quote))
        return out
    if not isinstance(data, dict):
        return out
    node = cast(dict[str, Any], data)
    if set(node) <= {"value", "quote", "page"} and "value" in node:
        if node.get("value") not in (None, ""):
            leaf_page = node.get("page")
            leaf_quote = node.get("quote")
            out.append(
                (
                    path,
                    str(node["value"]),
                    leaf_page if isinstance(leaf_page, int) else None,
                    leaf_quote if isinstance(leaf_quote, str) else None,
                )
            )
        return out
    own_page, own_quote = node.get("page"), node.get("quote")
    row_page = own_page if isinstance(own_page, int) else page
    row_quote = own_quote if isinstance(own_quote, str) else quote
    for key, value in node.items():
        if key in ROW_META:
            continue
        child = f"{path}.{key}" if path else key
        if isinstance(value, (dict, list)):
            out.extend(_walk(value, child, row_page, row_quote))
        elif value not in (None, ""):
            out.append((child, str(value), row_page, row_quote))
    return out


def resolve_pdf(item: DocInput, output: BaseModel) -> tuple[dict[str, Any], list[FactDraft]]:
    data = output.model_dump(mode="json")
    pages = {p.page.page_no: p for p in item.pages}
    words = {
        no: PageWords(p.layout)
        for no, p in pages.items()
        if p.layout and p.page.kind in (PageKind.TEXT, PageKind.SCAN, PageKind.PHOTO)
    }
    facts: list[FactDraft] = []
    for path, raw, page_no, quote in _walk(data, "", None, None):
        source = pages.get(page_no or -1) or item.pages[0]
        kind = source.page.kind
        draft = FactDraft(
            path=path,
            raw=raw,
            norm=normalize_value(path, raw),
            page_id=source.page.id,
            page_no=source.page.page_no,
            quote=quote,
            method="vision" if kind in (PageKind.SCAN, PageKind.PHOTO) else "text",
            grounding="image" if kind in (PageKind.SCAN, PageKind.PHOTO) else "none",
        )
        if kind is PageKind.EMAIL_BODY:
            haystack = re.sub(r"\s+", " ", source.text).lower()
            needle = re.sub(r"\s+", " ", quote or raw).lower()
            draft.grounding = "exact" if needle and needle in haystack else "none"
        elif draft.method == "vision":
            # Second reading: is the value the model read from the image also in the OCR text?
            ocr_words = words.get(source.page.page_no)
            if ocr_words is not None:
                found = ocr_words.locate(raw, quote)
                if found.kind != "none":
                    draft.grounding, draft.bbox = f"ocr_{found.kind}", found.bbox
        elif draft.method == "text":
            order = [source.page.page_no] + [n for n in words if n != source.page.page_no]
            for n in order:
                if n not in words:
                    continue
                found = words[n].locate(raw, quote)
                if found.kind != "none":
                    draft.grounding, draft.bbox = found.kind, found.bbox
                    draft.page_id, draft.page_no = pages[n].page.id, n
                    break
        facts.append(draft)
    return data, facts


# --- worksheets ----------------------------------------------------------------------------


def _cell_dict(cell: CellValue | None, interpreted: str | None = None) -> dict[str, Any] | None:
    if cell is None and not interpreted:
        return None
    return {
        "value": interpreted if interpreted else (cell.text if cell else None),
        "cell": cell.qualified if cell else None,
        "cell_value": cell.text if cell else None,
    }


def _numeric_cell(cell: CellValue | None) -> bool:
    return (
        cell is not None
        and isinstance(cell.value, int | float)
        and not isinstance(cell.value, bool)
    )


def resolve_sheet(
    item: DocInput, output: BaseModel, *, trust_cells: bool = False
) -> tuple[dict[str, Any], list[FactDraft]]:
    """`trust_cells` (v2): a number in a cell is read as the sheet displays it ("100%" for a
    percent-formatted 1); the model's own `value` is kept only for marks under an option."""

    def interpreted(cell: CellValue | None, given: str | None) -> str | None:
        return None if trust_cells and _numeric_cell(cell) else given

    sheets = [Sheet.from_grid(p.layout or {}, p.page.id) for p in item.pages]
    book = Workbook(sheets)
    page_of = {sh.name: p.page for sh, p in zip(sheets, item.pages, strict=True)}
    facts: list[FactDraft] = []
    data: dict[str, Any] = {}

    def emit(path: str, cell: CellValue | None, interpreted: str | None = None) -> None:
        if cell is None:
            return
        page = page_of[cell.sheet]
        raw = interpreted or cell.text
        facts.append(
            FactDraft(
                path=path,
                raw=raw,
                norm=normalize_value(path, interpreted if interpreted else cell.value),
                page_id=page.id,
                page_no=page.page_no,
                quote=cell.text,
                method="sheet",
                grounding="cell",
                cell_ref=cell.qualified,
            )
        )

    def table(name: str, cols: s.ColumnMap) -> None:
        fields = {
            k: v
            for k, v in cols.model_dump().items()
            if k not in {"sheet", "header_row", "first_data_row", "last_data_row"} and v
        }
        rows = book.read_table(cols.sheet, cols.first_data_row, cols.last_data_row, fields)
        data[name] = []
        for i, row in enumerate(rows):
            data[name].append({k: v.text for k, v in row.items()})
            for k, v in row.items():
                emit(f"{name}[{i}].{k}", v)

    for key, value in output:
        if isinstance(value, s.CellRef):
            cell = book.resolve(value.cell)
            data[key] = _cell_dict(cell, interpreted(cell, value.value))
            emit(key, cell, interpreted(cell, value.value))
        elif isinstance(value, s.ColumnMap):
            table("rows" if key == "table" else key, value)
        elif isinstance(value, list):
            data[key] = []
            for i, entry in enumerate(value):  # type: ignore[arg-type]
                record: dict[str, Any] = {}
                if isinstance(entry, s.CellRef):
                    cell = book.resolve(entry.cell)
                    record = _cell_dict(cell, interpreted(cell, entry.value)) or {}
                    emit(f"{key}[{i}]", cell, interpreted(cell, entry.value))
                elif isinstance(entry, BaseModel):
                    for sub, ref in entry:
                        if isinstance(ref, s.CellRef):
                            cell = book.resolve(ref.cell)
                            record[sub] = cell.text if cell else ref.value
                            emit(f"{key}[{i}].{sub}", cell, interpreted(cell, ref.value))
                if any(v not in (None, "") for v in record.values()):
                    data[key].append(record)
    return data, facts


# --- outcome -------------------------------------------------------------------------------


def finish(
    item: DocInput,
    result: LlmResult[BaseModel] | None,
    *,
    cheap: bool,
) -> ExtractionOutcome:
    method = "sheet" if item.is_sheet else "vision" if item.is_image else "text"
    if result is None or result.output is None:
        error = result.error if result else "No extraction was attempted"
        status = ExtractionStatus.NEEDS_ESCALATION if cheap else ExtractionStatus.FAILED
        return ExtractionOutcome(
            status,
            method,
            None,
            [],
            [],
            [f"The model could not read this document: {error}"],
            error,
        )

    data, facts = (
        resolve_sheet(item, result.output) if item.is_sheet else resolve_pdf(item, result.output)
    )
    checks = run_checks(item.doc.doc_type, data)
    failed = [c for c in checks if not c.passed]
    reasons = [c.detail for c in failed]

    text_facts = [f for f in facts if f.method == "text"]
    if text_facts:
        rate = sum(1 for f in text_facts if f.grounding in ("exact", "fuzzy")) / len(text_facts)
        if rate < MIN_GROUNDING_RATE:
            reasons.append(f"Only {rate:.0%} of values were found on the page as read")
    vision = [f for f in facts if f.method == "vision"]
    confirmed = ocr_confirmation(vision)
    # Without totals to reconcile, OCR agreement is the only independent check on an image.
    if confirmed is not None and not checks and confirmed < MIN_OCR_CONFIRMATION:
        reasons.append(f"OCR confirmed only {confirmed:.0%} of the values read from the image")

    _assign_confidence(item, facts, checks)
    if reasons:
        status = ExtractionStatus.NEEDS_ESCALATION if cheap else ExtractionStatus.NEEDS_REVIEW
    else:
        status = ExtractionStatus.OK
    return ExtractionOutcome(status, method, data, facts, checks, reasons)


def _assign_confidence(item: DocInput, facts: list[FactDraft], checks: list[Check]) -> None:
    failed_paths = {p for c in checks if not c.passed for p in c.paths}
    reconciled = bool(checks) and all(c.passed for c in checks)
    quality = {p.page.id: p.page.quality for p in item.pages}

    for f in facts:
        if f.method == "sheet":
            f.confidence = ConfidenceBand.HIGH
            f.reasons.append(f"Read directly from cell {f.cell_ref}")
        elif f.method == "text":
            if f.grounding == "exact":
                f.confidence = ConfidenceBand.HIGH
                f.reasons.append(f"Found exactly as read on page {f.page_no}")
            elif f.grounding == "fuzzy":
                f.confidence = ConfidenceBand.MEDIUM
                f.reasons.append(f"Found on page {f.page_no} with small differences")
            else:
                f.confidence = ConfidenceBand.LOW
                f.reasons.append("Not found on the page as read; check the source")
        elif f.grounding == "ocr_exact":
            f.confidence = ConfidenceBand.HIGH
            f.reasons.append("Read from the image and confirmed by OCR")
        elif f.grounding == "ocr_fuzzy":
            f.confidence = ConfidenceBand.MEDIUM
            f.reasons.append("Read from the image; OCR shows it with small differences")
        else:
            f.confidence = ConfidenceBand.MEDIUM
            f.reasons.append("Read from the image; OCR could not confirm it")
            if reconciled and _numeric_path(f.path):
                f.confidence = ConfidenceBand.HIGH
                f.reasons.append("Numbers reconcile with the totals printed on the document")
        if f.path in failed_paths or any(f.path.startswith(p + ".") for p in failed_paths):
            f.confidence = ConfidenceBand.LOW
            f.reasons.append("Part of a total that does not add up")
        q = quality.get(f.page_id or "")
        if q is QualityBand.POOR:
            f.confidence = ConfidenceBand.LOW
            f.reasons.append("Page image is poor quality")
        elif (
            q is QualityBand.DEGRADED
            and f.confidence is ConfidenceBand.HIGH
            and f.method == "vision"
        ):
            f.confidence = ConfidenceBand.MEDIUM
            f.reasons.append("Page image is soft or tilted")


def _numeric_path(path: str) -> bool:
    key = path.rsplit(".", 1)[-1].split("[")[0]
    return bool(_MONEY_KEYS.search(key) or _NUMBER_KEYS.search(key))


def escalation_cost(result: LlmResult[BaseModel] | None, settings: Settings) -> float:
    """What re-reading with the escalation model would cost, from the cheap call's token use."""
    prices = settings.llm_prices.get(settings.llm_escalation_model)
    if prices is None:
        return 0.0
    tokens_in = result.call.input_tokens if result and result.call.input_tokens else 8000
    tokens_out = result.call.output_tokens if result and result.call.output_tokens else 4000
    return round((tokens_in * prices[0] + tokens_out * prices[1]) / 1_000_000, 4)
