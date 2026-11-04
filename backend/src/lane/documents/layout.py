"""Readable text from stored page layouts (word boxes and worksheet grids)."""

import json
from collections import defaultdict
from typing import Any


def load_layout(raw: bytes) -> dict[str, Any]:
    return json.loads(raw)


def words_to_text(layout: dict[str, Any]) -> str:
    """Rebuild reading-order lines from PDF word boxes (grouped by block and line)."""
    lines: dict[tuple[int, int], list[tuple[float, str]]] = defaultdict(list)
    tops: dict[tuple[int, int], float] = {}
    for word in layout.get("words", []):
        x0, y0, _x1, _y1, text, block, line = word[:7]  # OCR words carry a score at the end
        key = (int(block), int(line))
        lines[key].append((float(x0), str(text)))
        tops[key] = min(tops.get(key, float(y0)), float(y0))
    ordered = sorted(lines, key=lambda k: (round(tops[k] / 3), min(x for x, _ in lines[k])))
    return "\n".join(" ".join(t for _, t in sorted(lines[k])) for k in ordered)


def sheet_to_text(grid: dict[str, Any], *, max_cells: int) -> str:
    """One line per row: 'Row 5: B=Date of Application | E=2026-10-22'. Cell refs survive."""
    rows: dict[int, list[str]] = defaultdict(list)
    for count, cell in enumerate(grid.get("cells", [])):
        if count >= max_cells:
            break
        ref = str(cell["ref"])
        column = "".join(ch for ch in ref if ch.isalpha())
        rows[int(cell["r"])].append(f"{column}={cell['v']}")
    out = [f"Worksheet {grid.get('sheet')!r} (state: {grid.get('state')})"]
    out += [f"Row {r}: " + " | ".join(values) for r, values in sorted(rows.items())]
    shown = sum(len(v) for v in rows.values())
    total = len(grid.get("cells", []))
    if shown < total:
        out.append(f"... {total - shown} more cells not shown")
    return "\n".join(out)
