"""A value read once and never verified decides nothing: no pass rests on it, and a misread
license number never splits one driver in two."""

from lane.rules.engine import _unverified  # pyright: ignore[reportPrivateUsage]
from lane.rules.model import Outcome, RuleResult, Verdict
from lane.rules.service import ruleset
from lane.rules.view import SubmissionView, UnverifiedRead


def _result(outcome: Outcome, rule: str = "INS-01") -> RuleResult:
    spec = next(r for r in ruleset().rules if r.id == rule)
    return RuleResult(spec, Verdict(outcome, "Continuous liability coverage"), [])


def _view(*rules: str) -> SubmissionView:
    return SubmissionView(
        unverified=[
            UnverifiedRead("Quick quote form", "prior_insurance[3].start", frozenset(rules))
        ]
    )


def test_a_pass_resting_on_an_unverified_value_is_unknown() -> None:
    r = _result(Outcome.PASS)
    _unverified(r, _view("INS-01", "DOC-02"))
    assert r.verdict.outcome is Outcome.UNKNOWN
    assert "could not be verified in Quick quote form" in r.verdict.reason
    assert "Confirm prior_insurance[3].start on Quick quote form" in r.verdict.items


def test_a_refer_on_verified_values_stands() -> None:
    r = _result(Outcome.REFER)
    _unverified(r, _view("INS-01"))
    assert r.verdict.outcome is Outcome.REFER


def test_an_unverified_value_another_rule_reads_changes_nothing() -> None:
    r = _result(Outcome.PASS)
    _unverified(r, _view("DOC-02"))
    assert r.verdict.outcome is Outcome.PASS
