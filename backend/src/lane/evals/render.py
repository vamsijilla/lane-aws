"""What the underwriter reads, as text: rule results, conflicts, missing items and the agent
request with its email. Snapshot tests freeze it; the v1-vs-v2 comparison diffs it."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.db.models import (
    AgentRequestItem,
    Conflict,
    EmailDraft,
    MissingInfo,
    RuleOutcomeRecord,
)


def render(db: Session, sid: str) -> dict[str, str]:
    rules = [
        "\n".join(
            [
                f"{r.rule_id} {r.outcome}{' (potential decline)' if r.potential_decline else ''}",
                f"  reason: {r.reason}",
                *(f"  - {i}" for i in r.items),
                *([f"  note: {r.anchor_note}"] if r.anchor_note else []),
                *(
                    f"  if {s['field']} = {s['value']}: {s.get('result') or s['outcome']}"
                    for s in r.sensitivity
                ),
                *(f"  reading {x['label']}: {x['outcome']}" for x in r.readings),
            ]
        )
        for r in db.scalars(
            select(RuleOutcomeRecord)
            .where(RuleOutcomeRecord.submission_id == sid)
            .order_by(RuleOutcomeRecord.ordinal)
        )
    ]
    conflicts = [
        "\n".join(
            [
                f"{c.title} [{c.type}]{' blocks ' + ', '.join(c.blocking_rules) if c.blocking_rules else ''}",
                *(
                    f"  {cand['display']}: "
                    + "; ".join(sorted(s["title"] for s in cand.get("sources", [])))
                    for cand in c.candidates
                ),
                *([f"  hint: {c.hint}"] if c.hint else []),
                *(f"  note: {n}" for n in c.notes),
            ]
        )
        for c in sorted(
            db.scalars(select(Conflict).where(Conflict.submission_id == sid)),
            key=lambda c: c.title,
        )
    ]
    missing = [
        f"{m.code}: {m.detail}"
        for m in db.scalars(
            select(MissingInfo).where(MissingInfo.submission_id == sid).order_by(MissingInfo.code)
        )
    ]
    items = [
        "\n".join([f"[{i.group}] {i.text}", *(f"  - {d}" for d in i.details)])
        for i in db.scalars(
            select(AgentRequestItem)
            .where(AgentRequestItem.submission_id == sid)
            .order_by(AgentRequestItem.ordinal)
        )
    ]
    draft = db.scalars(
        select(EmailDraft)
        .where(EmailDraft.submission_id == sid)
        .order_by(EmailDraft.version.desc())
    ).first()
    return {
        "rules": "\n\n".join(rules) + "\n",
        "conflicts": "\n\n".join(conflicts) + "\n",
        "missing": "\n".join(missing) + "\n",
        "email": "\n\n".join(items) + "\n\n----\n" + (draft.body if draft else "") + "\n",
    }
