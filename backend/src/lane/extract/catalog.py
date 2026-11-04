"""The field catalog (config/field_catalog.yaml): what Lane extracts, and why.

The catalog is the single source of truth. From it this module generates:
  - the extraction schema for each document type and shape (a PDF or image read by the model,
    or a worksheet answered with cell references),
  - the field list in each prompt, with the labels each value is printed beside,
  - the lookup from an extracted path ("claims[2].total_paid") to its catalog entry, which
    decides whether the value becomes a fact, its tier, its type and how it is checked.

Nothing outside the catalog becomes a fact. Generated field names match what the resolver reads,
so adding a document type is catalog entries plus one prompt.
"""

import hashlib
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from lane.extract import schemas as s

CATALOG_PATH = Path(__file__).resolve().parents[4] / "config" / "field_catalog.yaml"

FieldType = Literal["identifier", "number", "money", "date", "name", "enum", "text", "table"]
Tier = Literal["decision", "crosscheck", "context"]
Role = Literal["fact", "check", "display"]
Shape = Literal["pdf", "sheet"]
_USED_BY = re.compile(
    r"^([A-Z]+-\d{2}|conflicts|match:[a-z_]+|missing:[A-Z_]+|check:[a-z_]+|screen:[a-z_]+)$"
)


class FieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity: str | None = None
    attribute: str | None = None
    type: FieldType
    tier: Tier | None = None
    role: Role = "fact"
    labels: list[str] = Field(default_factory=lambda: list[str]())
    validators: list[str] = Field(default_factory=lambda: list[str]())
    used_by: list[str] = Field(default_factory=lambda: list[str]())
    shapes: list[Shape] | None = None
    description: str | None = None
    # An enum's values and the forms they are printed in ("violation": ["VIOL", "VIOLATION"])
    values: dict[str, list[str]] = Field(default_factory=lambda: dict[str, list[str]]())
    # Set by code, never asked of the model (a role printed where a date belongs)
    derived: bool = False
    # Named cleaners applied before the value becomes a fact (lane.extract.engine_v2.CLEANERS)
    clean: list[str] = Field(default_factory=lambda: list[str]())
    # A nested table inside a table record (a claim's lines)
    fields: dict[str, "FieldSpec"] | None = None

    @model_validator(mode="after")
    def _complete(self) -> "FieldSpec":
        if self.type == "table":
            if not self.fields:
                raise ValueError("a table field needs fields")
            return self
        if not (self.entity and self.attribute and self.tier):
            raise ValueError("a field needs entity, attribute and tier")
        if not self.used_by:
            raise ValueError("a field needs at least one used_by")
        bad = [u for u in self.used_by if not _USED_BY.match(u)]
        if bad:
            raise ValueError(f"unknown used_by {bad}")
        return self


class TableSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    fields: dict[str, FieldSpec]
    role: Role = "fact"
    single: bool = False  # one record (a totals row), not a list
    sheet: Literal["columns", "cells"] | None = None
    sheet_key: str | None = None  # the key in the worksheet answer, when it differs
    # A table laid out in columns: the record field whose value heads its column
    column_key: str | None = None
    shapes: list[Shape] | None = None


class DocSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str
    sheet_prompt: str | None = None
    shapes: list[Shape]
    fields: dict[str, FieldSpec] = Field(default_factory=lambda: dict[str, FieldSpec]())
    tables: dict[str, TableSpec] = Field(default_factory=lambda: dict[str, TableSpec]())


class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    doc_types: dict[str, DocSpec]
    sha256: str = ""
    # Types defined in an extension file (field_catalog.d/), with that file's own tag
    extension_tags: dict[str, str] = Field(default_factory=lambda: dict[str, str]())

    @property
    def tag(self) -> str:
        """'2.3f9a1c2b': the catalog's version and the hash of its content."""
        return f"{self.version}.{self.sha256[:8]}"

    def tag_for(self, doc_type: str) -> str:
        """The tag of the file that defines a type: an extension never changes the prompts (or
        the cached reads) of the types in the main catalog."""
        return self.extension_tags.get(doc_type, self.tag)


@cache
def catalog(path: Path = CATALOG_PATH) -> Catalog:
    text = path.read_text()
    data = cast(dict[str, Any], yaml.safe_load(text))
    data["sha256"] = hashlib.sha256(text.encode()).hexdigest()
    tags: dict[str, str] = {}
    for ext in sorted((path.parent / "field_catalog.d").glob("*.yaml")):
        ext_text = ext.read_text()
        ext_data = cast(dict[str, Any], yaml.safe_load(ext_text))
        tag = f"{ext_data['version']}.{hashlib.sha256(ext_text.encode()).hexdigest()[:8]}"
        for name, spec in cast(dict[str, Any], ext_data["doc_types"]).items():
            if name in data["doc_types"]:
                raise ValueError(f"{ext.name} redefines {name}, already in the main catalog")
            data["doc_types"][name] = spec
            tags[name] = f"x{tag}"
    data["extension_tags"] = tags
    return Catalog.model_validate(data)


# --- paths ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    """One catalog field, with where it sits: 'claims[].total_paid'."""

    doc_type: str
    path: str
    spec: FieldSpec

    @property
    def is_fact(self) -> bool:
        return self.spec.role == "fact"

    @property
    def tier(self) -> str:
        return self.spec.tier or "context"


def _shaped(shapes: list[Shape] | None, shape: Shape) -> bool:
    return shapes is None or shape in shapes


def entries(doc_type: str, shape: Shape | None = None) -> list[Entry]:
    """Every field of a document type (for one shape, or both), tables flattened."""
    doc = catalog().doc_types.get(doc_type)
    if doc is None:
        return []
    out: list[Entry] = []

    def add(prefix: str, fields: dict[str, FieldSpec], role: Role) -> None:
        for name, spec in fields.items():
            if shape and not _shaped(spec.shapes, shape):
                continue
            if spec.type == "table" and spec.fields:
                add(f"{prefix}{name}[].", spec.fields, spec.role)
                continue
            effective = spec if role == "fact" else spec.model_copy(update={"role": role})
            out.append(Entry(doc_type, f"{prefix}{name}", effective))

    add("", doc.fields, "fact")
    for name, table in doc.tables.items():
        if shape and not _shaped(table.shapes, shape):
            continue
        prefix = f"{name}." if table.single else f"{name}[]."
        add(prefix, table.fields, table.role)
    return out


_INDEX = re.compile(r"\[\d+\]")


@cache
def _index(doc_type: str) -> dict[str, Entry]:
    return {e.path: e for e in entries(doc_type)}


def entry_for(doc_type: str, path: str) -> Entry | None:
    """The catalog entry for an extracted path ('claims[2].lines[0].paid'), if any."""
    return _index(doc_type).get(_INDEX.sub("[]", path))


# --- schemas -------------------------------------------------------------------------------


def _model(name: str, base: type[BaseModel] | None, defs: dict[str, Any]) -> type[BaseModel]:
    """pydantic's create_model, typed: field definitions are (type, Field) pairs."""
    if base is None:
        return create_model(name, **defs)
    return create_model(name, __base__=base, **defs)


def _describe(name: str, spec: FieldSpec) -> str:
    parts = [spec.description or name.replace("_", " ")]
    if spec.labels:
        parts.append(f"printed beside: {', '.join(spec.labels)}")
    return "; ".join(parts)


def _pdf_record(model_name: str, fields: dict[str, FieldSpec]) -> type[BaseModel]:
    defs: dict[str, Any] = {}
    for name, spec in fields.items():
        if not _shaped(spec.shapes, "pdf") or spec.derived:
            continue
        if spec.type == "table" and spec.fields:
            sub = _pdf_record(f"{model_name}{_camel(name)}", spec.fields)
            defs[name] = (
                list[sub],  # type: ignore[valid-type]
                Field(default_factory=list, description=_describe(name, spec)),
            )
        else:
            defs[name] = (str | None, Field(default=None, description=_describe(name, spec)))
    return _model(model_name, s.Row, defs)


def _camel(name: str) -> str:
    return "".join(p.capitalize() for p in name.split("_"))


@cache
def pdf_schema(doc_type: str) -> type[BaseModel] | None:
    doc = catalog().doc_types.get(doc_type)
    if doc is None or "pdf" not in doc.shapes:
        return None
    base = f"{_camel(doc_type)}Extraction"
    defs: dict[str, Any] = {}
    for name, spec in doc.fields.items():
        if _shaped(spec.shapes, "pdf"):
            defs[name] = (s.Val, Field(description=_describe(name, spec)))
    for name, table in doc.tables.items():
        if not _shaped(table.shapes, "pdf"):
            continue
        record = _pdf_record(f"{base}{_camel(name)}", table.fields)
        if table.single:
            defs[name] = (record, Field(description=table.description))
        else:
            defs[name] = (list[record], Field(description=table.description))  # type: ignore[valid-type]
    return _model(base, None, defs)


@cache
def sheet_schema(doc_type: str) -> type[BaseModel] | None:
    doc = catalog().doc_types.get(doc_type)
    if doc is None or "sheet" not in doc.shapes:
        return None
    base = f"{_camel(doc_type)}SheetExtraction"
    defs: dict[str, Any] = {}
    for name, spec in doc.fields.items():
        if _shaped(spec.shapes, "sheet"):
            defs[name] = (s.CellRef, Field(description=_describe(name, spec)))
    for name, table in doc.tables.items():
        if not _shaped(table.shapes, "sheet") or table.sheet is None:
            continue
        key = table.sheet_key or name
        cols = {
            n: f for n, f in table.fields.items() if _shaped(f.shapes, "sheet") and not f.derived
        }
        if table.sheet == "columns":
            model = _model(
                f"{base}{_camel(key)}Columns",
                s.ColumnMap,
                {
                    n: (
                        str | None,
                        Field(default=None, description=f"Column letter: {_describe(n, f)}"),
                    )
                    for n, f in cols.items()
                },
            )
            defs[key] = (model, Field(description=table.description))
        else:
            model = _model(
                f"{base}{_camel(key)}Cells",
                None,
                {n: (s.CellRef, Field(description=_describe(n, f))) for n, f in cols.items()},
            )
            defs[key] = (list[model], Field(description=table.description))  # type: ignore[valid-type]
    return _model(base, None, defs)


def prompt_fields(doc_type: str, shape: Shape) -> str:
    """The field list for a prompt, generated from the catalog."""
    doc = catalog().doc_types[doc_type]
    lines = [f"Fields to read (field catalog {catalog().tag_for(doc_type)}):"]
    for name, spec in doc.fields.items():
        if _shaped(spec.shapes, shape):
            lines.append(f"- {name}: {_describe(name, spec)}")
    for name, table in doc.tables.items():
        if not _shaped(table.shapes, shape) or (shape == "sheet" and table.sheet is None):
            continue
        lines.append(
            f"- {table.sheet_key if shape == 'sheet' and table.sheet_key else name}: {table.description}"
        )
        for fname, spec in table.fields.items():
            if not _shaped(spec.shapes, shape) or spec.derived:
                continue
            lines.append(f"  - {fname}: {_describe(fname, spec)}")
            for sub, sspec in (spec.fields or {}).items():
                lines.append(f"    - {sub}: {_describe(sub, sspec)}")
    if shape == "pdf":
        lines.append(
            "Read only these fields. Copy each value exactly as printed; give the quote with the "
            "label it is printed beside, and the page. Leave a field null when it is not printed."
        )
    return "\n".join(lines)


def prompt_name(doc_type: str, shape: Shape) -> str:
    doc = catalog().doc_types[doc_type]
    return (doc.sheet_prompt or doc.prompt) if shape == "sheet" else doc.prompt


def column_key(doc_type: str, path: str) -> str | None:
    """For a table laid out in columns, the record field whose value heads the column."""
    doc = catalog().doc_types.get(doc_type)
    table = path.split("[", 1)[0]
    spec = doc.tables.get(table) if doc else None
    return spec.column_key if spec else None
