"""Rule outcomes, the ruleset file format, and loading it."""

import enum
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from lane.documents.types import DocType

RULES_DIR = Path(__file__).resolve().parents[3] / "rules"


class Outcome(enum.StrEnum):
    PASS = "pass"  # noqa: S105 (an outcome, not a password)
    FLAG = "flag"  # a note for the agent or underwriter; does not hold the submission
    REFER = "refer"  # an underwriter must review before quoting
    DECLINE = "decline"  # outside the program, on confirmed facts
    UNKNOWN = "unknown"  # cannot be evaluated from the documents
    NA = "n/a"


SEVERITY = [Outcome.NA, Outcome.PASS, Outcome.FLAG, Outcome.UNKNOWN, Outcome.REFER, Outcome.DECLINE]


class Reading(BaseModel):
    """Another defensible reading of the guideline line, evaluated alongside the primary one."""

    id: str
    label: str
    predicate: str
    assumption: str | None = None
    # A reading that is defensible but not plausible for this guideline line never makes a
    # potential decline (A03): when it would decline, the rule flags it with this question.
    plausible: bool = True
    question: str | None = None


class RuleSpec(BaseModel):
    id: str
    title: str
    section: str
    quote: str
    predicate: str
    # A short name of what it checks ("Driver CDLs"), for lists where a status sits beside it;
    # the title states the requirement and stays in the record
    name: str | None = None
    # What the rule checks, in one plain sentence for someone who has not read the guideline
    explain: str | None = None
    decline: bool = False  # the guideline makes a failure of this rule a decline
    # Needs data that never comes with a submission (A07): unknown at intake is a check for the
    # underwriter at quote, not a reason to refer a clean submission
    external: bool = False
    check: str | None = None  # short name of an external check, for the route sign
    # Document types that could hold what this rule looks for. If a source of one of these
    # could not be read, a refer that rests on its absence is unknown, not a finding.
    sources: list[str] = Field(default_factory=lambda: list[str]())
    request: str | None = None
    request_group: str = "before_quote"
    request_key: str | None = None  # rules with the same key become one agent request item
    request_details: bool = True  # include the rule's specific items under the request
    request_source: str | None = None  # "conflicts": details come from open conflicts
    covers: list[str] = Field(default_factory=lambda: list[str]())  # missing-item code prefixes
    # Missing items (code prefixes) this rule's findings point to. The missing item asks the
    # agent; a flag from this rule links to it instead of asking twice.
    missing: list[str] = Field(default_factory=lambda: list[str]())
    reading: str | None = None  # how the primary predicate reads the guideline line
    readings: list[Reading] = Field(default_factory=lambda: list[Reading]())
    assumptions: list[str] = Field(
        default_factory=lambda: list[str]()
    )  # ids in Ruleset.assumptions


class Ruleset(BaseModel):
    ruleset: str
    version: str
    guideline: str
    params: dict[str, Any]
    teams: dict[str, str]
    request_groups: dict[str, str] = Field(default_factory=lambda: dict[str, str]())
    assumptions: dict[str, str] = Field(default_factory=lambda: dict[str, str]())
    rules: list[RuleSpec] = Field(min_length=1)
    sha256: str = ""

    @classmethod
    def load(cls, name: str = "knight_trucking", directory: Path = RULES_DIR) -> "Ruleset":
        files = sorted(directory.glob(f"{name}.v*.yaml"))
        if not files:
            raise FileNotFoundError(f"No ruleset named {name!r} in {directory}")
        raw = files[-1].read_bytes()
        loaded = cls.model_validate(yaml.safe_load(raw))
        loaded.sha256 = hashlib.sha256(raw).hexdigest()
        ids = [r.id for r in loaded.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("Rule ids must be unique")
        cited = {a for r in loaded.rules for a in r.assumptions} | {
            rd.assumption for r in loaded.rules for rd in r.readings if rd.assumption
        }
        unknown = sorted(cited - set(loaded.assumptions))
        if unknown:
            raise ValueError(f"Rules cite assumptions that are not defined: {', '.join(unknown)}")
        types = {t.value for t in DocType}
        bad = sorted({s for r in loaded.rules for s in r.sources} - types)
        if bad:
            raise ValueError(f"Rules name document types that do not exist: {', '.join(bad)}")
        return loaded


@dataclass
class Verdict:
    """What a predicate decides. `items` are specifics for the agent request."""

    outcome: Outcome
    reason: str
    items: list[str] = field(default_factory=lambda: list[str]())
    evidence: list[str] = field(default_factory=lambda: list[str]())  # fact ids
    # The one missing fact this rule waits on, as (key, label): rules that share a key wait on
    # the same answer, so a person sees one question, not one per rule
    waiting_on: list[tuple[str, str]] = field(default_factory=lambda: list[tuple[str, str]]())


@dataclass
class RuleResult:
    spec: RuleSpec
    verdict: Verdict
    inputs: list[str]  # resolved field ids the rule read
    potential_decline: bool = False
    blocking_conflicts: list[str] = field(default_factory=lambda: list[str]())  # field ids
    sensitivity: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    anchor_note: str | None = None
    readings: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    waiting_on: list[dict[str, str]] = field(default_factory=lambda: list[dict[str, str]]())
    evidence: list[str] = field(default_factory=lambda: list[str]())  # facts behind details read
    absent: list[str] = field(default_factory=lambda: list[str]())  # "Applicant: FEIN"
    # What the other date basis would ask for, when only it refers ("IFTA return for Q3 2026")
    anchor_items: list[str] = field(default_factory=lambda: list[str]())

    @property
    def outcome(self) -> Outcome:
        return self.verdict.outcome
