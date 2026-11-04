"""Resolve the model's cell references against the stored worksheet grids.

The model only says where a value is; the value itself is read here, from the cell. A reference
that points inside a merged range resolves to the range's top-left cell, where the value lives.
References to empty or non-existent cells resolve to nothing, never to a guess.
"""

import re
from dataclasses import dataclass
from typing import Any

from openpyxl.utils import column_index_from_string, get_column_letter, range_boundaries

_REF = re.compile(r"^(?:'?(?P<sheet>[^'!]+)'?!)?\$?(?P<col>[A-Z]{1,3})\$?(?P<row>\d+)$")


@dataclass(frozen=True)
class CellValue:
    sheet: str
    ref: str  # A1
    value: Any
    page_id: str
    fmt: str | None = None  # Excel number format, when not General

    @property
    def qualified(self) -> str:
        return f"'{self.sheet}'!{self.ref}"

    @property
    def text(self) -> str:
        """The value as the sheet displays it. A percent-formatted 1 shows as 100%."""
        if self.fmt and "%" in self.fmt and isinstance(self.value, int | float):
            return f"{self.value * 100:g}%"
        return str(self.value)


@dataclass
class Sheet:
    name: str
    page_id: str
    cells: dict[str, dict[str, Any]]  # ref -> cell record from triage
    merged: list[tuple[int, int, int, int]]  # min_col, min_row, max_col, max_row

    @classmethod
    def from_grid(cls, grid: dict[str, Any], page_id: str) -> "Sheet":
        merged: list[tuple[int, int, int, int]] = []
        for rng in grid.get("merged", []):
            min_col, min_row, max_col, max_row = range_boundaries(rng)
            if None not in (min_col, min_row, max_col, max_row):
                merged.append((min_col, min_row, max_col, max_row))  # type: ignore[arg-type]
        return cls(
            name=str(grid.get("sheet")),
            page_id=page_id,
            cells={str(c["ref"]): c for c in grid.get("cells", [])},
            merged=merged,
        )

    def anchor(self, col: int, row: int) -> str:
        for min_col, min_row, max_col, max_row in self.merged:
            if min_col <= col <= max_col and min_row <= row <= max_row:
                return f"{get_column_letter(min_col)}{min_row}"
        return f"{get_column_letter(col)}{row}"

    def get(self, col: int, row: int) -> CellValue | None:
        ref = self.anchor(col, row)
        cell = self.cells.get(ref)
        if cell is None or cell.get("v") in (None, ""):
            return None
        fmt = cell.get("fmt")
        return CellValue(self.name, ref, cell["v"], self.page_id, str(fmt) if fmt else None)


class Workbook:
    def __init__(self, sheets: list[Sheet]) -> None:
        self.sheets = sheets
        self.by_name = {s.name.strip().lower(): s for s in sheets}

    def sheet(self, name: str | None) -> Sheet | None:
        if not self.sheets:
            return None
        if name is None:
            return self.sheets[0]
        return self.by_name.get(name.strip().strip("'").lower())

    def resolve(self, reference: str | None, default_sheet: str | None = None) -> CellValue | None:
        if not reference:
            return None
        match = _REF.match(
            reference.strip().upper() if "!" not in reference else _upper_ref(reference)
        )
        if not match:
            return None
        sheet = self.sheet(match["sheet"] or default_sheet)
        if sheet is None:
            return None
        return sheet.get(column_index_from_string(match["col"]), int(match["row"]))

    def read_table(
        self, sheet_name: str | None, first: int | None, last: int | None, columns: dict[str, str]
    ) -> list[dict[str, CellValue]]:
        """Rows from first to last (or until three empty rows), mapped by column letter."""
        sheet = self.sheet(sheet_name)
        if sheet is None or first is None or not columns:
            return []
        cols = {
            field: column_index_from_string(letter.strip().upper())
            for field, letter in columns.items()
            if letter and re.fullmatch(r"[A-Za-z]{1,3}", letter.strip())
        }
        rows: list[dict[str, CellValue]] = []
        empty_run = 0
        max_row = max((int(c["r"]) for c in sheet.cells.values()), default=first)
        for row in range(first, (last or max_row) + 1):
            record = {f: v for f, c in cols.items() if (v := sheet.get(c, row)) is not None}
            if record:
                rows.append(record)
                empty_run = 0
            else:
                empty_run += 1
                if last is None and empty_run >= 3:
                    break
        return rows


def _upper_ref(reference: str) -> str:
    sheet, _, cell = reference.rpartition("!")
    return f"{sheet}!{cell.strip().upper()}"
