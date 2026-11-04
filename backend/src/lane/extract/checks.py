"""Arithmetic self-checks. Many insurance documents carry their own totals; if the values we read
add up to the printed totals, the reading is very likely right. If they do not, either the
document is wrong or we misread it, and a person or a stronger model should look.
"""

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, cast

from lane.domain.normalize import money, number, percent

MONEY_TOLERANCE = Decimal("0.05")


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    paths: list[str] = field(default_factory=lambda: list[str]())

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sum(values: list[str | None]) -> Decimal | None:
    nums = [number(v) for v in values]
    if any(n is None for v, n in zip(values, nums, strict=True) if v not in (None, "")):
        return None  # an unreadable cell makes the sum meaningless
    return sum((n for n in nums if n is not None), Decimal(0))


def _scalar(data: dict[str, Any], key: str) -> str | None:
    """The printed value of a scalar field ({"value": ...})."""
    node: Any = data.get(key)
    if isinstance(node, dict):
        value: Any = cast(dict[str, Any], node).get("value")
        return None if value is None else str(value)
    return None


def _records(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    items: Any = data.get(key)
    if not isinstance(items, list):
        return []
    return [cast(dict[str, Any], i) for i in cast(list[Any], items) if isinstance(i, dict)]


def _close(a: Decimal | None, b: Decimal | None, tol: Decimal) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


def _fmt(d: Decimal | None) -> str:
    return "unreadable" if d is None else f"{d:,}"


def _is_surcharge(row: dict[str, Any]) -> bool:
    if str(row.get("surcharge") or "").strip():
        return True
    miles, taxable = number(row.get("total_miles")), number(row.get("taxable_gallons"))
    return miles == 0 and bool(taxable)


def ifta_checks(data: dict[str, Any]) -> list[Check]:
    rows = _records(data, "rows")
    totals_node: Any = data.get("totals")
    totals = cast(dict[str, Any], totals_node) if isinstance(totals_node, dict) else {}
    out: list[Check] = []
    if not rows:
        return [Check("rows_present", False, "No jurisdiction rows were read")]

    # Surcharge lines (e.g. Kentucky, Indiana, Virginia) tax the jurisdiction's taxable gallons a
    # second time. Their gallons are not part of the fleet's gallon totals, but their tax is.
    regular = [r for r in rows if not _is_surcharge(r)]
    columns = {
        "total_miles": (Decimal(0), regular),
        "taxable_miles": (Decimal(0), regular),
        "taxable_gallons": (Decimal(0), regular),
        "tax_paid_gallons": (Decimal(0), regular),
        "net_taxable_gallons": (Decimal(0), regular),
        "tax": (MONEY_TOLERANCE, rows),
        "interest": (MONEY_TOLERANCE, rows),
        "total": (MONEY_TOLERANCE, rows),
    }
    for column, (tol, included) in columns.items():
        printed = number(totals.get(column))
        if printed is None:
            continue
        summed = _sum([r.get(column) for r in included])
        ok = _close(summed, printed, tol)
        out.append(
            Check(
                f"sum_{column}",
                ok,
                f"Rows add up to {_fmt(summed)}; printed total is {_fmt(printed)}",
                [f"totals.{column}"]
                + ([] if ok else [f"rows[{i}].{column}" for i in range(len(rows))]),
            )
        )

    bad_tax: list[int] = []
    for i, r in enumerate(rows):
        basis = "taxable_gallons" if _is_surcharge(r) else "net_taxable_gallons"
        gallons, rate, tax = number(r.get(basis)), number(r.get("rate")), money(r.get("tax"))
        if gallons is None or rate is None or tax is None:
            continue
        # Gallons are printed rounded, so allow half a gallon at the row's rate.
        if abs(gallons * rate - tax) > rate / 2 + MONEY_TOLERANCE:
            bad_tax.append(i)
    out.append(
        Check(
            "row_tax",
            not bad_tax,
            "Each row's tax equals its gallons times rate"
            if not bad_tax
            else f"{len(bad_tax)} rows where tax does not equal gallons times rate",
            [f"rows[{i}].tax" for i in bad_tax],
        )
    )

    subtotal, penalty, interest, total_due = (
        money(_scalar(data, k)) for k in ("subtotal_due", "penalty", "interest", "total_due")
    )
    if subtotal is not None and total_due is not None:
        expected = subtotal + (penalty or 0) + (interest or 0)
        out.append(
            Check(
                "summary_total",
                _close(expected, total_due, MONEY_TOLERANCE),
                f"Subtotal + penalty + interest = {_fmt(expected)}; "
                f"total due printed {_fmt(total_due)}",
                ["total_due"],
            )
        )
    return out


def loss_run_checks(data: dict[str, Any]) -> list[Check]:
    out: list[Check] = []
    claims = _records(data, "claims")
    for i, claim in enumerate(claims):
        lines = _records(claim, "lines")
        for column, total_key in (
            ("paid", "total_paid"),
            ("reserves", "total_reserves"),
            ("incurred", "total_incurred"),
        ):
            printed = money(claim.get(total_key))
            if printed is None or not lines:
                continue
            summed = _sum([ln.get(column) for ln in lines])
            out.append(
                Check(
                    f"claim_{i}_{column}",
                    _close(summed, printed, MONEY_TOLERANCE),
                    f"Claim {claim.get('claim_number') or i + 1}: lines add up to {_fmt(summed)}, "
                    f"claim total printed {_fmt(printed)}",
                    [f"claims[{i}].{total_key}"],
                )
            )

    summary = next(
        (
            c
            for c in _records(data, "coverage_totals")
            if str(c.get("covered") or "").lower() != "no coverage" and c.get("losses_paid")
        ),
        None,
    )
    if summary and claims:
        for column, key in (("total_paid", "losses_paid"), ("total_incurred", "incurred")):
            printed = money(summary.get(key))
            summed = _sum([c.get(column) for c in claims])
            if printed is None:
                continue
            out.append(
                Check(
                    f"term_{key}",
                    _close(summed, printed, MONEY_TOLERANCE),
                    f"Claims add up to {_fmt(summed)}; "
                    f"term {key.replace('_', ' ')} printed {_fmt(printed)}",
                    [f"coverage_totals.{key}"],
                )
            )

    printed_count = number(_scalar(data, "total_claims"))
    if printed_count is not None:
        out.append(
            Check(
                "claim_count",
                int(printed_count) == len(claims),
                f"{len(claims)} claims read; header says {int(printed_count)}",
                ["total_claims"],
            )
        )
    return out


def share_checks(data: dict[str, Any], *groups: str) -> list[Check]:
    out: list[Check] = []
    for group in groups:
        items = _records(data, group)
        values = [percent(i.get("percent")) for i in items if i.get("percent")]
        if len(values) < 2 or any(v is None for v in values):
            continue
        total = sum((v for v in values if v is not None), Decimal(0))
        out.append(
            Check(
                f"{group}_percent",
                abs(total - 100) <= 1,
                f"{group.capitalize()} shares add up to {total}%",
                [f"{group}[{i}].percent" for i in range(len(items))],
            )
        )
    return out


def run_checks(doc_type: str, data: dict[str, Any]) -> list[Check]:
    if doc_type == "ifta_return":
        return ifta_checks(data)
    if doc_type == "loss_run":
        return loss_run_checks(data)
    if doc_type in ("quote_form", "application"):
        return share_checks(data, "radius", "commodities")
    return []
