"""Extraction v2: decision-driven, verified extraction (EXTRACT_V2).

What is read comes from the field catalog (config/field_catalog.yaml); nothing else becomes a
fact. Every value is read twice where it decides something:

  - worksheet: the model points at a cell and code reads it; the cell is the second read;
  - text layer: code finds the value's label (or its record's row, or its column) and reads the
    value there, then compares by type with what the model read;
  - scan or photo: the model reads the image; code reads OCR at the same anchor and compares.

A decision-tier value that does not verify, a validator that fails on a single read, or a recall
check that finds rows the model did not return re-reads the document with the stronger model,
without asking. If the stronger read still does not verify, the document needs review and both
reads are kept side by side. Crosscheck and context values need one read and grounding.
"""

import hashlib
import re
from dataclasses import replace
from datetime import date
from typing import Any, cast

from pydantic import BaseModel

from lane.config import get_settings
from lane.db.models import ConfidenceBand, ExtractionStatus, PageKind, QualityBand
from lane.domain import normalize as nz
from lane.extract import catalog as cat
from lane.extract.checks import Check, run_checks
from lane.extract.engine import (
    DocInput,
    DocPage,
    ExtractionOutcome,
    FactDraft,
    _walk,  # pyright: ignore[reportPrivateUsage]
    normalize_value,
    resolve_sheet,
)
from lane.extract.ocr_check import OcrCheck, Spot, gate, reread
from lane.extract.validators import recall_checks, run_validators
from lane.extract.verify import Anchor, fold, locate, next_quote, record_value, want_for
from lane.extract.verify import Page as VPage
from lane.llm.gateway import ImagePart, LlmRequest, LlmResult, Part, TextPart
from lane.llm.prompts import Prompt, load_prompt

EXTRACTOR_VERSION = "2"
PLACEHOLDER = re.compile(r"[-\u2013\u2014.*]+|n/?a|none|nil", re.IGNORECASE)
BASE_PROMPT = "v2_extract_document"
# A role printed where a date belongs ("OWNER" in a hire date column)
ROLE_IN_DATE = re.compile(r"owner(?:[\s/-]*operator)?|o/o", re.IGNORECASE)
CLEANERS = {"legal_presence": nz.drop_presence_markers}


# --- requests ------------------------------------------------------------------------------


def schema_for(doc_type: str, is_sheet: bool) -> type[BaseModel] | None:
    return cat.sheet_schema(doc_type) if is_sheet else cat.pdf_schema(doc_type)


def extractable(doc_type: str, is_sheet: bool) -> bool:
    return schema_for(doc_type, is_sheet) is not None


def prompt_for(doc_type: str, is_sheet: bool) -> Prompt:
    """Base prompt, the type's own guidance, and the field list generated from the catalog."""
    shape: cat.Shape = "sheet" if is_sheet else "pdf"
    base = load_prompt(BASE_PROMPT)
    specific = load_prompt(cat.prompt_name(doc_type, shape))
    fields = cat.prompt_fields(doc_type, shape)
    text = f"{base.text}\n\n{specific.text}\n\n{fields}"
    return Prompt(
        name=specific.name,
        version=f"{base.version}.{specific.version}.c{cat.catalog().tag_for(doc_type)}",
        text=text,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
    )


def cache_salt(item: DocInput) -> str:
    """Document, catalog and extractor: a change to any of them is a new read."""
    blob = item.doc.attachment.blob_sha256 if item.doc.attachment else None
    return (
        f"doc {blob}:{item.doc.page_from}-{item.doc.page_to} "
        f"catalog {cat.catalog().tag_for(item.doc.doc_type)} extractor {EXTRACTOR_VERSION}"
    )


def build_request(item: DocInput, *, model: str, effort: str) -> LlmRequest[BaseModel]:
    doc = item.doc
    schema = schema_for(doc.doc_type, item.is_sheet)
    assert schema is not None
    parts: list[Part] = [
        TextPart(
            f"File: {doc.attachment.filename} (name is an untrusted hint)\n"
            f"Document type: {doc.doc_type.replace('_', ' ')}\n"
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
        prompt=prompt_for(doc.doc_type, item.is_sheet),
        parts=parts,
        output_type=schema,
        max_tokens=16000,
        cache_salt=cache_salt(item),
    )


# --- the second read -----------------------------------------------------------------------


def _pages(item: DocInput) -> dict[int, VPage]:
    out: dict[int, VPage] = {}
    for p in item.pages:
        if p.page.kind is PageKind.EMAIL_BODY:
            out[p.page.page_no] = VPage.from_text(p.text)
            continue
        vp = VPage.from_layout(p.layout, ocr=p.page.kind in (PageKind.SCAN, PageKind.PHOTO))
        if vp is not None:
            out[p.page.page_no] = vp
    return out


def _sentence(anchor: Anchor, *, ocr: bool, value: str) -> str:
    """How the value was verified, in words, for the evidence lens."""
    who = "model and OCR" if ocr else "model and code"
    if anchor.mismatch:
        return f"Not confirmed: code read {anchor.read!r} beside '{anchor.label}', the model read {value!r}"
    if anchor.unsure:
        return (
            f"Unreadable at that spot: OCR's confidence there is {anchor.score:.2f}, under the "
            "threshold; one read"
        )
    match anchor.kind:
        case "label":
            where = {
                "same line": "on the same line",
                "below": "below it",
                "before": "before it",
                "split": "across its labels",
            }.get(anchor.where or "", "")
            return f"Read twice: {who} agree. Label '{anchor.label}' {where}".strip()
        case "row":
            return f"Read twice: {who} agree, in its record's row"
        case "column":
            return f"Read twice: {who} agree, under its column" + (
                f" '{anchor.label}'" if anchor.label else ""
            )
        case "line":
            return f"Read twice: {who} agree, printed as its own line"
        case "statement":
            return f"Read twice: {who} agree, printed verbatim"
        case "page":
            return "Found on the page, not beside its label: one read"
        case _:
            return "Not found on the page: one read"


def _ocr_cfg() -> OcrCheck:
    s = get_settings()
    return OcrCheck(s.triage_ocr_min_word_score, s.triage_ocr_models, s.triage_ocr_reread_scale)


def _spot(p: DocPage) -> Spot | None:
    width: Any = (p.layout or {}).get("width")
    return Spot(p.image_png, float(width)) if p.image_png and width else None


def _pdf_facts(item: DocInput, data: dict[str, Any]) -> list[FactDraft]:
    pages = _pages(item)
    cfg = _ocr_cfg()
    by_no = {p.page.page_no: p for p in item.pages}
    facts: list[FactDraft] = []
    for path, raw, page_no, quote in _walk(data, "", None, None):
        e = cat.entry_for(item.doc.doc_type, path)
        if e is None or not e.is_fact:
            continue  # not in the catalog: stays page text, never a fact
        if PLACEHOLDER.fullmatch(raw.strip()):
            continue  # "-" or "N/A" printed in a cell: nothing stated, not a value
        want = want_for(e.spec, raw)
        table = "[" in path
        header = record_value(data, path, cat.column_key(item.doc.doc_type, path))
        order = ([page_no] if page_no in pages else []) + [n for n in pages if n != page_no]
        best: tuple[Anchor, int] | None = None
        for n in order:
            got = locate(
                pages[n],
                want,
                row_quote=quote if table else None,
                next_quote=next_quote(data, path) if table else None,
                column=header,
                quote=quote,
            )
            if got.verified:
                best = (got, n)
                break
            if best is None and (got.mismatch or got.kind == "page"):
                best = (got, n)
        anchor, at = (
            best
            if best
            else (Anchor("none"), page_no if page_no in by_no else next(iter(by_no), 0))
        )
        source = by_no.get(at) or item.pages[0]
        ocr = source.page.kind in (PageKind.SCAN, PageKind.PHOTO)
        if ocr and at in pages:
            anchor = gate(pages[at], anchor, want, cfg)
            spot = _spot(source)
            if e.tier == "decision" and not anchor.verified and spot is not None:
                anchor = reread(
                    pages[at],
                    anchor,
                    want,
                    spot,
                    cfg,
                    quote=quote,
                    row_quote=quote if table else None,
                    next_quote=next_quote(data, path) if table else None,
                    column=header,
                )
        grounding = ("ocr_" if ocr and anchor.kind != "none" else "") + anchor.kind
        if anchor.mismatch:
            grounding = ("ocr_" if ocr else "") + "mismatch"
        elif anchor.unsure:
            grounding = "ocr_unsure"
        facts.append(
            FactDraft(
                path=path,
                raw=raw,
                norm=normalize_value(path, raw),
                page_id=source.page.id,
                page_no=source.page.page_no,
                quote=quote,
                method="vision" if ocr else "text",
                grounding=grounding,
                bbox=anchor.bbox,
                tier=e.tier,
                verified=anchor.verified,
                verification=_sentence(anchor, ocr=ocr, value=raw)
                + (
                    f"; OCR confidence {anchor.score:.2f}"
                    if anchor.score is not None and not anchor.unsure
                    else ""
                ),
                second_read=anchor.read if (anchor.mismatch or anchor.verified) else None,
                reasons=[
                    r
                    for r in (
                        f"Look-alikes folded: {anchor.folding}" if anchor.folding else None,
                        "Re-read at anchor: OCR read its spot again, enlarged"
                        if anchor.reread
                        else None,
                        "Day and month could be read either way" if anchor.ambiguous else None,
                    )
                    if r
                ],
            )
        )
    return facts


def _leaf_text(node: Any) -> str | None:
    if isinstance(node, dict):
        v = cast(dict[str, Any], node).get("value")
        return v if isinstance(v, str) else None
    return node if isinstance(node, str) else None


def _set_text(node: Any, text: str | None) -> Any:
    if isinstance(node, dict):
        return {**cast(dict[str, Any], node), "value": text}
    return text


def tidy(doc_type: str, data: dict[str, Any]) -> list[str]:
    """Code's corrections to the model's answer, before anything becomes a fact:
    - a role printed in a date column of a record becomes the record's role, the date empty;
    - catalog cleaners run (a license type loses its legal-presence designation, value and quote).
    Returns one note per change."""
    notes: list[str] = []

    def visit(node: Any, path: str) -> None:
        if isinstance(node, list):
            for i, item in enumerate(cast(list[Any], node)):
                visit(item, f"{path}[{i}]")
            return
        if not isinstance(node, dict):
            return
        row = cast(dict[str, Any], node)
        for key in list(row):
            if key in ("page", "quote"):
                continue
            child = f"{path}.{key}" if path else key
            e = cat.entry_for(doc_type, child)
            if e is None:
                visit(row[key], child)
                continue
            raw = _leaf_text(row[key])
            if raw is None:
                continue
            role = f"{path}.role"
            if (
                e.spec.type == "date"
                and "[" in path
                and ROLE_IN_DATE.fullmatch(raw.strip())
                and cat.entry_for(doc_type, role) is not None
            ):
                row["role"] = row[key]
                row[key] = _set_text(row[key], None)
                notes.append(f"{child}: '{raw}' is a role, not a date")
                continue
            for name in e.spec.clean:
                cleaned = CLEANERS[name](raw)
                if cleaned != raw:
                    row[key] = _set_text(row[key], cleaned)
                    if isinstance(row[key], dict) and isinstance(row[key].get("quote"), str):
                        row[key]["quote"] = CLEANERS[name](row[key]["quote"])
                    notes.append(f"{child}: {name} removed")

    visit(data, "")
    return notes


def _sheet_roles(doc_type: str, facts: list[FactDraft]) -> list[FactDraft]:
    """A sheet cell that prints a role in a date column: the fact is the record's role."""
    out: list[FactDraft] = []
    for f in facts:
        e = cat.entry_for(doc_type, f.path)
        role = f.path.rsplit(".", 1)[0] + ".role"
        r = cat.entry_for(doc_type, role)
        if (
            e is not None
            and r is not None
            and e.spec.type == "date"
            and f.raw
            and ROLE_IN_DATE.fullmatch(f.raw.strip())
        ):
            f = replace(f, path=role, norm=f.raw, tier=r.tier)
        out.append(f)
    return out


def _sheet_facts(item: DocInput, output: BaseModel) -> tuple[dict[str, Any], list[FactDraft]]:
    data, drafts = resolve_sheet(item, output, trust_cells=True)
    facts: list[FactDraft] = []
    for f in drafts:
        e = cat.entry_for(item.doc.doc_type, f.path)
        if e is None or not e.is_fact:
            continue
        if f.raw is not None and PLACEHOLDER.fullmatch(re.sub(r"[$\s]", "", f.raw) or "-"):
            continue  # "$ -" in a cell: nothing stated
        facts.append(
            replace(
                f,
                tier=e.tier,
                verified=True,
                verification=f"Cell {f.cell_ref}: the model pointed at the cell and code read it",
                second_read=f.raw,
            )
        )
    return data, facts


def _sheet_rows(item: DocInput, data: dict[str, Any], output: BaseModel) -> dict[str, int]:
    """Filled rows under each mapped table's header, for the recall check: from its first data
    row down to the first empty row, counting rows with a value in one of its mapped columns.
    Keyed by the table's name in the extracted data."""
    from lane.extract import schemas as s
    from lane.extract.sheets import Sheet

    sheets = {p.page.sheet_name: Sheet.from_grid(p.layout or {}, p.page.id) for p in item.pages}
    out: dict[str, int] = {}
    for key, cols in output:
        if not isinstance(cols, s.ColumnMap) or cols.first_data_row is None:
            continue
        sheet = sheets.get(cols.sheet) or next(iter(sheets.values()), None)
        if sheet is None:
            continue
        letters = {
            v
            for k, v in cols.model_dump().items()
            if k not in {"sheet", "header_row", "first_data_row", "last_data_row"}
            and isinstance(v, str)
            and v
        }
        count, r = 0, cols.first_data_row
        while any(str(sheet.cells.get(f"{c}{r}", {}).get("v") or "").strip() for c in letters):
            count += 1
            r += 1
        out["rows" if key == "table" else key] = count
    return out


# --- outcome -------------------------------------------------------------------------------


def _confidence(
    f: FactDraft, quality: dict[str, QualityBand], failed: dict[str, str], recall_failed: set[str]
) -> None:
    """A band with its reasons, from the signals. High needs a verified read for decision
    fields; a failed validator, an incomplete table or a poor page caps it."""
    reasons = list(f.reasons)
    if f.verified:
        band = ConfidenceBand.HIGH
    elif f.grounding.endswith("page"):
        band = ConfidenceBand.MEDIUM if f.tier != "decision" else ConfidenceBand.LOW
    else:
        band = ConfidenceBand.LOW
    if f.verification:
        reasons.insert(0, f.verification)
    if f.path in failed:
        band = min(band, ConfidenceBand.MEDIUM if f.verified else ConfidenceBand.LOW, key=_rank)
        reasons.append(
            f"Check failed: {failed[f.path]}" + (" (the document says so)" if f.verified else "")
        )
    table = f.path.split("[", 1)[0]
    if table in recall_failed:
        band = min(band, ConfidenceBand.MEDIUM, key=_rank)
        reasons.append("Some rows of this table may not have been read")
    if f.page_id and quality.get(f.page_id) is QualityBand.POOR:
        band = min(band, ConfidenceBand.MEDIUM, key=_rank)
        reasons.append("The page image is poor quality")
    if any(r.startswith("Day and month") for r in reasons):
        band = min(band, ConfidenceBand.MEDIUM, key=_rank)
    f.confidence, f.reasons = band, reasons


def _rank(b: ConfidenceBand) -> int:
    return [ConfidenceBand.LOW, ConfidenceBand.MEDIUM, ConfidenceBand.HIGH].index(b)


def _coverage(
    doc_type: str, is_sheet: bool, data: dict[str, Any], facts: list[FactDraft]
) -> dict[str, Any]:
    """Expected fields from the catalog: how many were found, verified, and which are missing."""
    shape: cat.Shape = "sheet" if is_sheet else "pdf"
    expected = [e for e in cat.entries(doc_type, shape) if e.is_fact]
    found_paths = {_pattern(f.path) for f in facts}
    verified_paths = {_pattern(f.path) for f in facts if f.verified}
    missing = [e.path for e in expected if e.path not in found_paths and "[]" not in e.path]
    decision = [e.path for e in expected if e.tier == "decision"]
    return {
        "expected": len(expected),
        "found": len([e for e in expected if e.path in found_paths]),
        "verified": len([e for e in expected if e.path in verified_paths]),
        "missing": missing,
        "decision_expected": len(decision),
        "decision_unverified": sorted(
            {_pattern(f.path) for f in facts if f.tier == "decision" and not f.verified}
        ),
    }


def _pattern(path: str) -> str:
    import re

    return re.sub(r"\[\d+\]", "[]", path)


def finish(
    item: DocInput, result: LlmResult[BaseModel] | None, *, strong: bool, today: date
) -> ExtractionOutcome:
    method = "sheet" if item.is_sheet else "vision" if item.is_image else "text"
    if result is None or result.output is None:
        error = result.error if result else "No extraction was attempted"
        status = ExtractionStatus.FAILED if strong else ExtractionStatus.NEEDS_ESCALATION
        return ExtractionOutcome(
            status,
            method,
            None,
            [],
            [],
            [f"The model could not read this document: {error}"],
            error,
        )
    if item.is_sheet:
        data, facts = _sheet_facts(item, result.output)
        tidy(item.doc.doc_type, data)
        facts = _sheet_roles(item.doc.doc_type, facts)
        sheet_rows = _sheet_rows(item, data, result.output)
    else:
        data = result.output.model_dump(mode="json")
        tidy(item.doc.doc_type, data)
        facts = _pdf_facts(item, data)
        sheet_rows = {}
    text = "\n".join(vp.text() for vp in _pages(item).values())
    checks: list[Check] = [
        *run_checks(item.doc.doc_type, data),
        *run_validators(item.doc.doc_type, data, today),
        *recall_checks(item.doc.doc_type, data, text, sheet_rows=sheet_rows),
    ]
    by_path = {f.path: f for f in facts}
    auto: list[str] = []
    review: list[str] = []
    failed_values: dict[str, str] = {}
    recall_failed: set[str] = set()
    for c in checks:
        if c.passed:
            continue
        if c.name.startswith("recall:"):
            auto.append(c.detail)
            recall_failed |= {p.split("[", 1)[0] for p in c.paths}
        elif c.name.startswith("validator:"):
            for p in c.paths:
                failed_values[p] = c.detail
                f = by_path.get(p)
                if f is not None and not f.verified and f.tier == "decision":
                    auto.append(f"{c.detail} (read once)")
        else:
            review.append(c.detail)  # arithmetic: escalates with a person's approval
    for f in facts:
        if f.tier == "decision" and not f.verified:
            what = (
                f"code read {f.second_read!r}"
                if f.grounding.endswith("mismatch")
                else "unreadable at that spot"
                if f.grounding == "ocr_unsure"
                else "no second read"
            )
            auto.append(f"{f.path} = {f.raw!r} is not verified ({what})")
    quality = {p.page.id: p.page.quality for p in item.pages}
    for f in facts:
        _confidence(f, quality, failed_values, recall_failed)
    if auto or review:
        status = ExtractionStatus.NEEDS_REVIEW if strong else ExtractionStatus.NEEDS_ESCALATION
    else:
        status = ExtractionStatus.OK
    return ExtractionOutcome(
        status,
        method,
        data,
        facts,
        checks,
        [*auto, *review],
        auto_escalate=[] if strong else auto,
        coverage=_coverage(item.doc.doc_type, item.is_sheet, data, facts),
    )


def keep_both_reads(
    item: DocInput,
    first: ExtractionOutcome,
    second: ExtractionOutcome,
    *,
    first_model: str,
    second_model: str,
) -> None:
    """After an escalation, each fact keeps what both models read, side by side.

    On an image, code's second read is OCR at the field's anchor; a poor photo can leave it with
    nothing to read. There the stronger model's independent read is the second read: a decision
    value both models read the same verifies (medium confidence), unless code read a different
    value at the anchor. A text page always needs code's read."""
    earlier = {f.path: f.raw for f in first.facts}
    settled: set[str] = set()
    for f in second.facts:
        reads = [{"by": second_model, "value": f.raw}]
        if f.path in earlier:
            reads.insert(0, {"by": first_model, "value": earlier[f.path]})
        if f.second_read is not None:
            reads.append({"by": "code", "value": f.second_read})
        f.reads = reads
        agree = (
            f.path in earlier and bool(f.raw) and fold(earlier[f.path] or "") == fold(f.raw or "")
        )
        if (
            f.tier == "decision"
            and not f.verified
            and f.method == "vision"
            and not f.grounding.endswith("mismatch")
            and agree
        ):
            f.verified = True
            f.verification = (
                "Two model reads agree; code could not read this spot of the image to check it"
            )
            f.confidence = ConfidenceBand.MEDIUM  # two reads, not code's check: never high
            f.reasons = [f.verification, *f.reasons[1:]]
            settled.add(f.path)
    if settled:
        second.escalation_reasons = [
            r for r in second.escalation_reasons if r.split(" = ", 1)[0] not in settled
        ]
        if not second.escalation_reasons and second.status is ExtractionStatus.NEEDS_REVIEW:
            second.status = ExtractionStatus.OK
        if second.data is not None:
            second.coverage = _coverage(item.doc.doc_type, item.is_sheet, second.data, second.facts)
