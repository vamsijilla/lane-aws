"""A conflict's source shows how well its value was read: the weakest band among its facts, with
that band's reasons. A derived value, read from no fact, has no band."""

from lane.api.models import candidates


def _raw(*fact_ids: str) -> list[dict[str, object]]:
    source = {"document_id": "d", "doc_type": "application", "title": "Application"}
    return [{"display": "Yes", "value": True, "sources": [{**source, "fact_ids": list(fact_ids)}]}]


def test_a_source_takes_its_weakest_fact_and_that_facts_reasons() -> None:
    bands = {
        "f1": ("high", ["Read directly from cell L58"]),
        "f2": ("medium", ["Found on page 2 with small differences"]),
    }
    (source,) = candidates(_raw("f1", "f2"), confidence=bands)[0].sources
    assert source.confidence == "medium"
    assert source.confidence_reasons == ["Found on page 2 with small differences"]


def test_a_derived_value_has_no_band() -> None:
    (source,) = candidates(_raw(), confidence={"f1": ("high", ["x"])})[0].sources
    assert source.confidence is None
    assert source.confidence_reasons == []
