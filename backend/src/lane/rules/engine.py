"""Evaluate the ruleset over resolved facts and decide the next step.

Three safeguards turn rule outcomes into a decision people can trust:
  - Sensitivity: a rule that read a conflicted fact is re-run once per candidate value. If the
    outcome changes, that conflict blocks the rule; if not, the conflict does not matter to it.
  - Decline needs certainty: a decline that rests on a conflicted fact, or that would not hold
    under another candidate, becomes "refer" tagged as a potential decline.
  - Anchor date: rules run counting from the configured anchor (application date by default) and
    again from the other one; any outcome that changes is noted on the rule.
AI output never enters here directly: rules read resolved fields only.
"""

from dataclasses import dataclass, field
from datetime import date
from itertools import product
from typing import Any

from lane.domain.order import natural_key, stable_items
from lane.resolve.unread import names as unread_names
from lane.rules.model import SEVERITY, Outcome, RuleResult, Ruleset, RuleSpec, Verdict
from lane.rules.predicates import REGISTRY, RuleContext, power_unit_counts, route_rows, team_for
from lane.rules.view import SubmissionView

ANCHOR_LABELS = {"application_date": "application date", "effective_date": "effective date"}


@dataclass
class Decision:
    outcome: str  # decline | refer | ready
    headline: str
    team: str | None
    team_label: str | None
    route_reason: str
    route_stable: bool
    potential_decline: bool
    reasons: list[str]
    anchor: str
    anchor_date: str
    ruleset_version: str
    ruleset_sha256: str
    counts: dict[str, int] = field(default_factory=lambda: dict[str, int]())


def pick_anchor(view: SubmissionView, preferred: str, received: date) -> tuple[date, str]:
    order = [preferred] + [a for a in ("application_date", "effective_date") if a != preferred]
    for name in order:
        d = view.date_of("applicant", name)
        if d:
            return d, name
    return received, "received_date"


def _run(spec: RuleSpec, ctx: RuleContext) -> Verdict:
    fn = REGISTRY.get(spec.predicate)
    if fn is None:
        return Verdict(Outcome.UNKNOWN, f"No predicate named {spec.predicate}")
    v = fn(ctx)
    return Verdict(
        v.outcome, v.reason, stable_items(v.items), sorted(set(v.evidence)), v.waiting_on
    )


def evaluate(
    view: SubmissionView,
    ruleset: Ruleset,
    *,
    received: date,
    conflicts: dict[str, str],  # field id -> conflict title
    settled_by_code: list[str] | None = None,  # conflict titles a reference settled (VIN decode)
) -> tuple[list[RuleResult], Decision]:
    params = ruleset.params
    anchor, anchor_key = pick_anchor(view, str(params.get("anchor", "application_date")), received)
    effective = view.date_of("applicant", "effective_date")
    other_key = "effective_date" if anchor_key == "application_date" else "application_date"
    other = view.date_of("applicant", other_key)

    def context(on: date, key: str) -> RuleContext:
        return RuleContext(
            view,
            params,
            on,
            ANCHOR_LABELS.get(key, "received date"),
            effective,
            list(conflicts.values()),
            ruleset.teams,
            list(settled_by_code or []),
        )

    results: list[RuleResult] = []
    final: list[RuleSpec] = []
    for spec in ruleset.rules:
        if spec.predicate == "final_route":  # decided from every other rule, below
            final.append(spec)
            continue
        view.reads, view.detail_facts, view.absent = set(), set(), set()
        verdict = _run(spec, context(anchor, anchor_key))
        result = RuleResult(spec, verdict, sorted(view.reads))
        # What the predicate says it weighed, when it says; else every record it read
        result.evidence = (
            sorted(verdict.evidence) if verdict.evidence else sorted(view.detail_facts)
        )
        result.absent = sorted(view.absent)
        # Kept apart from the verdict, which later steps rebuild; never changes the outcome
        result.waiting_on = [
            {"key": k, "label": label} for k, label in dict.fromkeys(verdict.waiting_on)
        ]
        _unread(result, view)
        _unverified(result, view)
        if spec.predicate == "route_team":
            result.sensitivity = route_rows(context(anchor, anchor_key))
        else:
            _sensitivity(result, view, conflicts, context(anchor, anchor_key))
        _decline_needs_certainty(result, view)
        # The anchor note compares the primary check under each date, before other readings.
        base, base_potential = result.outcome, result.potential_decline
        _readings(result, view, context(anchor, anchor_key))
        if other and other != anchor:
            # The same steps as the primary check, so a conflicted input that leads to the same
            # outcome under every value does not read as "unknown" under the other date.
            view.reads = set()
            other_ctx = context(other, other_key)
            alt_result = RuleResult(spec, _run(spec, other_ctx), sorted(view.reads))
            _sensitivity(alt_result, view, conflicts, other_ctx)
            alt = alt_result.verdict
            if alt.outcome != base and not (base_potential and alt.outcome is Outcome.DECLINE):
                detail = f": {'; '.join(alt.items)}" if alt.items else ""
                result.anchor_note = (
                    f"Counting from the {ANCHOR_LABELS.get(other_key, other_key)}: "
                    f"{alt.outcome.value}. {alt.reason}{detail}"
                )
                if alt.outcome is Outcome.REFER:
                    result.anchor_items = list(alt.items)
        results.append(result)

    decision = _decide(results, view, ruleset, anchor, anchor_key)
    order = {r.id: i for i, r in enumerate(ruleset.rules)}
    for spec in final:
        results.append(_final_route(spec, decision, results))
    results.sort(key=lambda r: order[r.spec.id])
    return results, decision


def _final_route(spec: RuleSpec, decision: "Decision", results: list[RuleResult]) -> RuleResult:
    """ROUTE-02: the route the guideline gives, with the rules that decide it."""
    outcome = {"decline": Outcome.DECLINE, "refer": Outcome.REFER}.get(
        decision.outcome, Outcome.PASS
    )
    reason = decision.headline
    if decision.potential_decline:
        pending = [r.spec.id for r in results if r.potential_decline]
        reason += f". Potential decline pending {', '.join(pending)}"
    # The route sums up the rules; the potential decline belongs to the rule that has it
    # (named in the reason), so the route is never counted or listed as a second one.
    return RuleResult(spec, Verdict(outcome, reason, list(decision.reasons)), [])


def _unread(result: RuleResult, view: SubmissionView) -> None:
    """A refer that rests on something not being found cannot stand if a file that could hold it
    was never read: it is unknown, and says which file."""
    sources = result.spec.sources
    if not sources or result.verdict.outcome is not Outcome.REFER:
        return
    unread = [u for u in view.unread if u.could_hold(sources)]
    if unread:
        v = result.verdict
        result.verdict = Verdict(
            Outcome.UNKNOWN,
            f"{v.reason}; {unread_names(unread)} could not be read and may hold it",
            v.items,
            v.evidence,
        )


def _unverified(result: RuleResult, view: SubmissionView) -> None:
    """A pass (or not applicable) cannot stand while a value the rule reads was read once and
    never verified: what could not be checked might change it. It is unknown, and names the
    values. A refer or decline found on verified values stands."""
    if result.verdict.outcome not in (Outcome.PASS, Outcome.NA):
        return
    reads = [u for u in view.unverified if result.spec.id in u.rules]
    if not reads:
        return
    docs = sorted({f"{u.document} ({u.why})" for u in reads})
    v = result.verdict
    result.verdict = Verdict(
        Outcome.UNKNOWN,
        f"{v.reason}, on the values that could be checked; {len(reads)} "
        f"value{'s' if len(reads) != 1 else ''} it reads could not be verified in "
        f"{'; '.join(docs)}",
        [*v.items, *(f"Confirm {u.path} on {u.document}" for u in reads[:5])],
        v.evidence,
    )


def _sensitivity(
    result: RuleResult, view: SubmissionView, conflicts: dict[str, str], ctx: RuleContext
) -> None:
    """Re-run the rule under each candidate of each conflicted input it read."""
    conflicted = sorted(
        (f for f in result.inputs if f in conflicts),
        key=lambda f: (natural_key(view.fields_by_id[f].label), f),
    )
    for fid in conflicted:
        fv = view.fields_by_id[fid]
        outcomes: list[dict[str, Any]] = []
        for cand in _active(fv.candidates):
            view.overrides = {fid: (cand.get("value"), str(cand.get("display")))}
            view.reads = set()
            alt = _run(result.spec, ctx)
            outcomes.append(
                {
                    "field_id": fid,
                    "field": fv.label,
                    "value": cand.get("display"),
                    "outcome": alt.outcome.value,
                }
            )
        view.overrides = {}
        outcomes.sort(key=lambda o: natural_key(str(o["value"])))
        result.sensitivity.extend(outcomes)
        if len({o["outcome"] for o in outcomes}) > 1:
            result.blocking_conflicts.append(fid)

    # A rule that could not decide because its input is conflicted, but reaches the same outcome
    # under every combination of candidate values, takes that outcome.
    if result.outcome is Outcome.UNKNOWN and conflicted and not result.blocking_conflicts:
        options = [
            [(fid, c) for c in _active(view.fields_by_id[fid].candidates)] for fid in conflicted
        ]
        combos = list(product(*options))[:16]
        verdicts: list[Verdict] = []
        for combo in combos:
            view.overrides = {fid: (c.get("value"), str(c.get("display"))) for fid, c in combo}
            view.reads = set()
            verdicts.append(_run(result.spec, ctx))
        view.overrides = {}
        if (
            verdicts
            and len({v.outcome for v in verdicts}) == 1
            and verdicts[0].outcome is not Outcome.UNKNOWN
        ):
            labels = ", ".join(view.fields_by_id[f].label.lower() for f in conflicted)
            result.verdict = Verdict(
                verdicts[0].outcome,
                f"{verdicts[0].reason}, under every value the documents give for {labels}",
                verdicts[0].items,
                verdicts[0].evidence,
            )


def _active(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in candidates if not c.get("superseded")]


def _decline_needs_certainty(result: RuleResult, view: SubmissionView) -> None:
    """A decline stands only when no conflicted input could change it."""
    if result.outcome is not Outcome.DECLINE or not result.blocking_conflicts:
        return
    labels = sorted({view.fields_by_id[f].label for f in result.blocking_conflicts})
    result.verdict = Verdict(
        Outcome.REFER,
        f"{result.verdict.reason} Potential decline: it depends on {', '.join(labels).lower()}, which the documents disagree on.",
        result.verdict.items,
        result.verdict.evidence,
    )
    result.potential_decline = True


def _readings(result: RuleResult, view: SubmissionView, ctx: RuleContext) -> None:
    """Evaluate every other reading of the rule. When the readings disagree on a decline, the
    decline is not certain: the rule refers as a potential decline and says which reading,
    under which assumption, would decline."""
    for rd in result.spec.readings:
        fn = REGISTRY.get(rd.predicate)
        if fn is None:
            continue
        view.reads = set()
        v = fn(ctx)
        result.readings.append(
            {
                "id": rd.id,
                "label": rd.label,
                "assumption": rd.assumption,
                "plausible": rd.plausible,
                "question": rd.question,
                "outcome": v.outcome.value,
                "reason": v.reason,
                "items": v.items,
            }
        )
    if not result.readings:
        return
    primary = result.outcome
    declining = [r for r in result.readings if r["outcome"] == Outcome.DECLINE.value]
    implausible = [r for r in declining if not r["plausible"]]
    declining = [r for r in declining if r["plausible"]]
    for r in implausible:
        r["outcome"] = Outcome.FLAG.value  # what it produces: an implausible reading only flags
    if primary is Outcome.PASS and implausible and not declining:
        alt = implausible[0]
        tag = f", assumption {alt['assumption']}" if alt["assumption"] else ""
        result.verdict = Verdict(
            Outcome.FLAG,
            f"{result.verdict.reason}. Under the other reading "
            f"({alt['label'][:1].lower()}{alt['label'][1:]}{tag}): {alt['reason']}",
            [*result.verdict.items, *([alt["question"]] if alt["question"] else alt["items"])],
            result.verdict.evidence,
        )
        return
    if primary is not Outcome.DECLINE and declining:
        alt = declining[0]
        tag = f", assumption {alt['assumption']}" if alt["assumption"] else ""
        result.verdict = Verdict(
            Outcome.REFER,
            f"{result.verdict.reason}. Potential decline under the other reading "
            f"({alt['label'][:1].lower()}{alt['label'][1:]}{tag}): {alt['reason']}",
            [*result.verdict.items, *alt["items"]],
            result.verdict.evidence,
        )
        result.potential_decline = True
    elif primary is Outcome.DECLINE and any(
        r["outcome"] != Outcome.DECLINE.value for r in result.readings
    ):
        result.verdict = Verdict(
            Outcome.REFER,
            f"{result.verdict.reason}. Potential decline: another reading of the guideline does not "
            "decline, so an underwriter should confirm which applies",
            result.verdict.items,
            result.verdict.evidence,
        )
        result.potential_decline = True


def _decide(
    results: list[RuleResult], view: SubmissionView, ruleset: Ruleset, anchor: date, anchor_key: str
) -> Decision:
    small_max = int(ruleset.params["small_fleet_max_units"])
    counted = power_unit_counts(view)
    units = [n for n, _ in counted]
    notes = [note for _, note in counted]
    teams = {team_for(u, ruleset.params) for u in units}
    if not units:
        team, stable, route_reason = None, False, "Power unit count is not known"
    elif len(teams) == 1:
        team, stable = teams.pop(), True
        band = f"1-{small_max}" if team == "small_fleet" else f"{small_max + 1}+"
        if len(units) > 1:
            route_reason = (
                f"{band} power units under every count the documents give: {', '.join(notes)}"
            )
        else:
            route_reason = f"{band} power units: {notes[0]}" if notes else f"{band} power units"
    else:
        team, stable = "fleet", False
        route_reason = f"Counts differ across the {small_max}-unit line ({', '.join(notes)}); sent to the larger team"

    counts: dict[str, int] = {}
    for r in results:
        counts[r.outcome.value] = counts.get(r.outcome.value, 0) + 1
    # A07: a check that needs outside data is done by the underwriter at quote; its unknown alone
    # does not refer a submission
    at_quote = [r for r in results if r.spec.external and r.outcome is Outcome.UNKNOWN]
    routed = [r for r in results if r not in at_quote]
    checks = [f"To check at quote: {r.spec.id} {r.spec.title}" for r in at_quote]
    worst = max((r.outcome for r in routed), key=SEVERITY.index, default=Outcome.PASS)
    potential = any(r.potential_decline for r in results)
    team_label = ruleset.teams.get(team) if team else None
    if worst is Outcome.DECLINE:
        outcome = "decline"
        failing = [r for r in results if r.outcome is Outcome.DECLINE]
        headline = "Decline recommended"
        reasons = [f"{r.spec.id} {r.spec.title}: {r.verdict.reason}" for r in failing]
    elif worst in (Outcome.REFER, Outcome.UNKNOWN):
        outcome = "refer"
        headline = f"Refer to {team_label}" if team_label else "Refer to an underwriter"
        reasons = [
            f"{r.spec.id} {r.spec.title}: {r.verdict.reason}"
            for r in sorted(
                routed, key=lambda r: (not r.potential_decline, -SEVERITY.index(r.outcome))
            )
            if r.outcome in (Outcome.REFER, Outcome.UNKNOWN)
        ] + checks
    else:
        outcome = "ready"
        headline = (
            f"Ready for {team_label} review" if team_label else "Ready for underwriter review"
        )
        reasons = checks
    return Decision(
        outcome=outcome,
        headline=headline,
        team=team,
        team_label=team_label,
        route_reason=route_reason,
        route_stable=stable,
        potential_decline=potential,
        reasons=reasons,
        anchor=anchor_key,
        anchor_date=anchor.isoformat(),
        ruleset_version=ruleset.version,
        ruleset_sha256=ruleset.sha256,
        counts=counts,
    )
