"""What the agent request actually asks for each rule: the included lines that come from the rule
itself, from a missing item the rule points to, or from the question about a person or unit the
rule waits on. A rule's own request text is shown only when one of these lines exists."""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.db.models import AgentRequestItem, MissingInfo, ResolvedEntity, RuleOutcomeRecord
from lane.rules.service import ruleset


def asks_by_rule(session: Session, submission_id: str) -> dict[str, list[tuple[str, list[str]]]]:
    """Per rule, the request items that ask for it, each once with the lines under it:
    [(request text, [detail, ...])]. A missing-list question stands alone (no details)."""
    flat = _lines_by_rule(session, submission_id)
    out: dict[str, list[tuple[str, list[str]]]] = {}
    for rid, lines in flat.items():
        grouped: dict[str, list[str]] = {}
        for head, detail in lines:
            grouped.setdefault(head, [])
            if detail and detail not in grouped[head]:
                grouped[head].append(detail)
        out[rid] = list(grouped.items())
    return out


def _lines_by_rule(session: Session, submission_id: str) -> dict[str, list[tuple[str, str]]]:
    specs = {s.id: s for s in ruleset().rules}
    missing = {
        m.code: m
        for m in session.scalars(
            select(MissingInfo).where(MissingInfo.submission_id == submission_id)
        )
    }
    entities = {
        e.id: f"{e.kind}:{e.label}"
        for e in session.scalars(
            select(ResolvedEntity).where(ResolvedEntity.submission_id == submission_id)
        )
    }
    waits: dict[str, set[str]] = {
        r.rule_id: {w["key"] for w in r.waiting_on}
        for r in session.scalars(
            select(RuleOutcomeRecord).where(RuleOutcomeRecord.submission_id == submission_id)
        )
    }

    def rules_for_missing(code: str) -> set[str]:
        found = {rid for rid, s in specs.items() if any(code.startswith(p) for p in s.missing)}
        m = missing.get(code)
        key = entities.get(m.entity_id or "") if m else None
        if key:  # the question about a person or unit, for every rule waiting on them
            found |= {rid for rid, keys in waits.items() if key in keys}
        return found

    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for item in session.scalars(
        select(AgentRequestItem)
        .where(AgentRequestItem.submission_id == submission_id, AgentRequestItem.included)
        .order_by(AgentRequestItem.ordinal)
    ):
        own = {ref[2:] for ref in item.source_refs if ref.startswith("R:")}
        if not item.details:
            for rid in own:
                out[rid].append((item.text, ""))
            continue
        for i, line in enumerate(item.details):
            key = item.detail_keys[i] if i < len(item.detail_keys) else ""
            if key.startswith("M:"):
                rids = rules_for_missing(key[2:])
            elif key.startswith("R:"):
                # Rules that share one request (loss runs: DOC-02 and INS-02) share its lines
                rids = {key.split(":")[1]} | own
            else:  # a conflict folded into a rule's request: that rule asks it
                rids = own
            # A missing-list question is its own line; a rule's line sits under its request
            shown = (line, "") if item.source_kind == "missing" else (item.text, line)
            for rid in rids:
                if shown not in out[rid]:
                    out[rid].append(shown)
    return dict(out)
