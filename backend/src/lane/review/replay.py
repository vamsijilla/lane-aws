"""Deterministic replay: run every step again from the stored files, with model answers only from
the cache, and compare a hash of everything produced with what is stored now.

A replay never touches the real results. It runs against a copy of the database and records only
its verdict (a replay run, its steps and an audit event) in the real one. A model call that is not
in the cache is recorded as such and never sent, so a replay costs nothing.
Production equivalent: replay in an isolated workspace or schema with the same versions pinned.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from lane.audit import chain
from lane.config import Settings
from lane.db.base import utcnow
from lane.db.models import LlmCall, Run, RunStatus, Step
from lane.db.scratch import scratch_copy
from lane.llm.gateway import NOT_CACHED, GatewayFactory, LlmGateway
from lane.pipeline import fingerprint
from lane.pipeline.registry import PIPELINE
from lane.pipeline.runner import StepDef, process_submission
from lane.storage.blobs import BlobStore

ACTOR = "system:replay"


@dataclass
class Verdict:
    identical: bool
    before_hash: str
    after_hash: str | None
    not_cached: int  # model calls the cache could not answer
    versions_changed: list[str] = field(default_factory=lambda: list[str]())
    differences: list[str] = field(default_factory=lambda: list[str]())  # output sections


def _base_run(session: Session, sid: str) -> Run | None:
    """The latest full run that was not itself a replay: what the stored results came from."""
    names = {s.name for s in PIPELINE}
    for run in session.scalars(
        select(Run)
        .where(Run.submission_id == sid, Run.trigger != "replay")
        .order_by(Run.started_at.desc())
    ):
        if names <= {s.name for s in run.steps}:
            return run
    return None


def _step_versions(run: Run | None) -> dict[str, str]:
    return {s.name: s.version for s in run.steps} if run else {}


def _prompt_versions(session: Session, run_id: str | None) -> dict[str, str]:
    if run_id is None:
        return {}
    return {
        c.prompt_name: f"v{c.prompt_version} ({c.prompt_sha256[:8]})"
        for c in session.scalars(select(LlmCall).where(LlmCall.run_id == run_id))
    }


def _changes(label: str, before: dict[str, str], after: dict[str, str]) -> list[str]:
    return [
        f"{label} {k}: {before[k]} to {after[k]}"
        for k in sorted(before.keys() & after.keys())
        if before[k] != after[k]
    ]


def start(session: Session, sid: str) -> Run:
    """Record that a replay started, so the screens can follow it."""
    base = _base_run(session, sid)
    run = Run(
        submission_id=sid,
        trigger="replay",
        status=RunStatus.RUNNING,
        replay_of=base.id if base else None,
    )
    session.add(run)
    session.commit()
    return run


def replay(
    session: Session,
    store: BlobStore,
    settings: Settings,
    run: Run,
    *,
    gateway_factory: GatewayFactory = LlmGateway,
    steps: list[StepDef] = PIPELINE,
    workdir: Path | None = None,
) -> Verdict:
    sid = run.submission_id
    before_hash = fingerprint.output_hash(session, sid)
    before_sections = fingerprint.section_hashes(session, sid)
    base = session.get(Run, run.replay_of) if run.replay_of else None
    base_steps = _step_versions(base)
    base_prompts = _prompt_versions(session, base.id if base else None)

    with scratch_copy(session.get_bind().engine, workdir=workdir) as engine:
        replay_settings = settings.model_copy(update={"llm_cache_only": True})
        with sessionmaker(engine, expire_on_commit=False)() as scratch:
            done = process_submission(
                scratch,
                store,
                replay_settings,
                sid,
                steps=steps,
                trigger="replay",
                llm_factory=gateway_factory,
                replay=True,
            )
            after_sections = fingerprint.section_hashes(scratch, sid)
            not_cached = (
                scratch.scalar(
                    select(func.count())
                    .select_from(LlmCall)
                    .where(LlmCall.run_id == done.id, LlmCall.error == NOT_CACHED)
                )
                or 0
            )
            prompts = _prompt_versions(scratch, done.id)
            result: dict[str, Any] = {
                "status": done.status,
                "input_hash": done.input_hash,
                "output_hash": done.output_hash,
                "versions": done.versions,
                "steps": [
                    Step(
                        run_id=run.id,
                        name=s.name,
                        version=s.version,
                        status=s.status,
                        attempt=s.attempt,
                        started_at=s.started_at,
                        finished_at=s.finished_at,
                        duration_ms=s.duration_ms,
                        summary=s.summary,
                        error=s.error,
                    )
                    for s in done.steps
                ],
                # The replay's model calls (all from the cache, or recorded as not cached)
                "calls": [
                    _copy_row(c, run_id=run.id)
                    for c in scratch.scalars(select(LlmCall).where(LlmCall.run_id == done.id))
                ],
            }

    differences = sorted(k for k in after_sections if after_sections[k] != before_sections.get(k))
    verdict = Verdict(
        identical=result["output_hash"] == before_hash and not not_cached,
        before_hash=before_hash,
        after_hash=result["output_hash"],
        not_cached=not_cached,
        versions_changed=_changes("Step", base_steps, _step_versions_of(result["steps"]))
        + _changes("Prompt", base_prompts, prompts),
        differences=differences,
    )
    for row in [*result["steps"], *result["calls"]]:
        session.add(row)
    run.status = result["status"]
    run.finished_at = utcnow()
    run.input_hash = result["input_hash"]
    run.output_hash = result["output_hash"]
    run.versions = result["versions"] | {
        "replay": {
            "identical": verdict.identical,
            "before_hash": before_hash,
            "not_cached": not_cached,
            "versions_changed": verdict.versions_changed,
            "differences": differences,
        }
    }
    chain.record(
        session,
        actor=ACTOR,
        action="run.replayed",
        subject_type="run",
        subject_id=run.id,
        payload={
            "submission_id": sid,
            "identical": verdict.identical,
            "before_hash": before_hash,
            "after_hash": result["output_hash"],
            "not_cached": not_cached,
            "differences": differences,
        },
    )
    session.commit()
    return verdict


def _step_versions_of(steps: list[Step]) -> dict[str, str]:
    return {s.name: s.version for s in steps}


def _copy_row(row: LlmCall, **changes: Any) -> LlmCall:
    """A copy of a call record from the scratch database, with a new id."""
    columns = {c.key: getattr(row, c.key) for c in LlmCall.__table__.columns if c.key != "id"}
    return LlmCall(**(columns | changes))
