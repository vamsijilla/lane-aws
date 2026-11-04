"""Eval reports written by `make eval` (evals/report/<gold>.json)."""

import json
import re
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from lane.api.models import EvalFiguresOut
from lane.evals.gold import EVALS_DIR

router = APIRouter(prefix="/api/v1/evals", tags=["evals"])
REPORT_DIR = EVALS_DIR / "report"


class MetricOut(BaseModel):
    name: str
    correct: int
    total: int
    rate: float | None
    misses: list[str]


class EvalReportOut(BaseModel):
    gold: str
    submission_id: str
    generated_at: str
    metrics: list[MetricOut]
    figures: EvalFiguresOut


def _read(path: Path) -> EvalReportOut:
    return EvalReportOut.model_validate(json.loads(path.read_text()))


class CheckOut(BaseModel):
    check: str
    ok: bool
    detail: str


class PerturbationOut(BaseModel):
    name: str
    what: str
    submission_id: str
    passed: bool
    headline: str | None
    checks: list[CheckOut]


class PerturbationReportOut(BaseModel):
    generated_at: str
    results: list[PerturbationOut]


@router.get("/perturbations", operation_id="get_perturbations")
def get_perturbations() -> PerturbationReportOut:
    """Each perturbed packet and the synthetic packet, with every expected effect checked."""
    path = REPORT_DIR / "perturbations.json"
    if not path.exists():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No perturbation report yet. Run make perturb."
        )
    report = PerturbationReportOut.model_validate(json.loads(path.read_text()))
    for r in report.results:
        for c in r.checks:
            c.check = plain_check(c.check)
    return report


# Missing-item codes in the words the Missing page uses
_CODE_WORDS = {
    "APP_FEIN": "FEIN or SSN",
    "DRV_UNLISTED": "driver found only in loss history",
    "VEH_UNLISTED": "vehicle found only in loss history",
}
_CODE = re.compile(r"\b(?:LOSS_TERM:(\w+):(\d{4}-\d{2}-\d{2})|[A-Z][A-Z0-9]*_[A-Z0-9_]+)\b")


def _code_words(m: re.Match[str]) -> str:
    if m.group(1):
        start = date.fromisoformat(m.group(2))
        line = m.group(1).replace("_", " ")
        return f"loss run for the {line} term from {start:%b} {start.day}, {start.year}"
    return _CODE_WORDS.get(m.group(0), m.group(0).replace("_", " ").lower())


def plain_check(text: str) -> str:
    """A check as the Evals page shows it: missing items by name, not code, and the status
    words the rest of the app uses. The stored report keeps the text it was written with."""
    text = _CODE.sub(_code_words, text)
    text = re.sub(r"\bNo missing item\b", "No missing item:", text)
    text = re.sub(r"\bMissing item\b", "Missing item:", text)
    text = text.replace("flagged as low quality", "marked as low quality")
    text = text.replace(
        "are unknown, never another outcome", "can't be checked yet, never another outcome"
    )
    return re.sub(r"^Only (.+) unknown$", r"Only \1 can't be checked yet", text)


@router.get("", operation_id="list_evals")
def list_evals() -> list[EvalReportOut]:
    """Every gold-set report, newest first."""
    golds = {g.stem for g in (EVALS_DIR / "gold").glob("*.yaml")}
    reports = (
        [_read(p) for p in REPORT_DIR.glob("*.json") if p.stem in golds]
        if REPORT_DIR.exists()
        else []
    )
    return sorted(reports, key=lambda r: r.generated_at, reverse=True)


@router.get("/latest", operation_id="get_latest_eval")
def latest_eval() -> EvalReportOut:
    reports = list_evals()
    if not reports:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No eval report yet. Run make eval.")
    return reports[0]


@router.get("/{name}", operation_id="get_eval")
def get_eval(name: str) -> EvalReportOut:
    path = REPORT_DIR / f"{name}.json"
    if not name.replace("_", "").isalnum() or not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such eval report")
    return _read(path)
