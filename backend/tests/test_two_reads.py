"""On an image, two agreeing model reads are the second read when OCR cannot check the spot."""

from types import SimpleNamespace
from typing import Any, cast

from lane.db.models import ConfidenceBand, ExtractionStatus
from lane.extract.engine import DocInput, ExtractionOutcome, FactDraft
from lane.extract.engine_v2 import keep_both_reads


def _fact(raw: str, *, method: str = "vision", grounding: str = "none") -> FactDraft:
    return FactDraft(
        path="dot_number",
        raw=raw,
        norm=raw,
        page_id="p",
        page_no=1,
        quote=None,
        method=method,
        grounding=grounding,
        confidence=ConfidenceBand.LOW,
        reasons=["Not found on the page: one read"],
        tier="decision",
        verified=False,
        verification="Not found on the page: one read",
    )


def _outcome(f: FactDraft) -> ExtractionOutcome:
    return ExtractionOutcome(
        ExtractionStatus.NEEDS_REVIEW,
        f.method,
        {"dot_number": {"value": f.raw}},
        [f],
        [],
        [f"dot_number = {f.raw!r} is not verified (no second read)"],
    )


ITEM = cast(DocInput, SimpleNamespace(doc=SimpleNamespace(doc_type="quote_form"), is_sheet=False))


def _run(first: str, second: str, **kw: Any) -> ExtractionOutcome:
    a, b = _outcome(_fact(first, **kw)), _outcome(_fact(second, **kw))
    keep_both_reads(ITEM, a, b, first_model="cheap", second_model="strong")
    return b


def test_agreeing_reads_on_an_image_verify_at_medium_confidence() -> None:
    out = _run("3481107", "3481107")
    [f] = out.facts
    assert f.verified and f.confidence is ConfidenceBand.MEDIUM
    assert f.verification and f.verification.startswith("Two model reads agree")
    assert out.status is ExtractionStatus.OK and not out.escalation_reasons
    assert [r["by"] for r in f.reads or []] == ["cheap", "strong"]


def test_disagreeing_reads_stay_in_review() -> None:
    out = _run("3481107", "3481101")
    assert not out.facts[0].verified and out.status is ExtractionStatus.NEEDS_REVIEW


def test_a_text_page_always_needs_codes_read() -> None:
    out = _run("3481107", "3481107", method="text")
    assert not out.facts[0].verified and out.status is ExtractionStatus.NEEDS_REVIEW


def test_code_reading_a_different_value_keeps_it_in_review() -> None:
    out = _run("3481107", "3481107", grounding="ocr_mismatch")
    assert not out.facts[0].verified
