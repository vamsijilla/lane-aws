"""Same inputs, same output: no list a person reads or a hash covers may depend on the order rows
came back from the database or on set iteration."""

import hashlib
import json
import random
from dataclasses import asdict
from typing import Any, cast

from lane.domain.order import natural_key, stable_items
from lane.rules.view import SubmissionView
from tests.test_rules import Conflict, ViewBuilder, clean_submission, driver, results_of, term


def busy_view(seed: int | None = None) -> tuple[SubmissionView, dict[str, str]]:
    """A submission with several drivers, terms and conflicts, built in a shuffled order."""
    b = ViewBuilder()
    clean_submission(b, owner_drives=Conflict(False, True), units=Conflict(4, 5))
    driver(b, "morales", dob=Conflict("1991-10-03", "1991-10-30"))
    driver(b, "whitfield", mvrs=[])
    driver(b, "osei", cdl=False)
    for line in ("cargo", "auto_liability"):
        term(b, "2021-12-15", "2022-12-15", line=line)
    conflicts = {
        "operations.operations.owner_drives": "Owner drives",
        "fleet.fleet.power_units": "Power units",
        "driver.morales.date_of_birth": "Date of birth",
    }
    if seed is not None:
        shuffle_view(b.view, seed)
        conflicts = dict(random.Random(seed).sample(list(conflicts.items()), len(conflicts)))
    return b.view, conflicts


def _shuffled(node: Any, rng: random.Random) -> Any:
    """Every list and every dict inside, in a random order."""
    if isinstance(node, dict):
        items = [(k, _shuffled(v, rng)) for k, v in cast(dict[str, Any], node).items()]
        rng.shuffle(items)
        return dict(items)
    if isinstance(node, list):
        out = [_shuffled(x, rng) for x in cast(list[Any], node)]
        rng.shuffle(out)
        return out
    return node


def shuffle_view(view: SubmissionView, seed: int) -> None:
    """The entities, their fields, candidates, sources and every list in their details."""
    rng = random.Random(seed)
    rng.shuffle(view.entities)
    for e in view.entities:
        e.details = _shuffled(e.details, rng)
        e._fields = dict(_shuffled(list(e._fields.items()), rng))  # pyright: ignore[reportPrivateUsage]
    for f in view.fields_by_id.values():
        f.candidates = _shuffled(f.candidates, rng)


def output_hash(view: SubmissionView, conflicts: dict[str, str]) -> str:
    by_id, decision = results_of(view, conflicts)
    rules: list[Any] = [
        [
            rid,
            r.outcome.value,
            r.verdict.reason,
            r.verdict.items,
            r.inputs,
            r.sensitivity,
            r.blocking_conflicts,
            r.anchor_note,
            r.readings,
        ]
        for rid, r in by_id.items()
    ]
    blob = json.dumps({"rules": rules, "decision": asdict(decision)}, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def test_two_runs_on_the_same_packet_hash_the_same() -> None:
    assert output_hash(*busy_view()) == output_hash(*busy_view())


def test_the_order_rows_arrive_in_does_not_change_the_output() -> None:
    expected = output_hash(*busy_view())
    for seed in range(8):
        assert output_hash(*busy_view(seed)) == expected, f"seed {seed}"


def test_the_sample_rules_read_the_same_however_their_inputs_are_ordered() -> None:
    """The real submission: every rule's reason, items, sensitivity, readings and notes."""
    from tests.sample import board, view_of

    view, conflicts = view_of(board())
    expected = output_hash(view, conflicts)
    for seed in range(6):
        view, conflicts = view_of(board())
        shuffle_view(view, seed)
        assert output_hash(view, conflicts) == expected, f"seed {seed}"


def test_natural_order() -> None:
    assert stable_items(["Unit 10", "unit 2", "Unit 2", "Cargo 2024-2025", "Cargo 2023-2024"]) == [
        "Cargo 2023-2024",
        "Cargo 2024-2025",
        "unit 2",
        "Unit 2",
        "Unit 10",
    ]
    assert natural_key("a2") < natural_key("a10")
