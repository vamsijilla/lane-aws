"""Gold set format (evals/gold/<name>.yaml). Packet specifics live in the YAML, never here."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

EVALS_DIR = Path(__file__).resolve().parents[4] / "evals"


class GoldDocument(BaseModel):
    file: str
    types: list[str]


class GoldSupersession(BaseModel):
    file: str
    type: str
    subject_contains: str
    superseded: int


class GoldField(BaseModel):
    entity: str
    attribute: str | None = None
    detail: str | None = None  # a value in the entity's details (IFTA totals) instead of a field
    match: str | None = None  # substring of the entity key or label
    values: list[str] = Field(default_factory=lambda: list[str]())
    state: str | None = None  # expect this state (conflict, invalid) instead of a value


class GoldConflict(BaseModel):
    id: str
    label: str
    or_missing: str | None = None


class GoldMissing(BaseModel):
    id: str
    codes: list[str] = Field(default_factory=lambda: list[str]())
    rules: list[str] = Field(default_factory=lambda: list[str]())


class GoldRoute(BaseModel):
    outcome: str
    team: str | None
    potential_decline: bool
    potential_decline_rule: str | None = None


class GoldTopic(BaseModel):
    id: str
    topic: str
    all: list[list[str]]


class Gold(BaseModel):
    name: str
    email_subject_contains: str
    documents: list[GoldDocument]
    supersessions: list[GoldSupersession] = Field(default_factory=lambda: list[GoldSupersession]())
    fields: list[GoldField]
    conflicts: list[GoldConflict]
    conflicts_also_valid: list[str] = Field(default_factory=lambda: list[str]())
    missing: list[GoldMissing]
    rules: dict[str, list[str]]
    route: GoldRoute
    findings: list[GoldTopic]

    @classmethod
    def load(cls, name: str, directory: Path = EVALS_DIR / "gold") -> "Gold":
        return cls.model_validate(yaml.safe_load((directory / f"{name}.yaml").read_text()))
