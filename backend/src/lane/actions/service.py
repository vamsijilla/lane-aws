"""Build and persist the agent request and its draft email."""

from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from lane.actions.build import Item, build_items
from lane.actions.email import Draft, Parties, draft_email, items_sha
from lane.audit import chain
from lane.config import Settings
from lane.db.models import (
    AgentRequestItem,
    EmailDraft,
    ResolvedEntity,
    ResolvedField,
    Submission,
)
from lane.llm.gateway import GatewayFactory
from lane.resolve.service import stable_id
from lane.rules.service import ruleset

ACTOR = "system:actions"


@dataclass
class ActionsResult:
    items: list[Item]
    draft: EmailDraft
    redrafted: bool
    cost_usd: float
    problems: list[str]


def _value(session: Session, submission_id: str, kind: str, attribute: str) -> str | None:
    row = session.execute(
        select(ResolvedField.value_display)
        .join(ResolvedEntity, ResolvedEntity.id == ResolvedField.entity_id)
        .where(
            ResolvedField.submission_id == submission_id,
            ResolvedEntity.kind == kind,
            ResolvedField.attribute == attribute,
            ResolvedField.state.in_(("known", "invalid")),
        )
    ).first()
    return row[0] if row else None


def included_sha(session: Session, submission_id: str) -> str:
    """Fingerprint of the items currently included, to tell whether the draft is current."""
    rows = session.scalars(
        select(AgentRequestItem)
        .where(AgentRequestItem.submission_id == submission_id, AgentRequestItem.included)
        .order_by(AgentRequestItem.ordinal)
    )
    return items_sha([Item(r.id, r.group, r.text, list(r.details)) for r in rows])


def parties(session: Session, submission: Submission) -> Parties:
    sid = submission.id
    return Parties(
        applicant=_value(session, sid, "applicant", "legal_name") or "the applicant",
        agent_name=_value(session, sid, "agency", "contact"),
        agent_email=_value(session, sid, "agency", "email") or submission.email.from_addr,
    )


def build_actions(
    session: Session,
    submission: Submission,
    settings: Settings,
    gateway_factory: GatewayFactory,
    *,
    run_id: str | None,
    redraft: bool = True,
) -> ActionsResult:
    rules = ruleset()
    built = build_items(session, submission.id, rules, settings)

    existing = {
        i.id: i
        for i in session.scalars(
            select(AgentRequestItem).where(AgentRequestItem.submission_id == submission.id)
        )
    }
    session.execute(delete(AgentRequestItem).where(AgentRequestItem.submission_id == submission.id))
    session.flush()
    for n, item in enumerate(built, start=1):
        iid = stable_id(submission.id, item.key)
        previous = existing.get(iid)
        session.add(
            AgentRequestItem(
                id=iid,
                submission_id=submission.id,
                ordinal=n,
                group=item.group,
                text=item.text,
                details=item.details,
                detail_keys=item.keys,
                source_kind=item.source_kind,
                source_refs=item.source_refs,
                # a person's choice stays; a new item starts as its tier says
                included=previous.included if previous else item.default_on,
            )
        )
    session.flush()

    def on(b: Item) -> bool:
        previous = existing.get(stable_id(submission.id, b.key))
        return previous.included if previous else b.default_on

    included = [b for b in built if on(b)]
    latest = session.scalars(
        select(EmailDraft)
        .where(EmailDraft.submission_id == submission.id)
        .order_by(EmailDraft.version.desc())
    ).first()
    sha = items_sha(included)
    if latest is not None and (latest.items_sha256 == sha or not redraft):
        session.commit()
        return ActionsResult(built, latest, False, 0.0, list(latest.problems))

    draft: Draft = draft_email(
        session,
        submission.id,
        included,
        parties(session, submission),
        rules.request_groups,
        settings,
        gateway_factory,
        run_id=run_id,
    )
    version = (
        session.scalar(
            select(func.max(EmailDraft.version)).where(EmailDraft.submission_id == submission.id)
        )
        or 0
    ) + 1
    row = EmailDraft(
        submission_id=submission.id,
        version=version,
        to=draft.to,
        subject=draft.subject,
        body=draft.body,
        method=draft.method,
        model=draft.model,
        items_sha256=draft.items_sha256,
        problems=draft.problems,
    )
    session.add(row)
    chain.record(
        session,
        actor=ACTOR,
        action="agent_request.drafted",
        subject_type="submission",
        subject_id=submission.id,
        payload={
            "items": len(included),
            "version": version,
            "method": draft.method,
            "sent": False,
        },
    )
    session.commit()
    return ActionsResult(built, row, True, draft.cost_usd, draft.problems)
