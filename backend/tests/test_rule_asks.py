"""Every rule the documents cannot answer yet is asked in the agent request, and a rule shows only
the lines the request actually holds. The sample, rebuilt from its fixture, no models."""

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from lane.actions.asks import asks_by_rule
from lane.db.models import AgentRequestItem, RuleOutcomeRecord
from lane.overview.service import rule_kind
from tests.slice import load_slice, process


def test_every_rule_that_cannot_be_checked_yet_is_asked(db: Session) -> None:
    sid = load_slice(db)
    process(db, sid)
    asked = asks_by_rule(db, sid)
    records = list(
        db.scalars(select(RuleOutcomeRecord).where(RuleOutcomeRecord.submission_id == sid))
    )
    waiting = [r for r in records if rule_kind(r) == "unknown"]
    assert waiting  # the sample has rules waiting on a missing answer
    assert [r.rule_id for r in waiting if not asked.get(r.rule_id)] == []
    # The unit found only in claims is asked its type, and the unit-type rule shows that line
    elig = next(
        r for r in waiting if r.waiting_on and r.waiting_on[0]["key"].startswith("vehicle:")
    )
    assert any("tractor or a straight truck" in text for text, _details in asked[elig.rule_id])


def test_a_line_taken_out_of_the_request_is_not_shown_as_asked(db: Session) -> None:
    sid = load_slice(db)
    process(db, sid)
    before = asks_by_rule(db, sid)
    rule_id = next(rid for rid, lines in before.items() if lines)
    db.execute(
        update(AgentRequestItem).where(AgentRequestItem.submission_id == sid).values(included=False)
    )
    assert asks_by_rule(db, sid).get(rule_id) is None
