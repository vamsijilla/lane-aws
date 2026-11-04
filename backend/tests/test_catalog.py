"""The field catalog is the single source of truth for extraction: every attribute a rule or a
conflict check reads has an entry, and every entry says who uses it."""

import re

from lane.extract import catalog as c
from lane.rules.service import ruleset
from tests.sample import rules, view_of

# Attributes the resolver derives from a catalog table rather than reading one field: each yes or
# no question of the application or quote form becomes operations.q_<topic>.
DERIVED = {r"operations\.q_[a-z_]+": "operations.questions"}


def _catalog_attributes() -> set[str]:
    return {
        f"{e.spec.entity}.{e.spec.attribute}" for dt in c.catalog().doc_types for e in c.entries(dt)
    }


def test_every_attribute_a_rule_reads_is_in_the_catalog() -> None:
    by_id, _, board = rules()
    view, _ = view_of(board)
    names = {f.id: f"{e.kind}.{a}" for e in view.entities for a, f in e._fields.items()}  # pyright: ignore[reportPrivateUsage]
    reads = {names[i] for r in by_id.values() for i in r.inputs if i in names}
    have = _catalog_attributes()
    for attr in reads:
        derived = next((src for pat, src in DERIVED.items() if re.fullmatch(pat, attr)), None)
        assert attr in have or derived in have, f"{attr} is read by a rule but not in the catalog"


def test_every_catalog_entry_has_a_user_and_rule_ids_exist() -> None:
    rule_ids = {r.id for r in ruleset().rules}
    for dt in c.catalog().doc_types:
        for e in c.entries(dt):
            assert e.spec.used_by, f"{dt}.{e.path} has no used_by"
            for u in e.spec.used_by:
                if re.fullmatch(r"[A-Z]+-\d{2}", u):
                    assert u in rule_ids, f"{dt}.{e.path}: unknown rule {u}"


def test_schemas_and_prompts_are_generated_for_every_type() -> None:
    for dt, doc in c.catalog().doc_types.items():
        for shape in doc.shapes:
            model = c.pdf_schema(dt) if shape == "pdf" else c.sheet_schema(dt)
            assert model is not None, (dt, shape)
            listed = c.prompt_fields(dt, shape)
            for e in c.entries(dt, shape):
                if e.spec.derived:
                    continue  # set by code, never asked of the model
                leaf = e.path.rsplit(".", 1)[-1].removesuffix("[]")
                assert leaf in listed, (dt, shape, e.path)


def test_only_catalog_paths_become_facts() -> None:
    assert c.entry_for("ifta_return", "rows[3].total_miles") is not None
    tax = c.entry_for("ifta_return", "rows[3].tax")
    assert tax is not None and not tax.is_fact  # a check input, never a fact
    assert c.entry_for("ifta_return", "rows[3].mpg") is None  # not extracted at all
    assert c.entry_for("drivers_license", "eyes") is None
