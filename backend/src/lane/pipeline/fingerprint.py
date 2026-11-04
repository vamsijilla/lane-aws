"""Run fingerprints for replay: what went in, which versions ran, and one hash of everything that
came out. Ids and timestamps are left out, so two runs over the same files with the same versions
and the same model answers hash the same."""

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.db.models import (
    AgentRequestItem,
    Attachment,
    Conflict,
    DecisionRecord,
    Document,
    EmailDraft,
    EmailMessage,
    Fact,
    FindingRecord,
    LlmCall,
    MissingInfo,
    ResolvedEntity,
    ResolvedField,
    RuleOutcomeRecord,
    Run,
    Step,
    Submission,
)


def _sha(data: Any) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def input_hash(session: Session, sid: str) -> str:
    """The email and every attached file, by content."""
    sub = session.get(Submission, sid)
    assert sub is not None
    email = session.get(EmailMessage, sub.email_id)
    files = sorted(
        a.blob_sha256 or ""
        for a in session.scalars(select(Attachment).where(Attachment.submission_id == sid))
    )
    return _sha({"email": email.raw_sha256 if email else None, "files": files})


def versions(session: Session, run: Run) -> dict[str, Any]:
    steps = {s.name: s.version for s in session.scalars(select(Step).where(Step.run_id == run.id))}
    prompts = sorted(
        {
            f"{c.prompt_name} v{c.prompt_version} ({c.prompt_sha256[:8]}) on {c.model}"
            for c in session.scalars(select(LlmCall).where(LlmCall.run_id == run.id))
        }
    )
    decision = session.get(DecisionRecord, run.submission_id)
    return {
        "steps": steps,
        "ruleset": f"{decision.ruleset_version} ({decision.ruleset_sha256[:8]})"
        if decision
        else None,
        "prompts": prompts,
    }


def output(session: Session, sid: str) -> dict[str, Any]:
    """Everything the pipeline produced for a submission, in a canonical form."""
    docs = list(
        session.scalars(
            select(Document).where(Document.submission_id == sid).order_by(Document.ordinal)
        )
    )
    ordinal = {d.id: d.ordinal for d in docs}
    entities = {
        e.id: e
        for e in session.scalars(select(ResolvedEntity).where(ResolvedEntity.submission_id == sid))
    }
    fields = list(session.scalars(select(ResolvedField).where(ResolvedField.submission_id == sid)))
    field_key = {f.id: f"{entities[f.entity_id].key}.{f.attribute}" for f in fields}
    decision = session.get(DecisionRecord, sid)
    draft = session.scalars(
        select(EmailDraft)
        .where(EmailDraft.submission_id == sid)
        .order_by(EmailDraft.version.desc())
    ).first()
    return {
        "documents": [
            [
                d.ordinal,
                d.doc_type,
                d.page_from,
                d.page_to,
                d.title,
                ordinal.get(d.superseded_by_id or ""),
            ]
            for d in docs
        ],
        "facts": sorted(
            [ordinal.get(f.document_id), f.path, f.value_norm, f.page_no, f.cell_ref, f.grounding]
            for f in session.scalars(select(Fact).where(Fact.submission_id == sid))
        ),
        "fields": sorted(
            [field_key[f.id], f.state, f.value, f.resolution, sorted(f.notes)] for f in fields
        ),
        "conflicts": sorted(
            [field_key.get(c.field_id, ""), c.type, sorted(c.blocking_rules)]
            for c in session.scalars(select(Conflict).where(Conflict.submission_id == sid))
        ),
        "missing": sorted(
            session.scalars(select(MissingInfo.code).where(MissingInfo.submission_id == sid))
        ),
        "rules": [
            [r.rule_id, r.outcome, r.potential_decline, r.reason, r.items]
            for r in session.scalars(
                select(RuleOutcomeRecord)
                .where(RuleOutcomeRecord.submission_id == sid)
                .order_by(RuleOutcomeRecord.ordinal)
            )
        ],
        "decision": [decision.outcome, decision.team, decision.headline, decision.potential_decline]
        if decision
        else None,
        "findings": [
            [f.title, f.kind, f.severity, f.body, f.citations]
            for f in session.scalars(
                select(FindingRecord)
                .where(FindingRecord.submission_id == sid)
                .order_by(FindingRecord.ordinal)
            )
        ],
        "request": [
            [i.group, i.text, i.details]
            for i in session.scalars(
                select(AgentRequestItem)
                .where(AgentRequestItem.submission_id == sid)
                .order_by(AgentRequestItem.ordinal)
            )
        ],
        "draft": [draft.subject, draft.body] if draft else None,
    }


def output_hash(session: Session, sid: str) -> str:
    return _sha(output(session, sid))


def section_hashes(session: Session, sid: str) -> dict[str, str]:
    """One hash per section, to say where two runs differ."""
    return {k: _sha(v) for k, v in output(session, sid).items()}


def stamp(session: Session, run: Run) -> None:
    """Record a finished run's fingerprints."""
    run.input_hash = input_hash(session, run.submission_id)
    run.versions = versions(session, run) | {"sections": section_hashes(session, run.submission_id)}
    run.output_hash = output_hash(session, run.submission_id)
