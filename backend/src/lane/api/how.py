"""How Lane works, with this submission's own numbers: the eight stages of the lane, where a
model reads and where code decides, the guarantees with their live proof, where a person decides,
and the eval figures. Read only; every number comes from what is stored."""

import json
from collections import Counter
from typing import Any, cast

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select

from lane.api.deps import SessionDep, SettingsDep
from lane.api.evals import REPORT_DIR, list_evals
from lane.api.submissions import summary_of
from lane.db.models import (
    AuditEvent,
    Document,
    Fact,
    Page,
    PageKind,
    ResolvedEntity,
    ResolvedField,
    RuleOutcomeRecord,
    Run,
    Step,
    Submission,
)
from lane.overview.service import group_counts
from lane.security import injection

router = APIRouter(prefix="/api/v1", tags=["how"])


class StageOut(BaseModel):
    key: str
    name: str
    count: int | None
    headline: str | None = None  # in place of a count: the decision ("Refer")
    detail: str
    by: list[str]  # "AI", "Code"
    what: str
    method: str
    model: str | None
    seconds: float | None
    cost_usd: float | None
    guarantee: str
    tab: str  # path under the submission


class ProofOut(BaseModel):
    claim: str
    proof: str
    link: str | None = None  # path under the submission, or a field to open in the lens
    field_id: str | None = None


class PersonStepOut(BaseModel):
    step: str
    count: int
    tab: str


class FigureOut(BaseModel):
    label: str
    value: str


class HowOut(BaseModel):
    submission_id: str
    applicant: str | None
    run_trigger: str | None
    stages: list[StageOut]
    guarantees: list[ProofOut]
    person: list[PersonStepOut]
    proof: list[FigureOut]


def _step_cost(step: Step | None) -> float | None:
    if step is None:
        return None
    summary = step.summary or {}
    llm = cast(dict[str, Any], summary.get("llm") or {})
    cost = llm.get("cost_usd", summary.get("cost_usd"))
    return round(float(cost), 4) if cost is not None else 0.0


def _secs(*steps: Step | None) -> float | None:
    ms = [s.duration_ms for s in steps if s is not None and s.duration_ms is not None]
    return round(sum(ms) / 1000, 1) if ms else None


def _n(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


@router.get("/submissions/{submission_id}/how", operation_id="how_lane_works")
def how(submission_id: str, session: SessionDep, settings: SettingsDep) -> HowOut:
    sub = session.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    s = summary_of(session, sub, sub.email)
    run = session.scalars(
        select(Run)
        .where(Run.submission_id == sub.id, Run.trigger != "replay", Run.trigger != "what-if")
        .order_by(Run.started_at.desc())
        .limit(1)
    ).first()
    steps: dict[str, Step] = {st.name: st for st in run.steps} if run else {}

    def summ(name: str) -> dict[str, Any]:
        st = steps.get(name)
        return dict(st.summary or {}) if st else {}

    pages = list(session.scalars(select(Page.kind).where(Page.submission_id == sub.id)))
    scanned = sum(1 for k in pages if k in (PageKind.SCAN, PageKind.PHOTO))
    reused = int(summ("triage").get("same_file_reused", 0)) + int(
        summ("triage").get("reused_attachments", 0)
    )
    docs = int(
        session.scalar(
            select(func.count()).select_from(Document).where(Document.submission_id == sub.id)
        )
        or 0
    )
    tier1 = int(cast(dict[str, Any], summ("classify").get("llm") or {}).get("tier1_pages", 0))
    facts = int(
        session.scalar(select(func.count()).select_from(Fact).where(Fact.submission_id == sub.id))
        or 0
    )
    extract = summ("extract")
    decision_facts = int(extract.get("decision_facts", 0))
    rules = list(
        session.scalars(select(RuleOutcomeRecord).where(RuleOutcomeRecord.submission_id == sub.id))
    )
    unknown = [r for r in rules if r.outcome == "unknown"]
    pd = next((r for r in rules if r.potential_decline), None)
    route = s.recommendation_headline or "Not decided yet"
    if s.potential_decline:
        route += ", potential decline"

    stages = [
        StageOut(
            key="email",
            name="Email",
            count=1,
            detail=f"from {s.sender_name or s.sender_email.rsplit('@', 1)[-1]}",
            by=["Code"],
            what="The agent's email is stored as it arrived, with every attachment and archive "
            "opened, and matched to any submission it answers.",
            method="Parsing, content hashes, the reference token and thread headers",
            model=None,
            seconds=None,
            cost_usd=0.0,
            guarantee="The same email twice changes nothing; a reply is attached only on a "
            "reference, a thread or a matching DOT, MC or FEIN, never on a name alone.",
            tab="/documents",
        ),
        StageOut(
            key="files",
            name="Files",
            count=s.files,
            detail=f"{s.duplicates} duplicates merged",
            by=["Code"],
            what="Each file is identified by its bytes, not its name: copies are merged, archives "
            "opened, unreadable files reported.",
            method="sha256 per file, file type sniffing, archive limits",
            model=None,
            seconds=None,
            cost_usd=0.0,
            guarantee="Nothing is dropped silently: every file is accepted, merged as a "
            "duplicate, expanded or rejected with a reason.",
            tab="/documents",
        ),
        StageOut(
            key="pages",
            name="Pages",
            count=len(pages),
            detail=f"{scanned} scanned" + (f", {reused} files already read" if reused else ""),
            by=["Code"],
            what="Every page is rendered, scans are straightened and read by OCR, and the "
            "quality of each page is measured.",
            method="Text layers, local OCR, image preparation; a file read before is not read "
            "again",
            model=None,
            seconds=_secs(steps.get("triage")),
            cost_usd=0.0,
            guarantee="Hidden text is removed before any model reads a page, and recorded.",
            tab="/documents",
        ),
        StageOut(
            key="documents",
            name="Documents",
            count=docs,
            detail=f"{tier1} pages typed by fixed signals",
            by=["AI", "Code"],
            what="Pages are typed and cut into documents. Fixed signals decide first; a model "
            "types the rest; any disagreement goes to review.",
            method="Form identifiers, worksheet headers, key phrases, then the model",
            model=settings.llm_classify_model,
            seconds=_secs(steps.get("classify")),
            cost_usd=_step_cost(steps.get("classify")),
            guarantee="A misnamed file is typed by its content; the type is never taken from "
            "the filename.",
            tab="/documents",
        ),
        StageOut(
            key="facts",
            name="Facts",
            count=facts,
            detail="decision values read twice",
            by=["AI", "Code"],
            what="A model reads each document into catalog fields; each value is found again "
            "on the page, checked by validators, and decision values are read a second time.",
            method="Structured reads, grounding on page text or cells, validators, recall checks",
            model=settings.llm_extract_model,
            seconds=_secs(steps.get("extract")),
            cost_usd=_step_cost(steps.get("extract")),
            guarantee=f"Every value carries its document, page or cell, and quote; "
            f"{decision_facts:,} decision values were checked twice.",
            tab="/drivers",
        ),
        StageOut(
            key="reconcile",
            name="Reconcile",
            count=s.conflicts_open,
            detail=f"conflicts open, {s.missing} missing",
            by=["Code"],
            what="Facts about the same applicant, driver, unit or term are joined across "
            "documents; every disagreement is kept and every gap listed.",
            method="Matching by license, VIN, policy number and name; supersession by date",
            model=None,
            seconds=_secs(steps.get("resolve")),
            cost_usd=0.0,
            guarantee="Nothing is picked silently: both values stay until a person chooses, "
            "with a reason.",
            tab="/conflicts",
        ),
        StageOut(
            key="rules",
            name="Rules",
            count=len(rules),
            detail=group_counts(rules),
            by=["Code"],
            what="Each guideline rule runs as code on the reconciled facts and quotes the "
            "guideline line it applies.",
            method="Predicates over the facts, with every disputed value tried both ways",
            model=None,
            seconds=_secs(steps.get("rules")),
            cost_usd=0.0,
            guarantee="A rule Lane can't check yet never becomes a pass; a decline needs certainty.",
            tab="/rules",
        ),
        StageOut(
            key="decision",
            name="Decision",
            count=None,
            headline=(s.recommendation or "pending").capitalize(),
            detail=route,
            by=["Code"],
            what="The rules give a route and a recommendation; a model writes a cited summary "
            "and the email to the agent; a person decides.",
            method="The final route from the rules; summary and email checked by code",
            model=settings.llm_analyze_model,
            seconds=_secs(steps.get("analyze"), steps.get("actions")),
            cost_usd=_step_cost(steps.get("actions")),
            guarantee="Lane recommends; the underwriter decides, and both are kept.",
            tab="",
        ),
    ]

    # --- guarantees, each with its proof ---------------------------------------------------
    sample = session.scalars(
        select(ResolvedField)
        .join(ResolvedEntity, ResolvedEntity.id == ResolvedField.entity_id)
        .where(
            ResolvedField.submission_id == sub.id,
            ResolvedEntity.kind == "applicant",
            ResolvedField.attribute == "dot_number",
            ResolvedField.state == "known",
        )
    ).first()
    field_ids = set(
        session.scalars(select(ResolvedField.id).where(ResolvedField.submission_id == sub.id))
    )
    events = list(
        session.scalars(
            select(AuditEvent).where(
                (AuditEvent.subject_id == sub.id) | (AuditEvent.subject_id.in_(field_ids))
            )
        )
    )
    by_action = Counter(e.action for e in events)
    reveals = sum(
        1
        for e in session.scalars(
            select(AuditEvent).where(AuditEvent.action.in_(("pii.revealed", "pii.photo_revealed")))
        )
        if e.payload.get("submission_id") == sub.id or e.subject_id in field_ids
    )
    injections = sum(
        1
        for e in session.scalars(
            select(AuditEvent).where(
                AuditEvent.action.in_((injection.ACTION, injection.HIDDEN_ACTION))
            )
        )
        if e.payload.get("submission_id") == sub.id
    )
    perturb = _perturbations()
    inj = next((r for r in perturb if r.get("name") == "injection"), None)
    replay = session.scalars(
        select(Run)
        .where(Run.submission_id == sub.id, Run.trigger == "replay")
        .order_by(Run.started_at.desc())
        .limit(1)
    ).first()
    replay_note = (
        "No replay of this submission yet; Audit replays it from the stored files at no cost."
        if replay is None
        else (
            "The latest replay matched the stored output exactly."
            if cast(dict[str, Any], replay.versions.get("replay") or {}).get("identical")
            else "The latest replay differed; Audit lists what changed."
        )
    )
    guarantees = [
        ProofOut(
            claim="Every value opens its source",
            proof=f"USDOT {sample.value_display} opens on the page it was read from."
            if sample and sample.value_display
            else "Open any value to see the page or cell it was read from.",
            field_id=sample.id if sample else None,
        ),
        ProofOut(
            claim="Nothing is picked silently",
            proof=f"{_n(s.conflicts_open, 'conflict')} open, {s.conflicts_resolved} resolved.",
            link="/conflicts",
        ),
        ProofOut(
            claim="A decline needs certainty",
            proof=(
                f"{pd.rule_id} is a potential decline: {pd.reason.split('. ')[0]}. "
                + (
                    "It depends on a disputed fact; settling that fact decides it."
                    if pd.blocking_conflicts
                    else "It stays potential until a person confirms it."
                )
            )
            if pd
            else "No rule is a potential decline.",
            link="/rules",
        ),
        ProofOut(
            claim="Can't check yet never becomes a pass",
            proof=f"{_n(len(unknown), 'rule')} can't be checked yet: "
            + ", ".join(r.rule_id for r in unknown)
            + ". Each says what is missing."
            if unknown
            else "Every rule could be checked.",
            link="/rules",
        ),
        ProofOut(
            claim="A worse document never makes an outcome less careful",
            proof=f"{sum(1 for r in perturb if r.get('passed'))} of {len(perturb)} perturbations "
            "pass (blurred, rotated, renamed, missing and corrupted files).",
        ),
        ProofOut(claim="Same input, same output", proof=replay_note, link="/audit"),
        ProofOut(
            claim="Personal data is masked; every reveal is audited",
            proof=f"{_n(reveals, 'reveal')} recorded for this submission.",
            link="/audit",
        ),
        ProofOut(
            claim="Document text is data, never instructions",
            proof=(
                "The injection test passes: an instruction to approve, in the email and hidden "
                "in a loss run, is recorded and ignored."
                if inj and inj.get("passed")
                else "The injection test has not passed."
            )
            + (f" {_n(injections, 'attempt')} recorded here." if injections else ""),
        ),
    ]
    person = [
        PersonStepOut(
            step="Resolve a conflict", count=by_action["conflict.resolved"], tab="/conflicts"
        ),
        PersonStepOut(
            step="Confirm a change from an agent document",
            count=by_action["rule_change.confirmed"],
            tab="",
        ),
        PersonStepOut(
            step="Approve the expert review", count=by_action["analysis.approved"], tab="/findings"
        ),
        PersonStepOut(
            step="Mark the request sent",
            count=by_action["agent_request.marked_sent"],
            tab="/actions",
        ),
        PersonStepOut(step="Record the decision", count=by_action["decision.recorded"], tab=""),
    ]

    proof: list[FigureOut] = [
        FigureOut(
            label="Perturbations",
            value=f"{sum(1 for r in perturb if r.get('passed'))} of {len(perturb)} pass",
        )
    ]
    synthetic = next((r for r in perturb if str(r.get("name", "")).startswith("synthetic")), None)
    if synthetic:
        proof.append(FigureOut(label="Unseen packet", value=str(synthetic.get("headline") or "")))
    for report in list_evals()[:1]:
        for m in report.metrics:
            if m.name in (
                "Document classification",
                "Field accuracy",
                "Rule outcomes",
                "Expert finding coverage",
            ):
                proof.append(FigureOut(label=m.name, value=f"{m.correct} of {m.total}"))

    return HowOut(
        submission_id=sub.id,
        applicant=s.applicant,
        run_trigger=run.trigger if run else None,
        stages=stages,
        guarantees=guarantees,
        person=person,
        proof=proof,
    )


def _perturbations() -> list[dict[str, Any]]:
    path = REPORT_DIR / "perturbations.json"
    if not path.exists():
        return []
    return list(json.loads(path.read_text()).get("results", []))
