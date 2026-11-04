"""The agent request: what to ask or tell the agent, built in code from rule results, missing
information, open conflicts and the findings an underwriter accepted.

Items are worded for the agent (no rule ids, no internal terms) and keep links back to what
raised them, so the UI can show why each item is there.
"""

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from lane.analyze.service import review_is_stale
from lane.config import Settings
from lane.db.models import (
    Conflict,
    FindingRecord,
    MissingInfo,
    ResolvedEntity,
    ResolvedField,
    RuleOutcomeRecord,
)
from lane.resolve.severity import conflict_order, decline_capable, severity
from lane.resolve.tiers import tier_of
from lane.rules.model import Ruleset

GROUP_ORDER = ("before_quote", "before_binding", "inform")
ASKING = {"refer", "unknown", "decline"}  # outcomes that turn a rule's request into an ask


@dataclass
class Item:
    key: str
    group: str
    text: str
    details: list[str] = field(default_factory=lambda: list[str]())
    source_kind: str = "rule"
    source_refs: list[str] = field(default_factory=lambda: list[str]())
    default_on: bool = True  # in the request until a person takes it out
    # The question each detail line belongs to (entity and topic), parallel to details: lines
    # with one key are one point when a reply is measured (lane/actions/points.py)
    keys: list[str] = field(default_factory=lambda: list[str]())

    def add(self, text: str, key: str) -> None:
        """Rules that share a request often name the same thing; keep the more specific
        wording, and the first key."""
        for i, old in enumerate(self.details):
            if _subject(old) == _subject(text):
                if len(text) > len(old):
                    self.details[i] = text
                return
        self.details.append(text)
        self.keys.append(key)


def build_items(
    session: Session, submission_id: str, rules: Ruleset, settings: Settings
) -> list[Item]:
    specs = {r.id: r for r in rules.rules}
    outcomes = list(
        session.scalars(
            select(RuleOutcomeRecord)
            .where(RuleOutcomeRecord.submission_id == submission_id)
            .order_by(RuleOutcomeRecord.ordinal)
        )
    )
    conflicts = list(
        session.scalars(
            select(Conflict).where(Conflict.submission_id == submission_id).order_by(Conflict.title)
        )
    )
    attributes = {
        fid: attr
        for fid, attr in session.execute(
            select(ResolvedField.id, ResolvedField.attribute).where(
                ResolvedField.submission_id == submission_id
            )
        )
    }
    kinds = {
        eid: kind
        for eid, kind in session.execute(
            select(ResolvedEntity.id, ResolvedEntity.kind).where(
                ResolvedEntity.submission_id == submission_id
            )
        )
    }
    conflicts.sort(
        key=lambda c: conflict_order(
            bool(c.blocking_rules),
            severity(
                settings,
                kinds.get(c.entity_id, ""),
                attributes.get(c.field_id, ""),
                bool(set(c.blocking_rules) & decline_capable()),
            ),
            c.title,
        )
    )
    missing_codes = list(
        session.scalars(select(MissingInfo.code).where(MissingInfo.submission_id == submission_id))
    )
    asked_elsewhere = {
        attr
        for attr, prefix in settings.actions_conflicts_asked_by_missing.items()
        if any(code.startswith(prefix) for code in missing_codes)
    }
    items: dict[str, Item] = {}
    covered: list[str] = []
    asked_rules: set[str] = set()

    for r in outcomes:
        spec = specs.get(r.rule_id)
        if spec is None or not spec.request:
            continue
        asks = r.outcome in ASKING or (r.outcome == "flag" and bool(r.items))
        if not asks or (r.outcome == "flag" and spec.missing):
            continue  # a flag that points to missing items is asked there
        folded = conflict_details(
            [
                c
                for c in conflicts
                if settings.actions_conflicts_asked_by_rule.get(attributes.get(c.field_id, ""))
                == spec.id
            ],
            attributes,
            settings.actions_conflict_questions,
        )
        # A conflict this rule's request already asks about (who drives) is asked here, once,
        # and the rule's own lines and the conflict are one question
        own = list(r.items) if spec.request_details else []
        details: list[tuple[str, str]] = [
            (line, f"R:{spec.id}" if folded else f"R:{spec.id}:{_head(line)}") for line in own
        ] + [(text, f"R:{spec.id}") for _key, text in folded]
        if spec.request_source == "conflicts":
            optional = settings.actions_optional_conflicts
            details = [
                (text, key)
                for key, text in conflict_details(
                    [
                        c
                        for c in conflicts
                        if attributes.get(c.field_id) not in asked_elsewhere
                        and attributes.get(c.field_id)
                        not in settings.actions_conflicts_asked_by_rule
                        and attributes.get(c.field_id) not in optional
                    ],
                    attributes,
                    settings.actions_conflict_questions,
                )
            ]
            # One question per small difference, so each reads (and is answered) on its own
            minor = [
                f"Which is right for {_lower(c.title)}: {' or '.join(vals)}?"
                for c in conflicts
                if attributes.get(c.field_id) in optional and not c.resolution
                for vals in [
                    list(
                        dict.fromkeys(
                            str(v.get("display")) for v in c.candidates if not v.get("superseded")
                        )
                    )
                ]
                if len(vals) > 1
            ]
            if minor:
                items["optional:minor"] = Item(
                    "optional:minor",
                    "before_quote",
                    "Optional: confirm these small differences, likely the same thing written two ways",
                    minor,
                    source_kind="rule",
                    source_refs=[f"R:{spec.id}"],
                    default_on=False,
                    keys=["C:minor"] * len(minor),
                )
            if not details:
                continue
        key = f"rule:{spec.request_key or spec.id}"
        item = items.get(key)
        if item is None:
            item = items[key] = Item(key, spec.request_group, spec.request, source_kind="rule")
        for text, dkey in details:
            item.add(text, dkey)
        item.source_refs.append(f"R:{spec.id}")
        covered += spec.covers
        asked_rules.add(spec.id)

    # A rule that holds today but refers by the effective date (an IFTA quarter that comes due)
    # is asked before binding, the way a loss run that will age out is
    for r in outcomes:
        spec = specs.get(r.rule_id)
        if spec is None or not spec.request or spec.id in asked_rules or not r.anchor_items:
            continue
        lines = [f"{line} before binding" for line in r.anchor_items]
        items[f"rule:{spec.id}:binding"] = Item(
            f"rule:{spec.id}:binding",
            "before_binding",
            spec.request,
            lines,
            "rule",
            [f"R:{spec.id}"],
            keys=[f"R:{spec.id}:{_head(line)}" for line in lines],
        )
        asked_rules.add(spec.id)

    by_category: dict[str, Item] = {}
    for m in session.scalars(
        select(MissingInfo)
        .where(MissingInfo.submission_id == submission_id)
        .order_by(MissingInfo.code)
    ):
        if any(m.code.startswith(prefix) for prefix in covered):
            continue
        tier = tier_of(settings, m.code)
        if tier == "helpful":
            key, group, heading = "helpful", "before_quote", settings.actions_helpful_heading
        elif tier == "bind":
            key, group = f"bind:{m.category}", "before_binding"
            heading = settings.actions_bind_headings.get(m.category, "Needed to bind")
        else:
            key, group = m.category, "before_quote"
            heading = settings.actions_missing_headings.get(
                m.category, m.category.replace("_", " ").capitalize()
            )
        item = by_category.get(key)
        if item is None:
            item = by_category[key] = Item(
                f"missing:{key}",
                group,
                heading,
                source_kind="missing",
                default_on=tier != "helpful",
            )
        # Questions left blank are asked as questions
        item.details.append(_as_question(m.detail) if tier == "helpful" else m.detail)
        item.keys.append(f"M:{m.code}")
        item.source_refs.append(m.id)
        # Conflicts this missing item asks about, folded in so they are asked once
        for c in conflicts:
            attr = attributes.get(c.field_id, "")
            prefix = settings.actions_conflicts_asked_by_missing.get(attr)
            # (a conflict on the form's own yes/no question is that same question: not repeated)
            if prefix and m.code.startswith(prefix) and not attr.startswith("q_"):
                for ckey, q in conflict_details(
                    [c], attributes, settings.actions_conflict_questions
                ):
                    if q not in item.details:
                        item.details.append(q)
                        item.keys.append(ckey)
    items.update({i.key: i for i in by_category.values()})
    _merge_by_entity(session, submission_id, items)

    # Recommendations from a review written before the agent's latest reply are not asked again
    stale = review_is_stale(session, submission_id)
    for f in session.scalars(
        select(FindingRecord)
        .where(
            FindingRecord.submission_id == submission_id,
            FindingRecord.review_state == "accepted",
            FindingRecord.kind == "recommendation",
        )
        .order_by(FindingRecord.ordinal)
    ):
        if stale:
            break
        items[f"finding:{f.id}"] = Item(
            f"finding:{f.id}",
            "before_quote",
            f.title,
            [f.body],
            "finding",
            [f.id],
            keys=[f"F:{f.id}"],
        )

    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    # Within a section, what is optional (off until a person adds it) comes last
    return sorted(items.values(), key=lambda i: (order.get(i.group, len(order)), not i.default_on))


_BLANK_ON = re.compile(r"^(?:The )?(.+?) is blank on the (.+?)\.$")
_BLANK = re.compile(r"^(?:The )?(.+?) is blank\.$")
_LEFT_BLANK = re.compile(r"\s*\(left blank on the .+?\)\s*$")


def _as_question(detail: str) -> str:
    """'The first date in business is blank.' -> 'What is the first date in business?'; a form
    question left blank is asked as itself."""
    if m := _BLANK_ON.match(detail):
        return f"Can you fill in the {_lower(m.group(1))} on the {_lower(m.group(2))}?"
    if m := _BLANK.match(detail):
        return f"What is the {_lower(m.group(1))}?"
    if _LEFT_BLANK.search(detail):
        return _LEFT_BLANK.sub("", detail).rstrip(".?") + "?"
    return detail


def _subject(detail: str) -> str:
    """What a detail is about: 'Cargo 2022-2023' for both 'Loss run for Cargo 2022-2023' and
    'Cargo 2022-2023: no loss history sent'."""
    return detail.split(":")[0].removeprefix("Loss run for ").strip().lower()


def _merge_by_entity(session: Session, submission_id: str, items: dict[str, Item]) -> None:
    """Asks about the same person or unit merge into one question. A rule that names someone
    only because they are found in loss history (DRV-02: their CDL experience) adds its ask to
    the question already put about that person, and drops its own line."""
    labels = {
        eid: label
        for eid, label in session.execute(
            select(ResolvedEntity.id, ResolvedEntity.label).where(
                ResolvedEntity.submission_id == submission_id
            )
        )
    }
    asked: dict[str, tuple[Item, int]] = {}  # entity label -> the question about them
    for m in session.scalars(
        select(MissingInfo).where(
            MissingInfo.submission_id == submission_id,
            MissingInfo.code.like("DRV_UNLISTED:%"),
        )
    ):
        label = labels.get(m.entity_id or "")
        for item in items.values():
            if label and m.detail in item.details:
                asked[label] = (item, item.details.index(m.detail))
    for key, item in list(items.items()):
        if item.source_kind != "rule":
            continue
        extra = [d for d in item.details if d in asked]
        if not extra:
            continue
        what = _lower(re.sub(r"\s+for (each|every) \w+$", "", item.text))
        for name in extra:
            target, i = asked[name]
            target.details[i] = target.details[i].replace(
                "send their MVR", f"send their MVR and {what}", 1
            )
            j = item.details.index(name)
            del item.details[j]
            del item.keys[j]
        if not item.details:
            del items[key]  # nothing left that the merged question does not ask


def _head(line: str) -> str:
    """What a rule's line is about, for its point key: '2017 Freightliner (SKR736)' in
    '2017 Freightliner (SKR736): send the stated value', so the point survives a reworded ask."""
    head, colon, _rest = line.partition(":")
    return head.strip() if colon and len(head) <= 60 else line.strip()


def conflict_details(
    conflicts: list[Conflict], attributes: dict[str, str], questions: dict[str, str]
) -> list[tuple[str, str]]:
    """Every open conflict as a plain question with the values the documents show, and its
    point key (entity and field). A rule's own request may touch the same topic (who drives),
    but it does not name the disagreement, so the conflict is always asked."""
    out: list[tuple[str, str]] = []
    for c in conflicts:
        if c.resolution:
            continue  # settled by a person; nothing to ask the agent
        if c.type == "reference":
            continue  # settled by a deterministic reference (the VIN's own decode)
        values = list(
            dict.fromkeys(str(v.get("display")) for v in c.candidates if not v.get("superseded"))
        )
        if len(values) < 2:
            continue
        attr = attributes.get(c.field_id, "")
        key = f"C:{c.entity_id}:{attr}"
        if attr in _MONTH_DAY_ONLY:
            # A date of birth is asked by month and day; the year is personal data, not shown
            values = list(dict.fromkeys(_YEAR.sub("", v) for v in values))
        shown = f"{', '.join(values[:-1])} and {values[-1]}"
        label, _sep, subject = c.title.partition(": ")
        about = f" for {subject}" if subject else ""
        question = questions.get(attr) or f"What is the {_lower(_plain(label))}{{about}}?"
        text = question.replace("{about}", about).replace("{n}", _count_word(len(values)))
        if "{shown}" in text:
            out.append((key, text.replace("{shown}", shown)))
        else:
            out.append((key, f"{text} The documents show {shown}."))
    return out


_MONTH_DAY_ONLY = frozenset({"date_of_birth"})
_YEAR = re.compile(r",?\s*\d{4}$")


def _count_word(n: int) -> str:
    return {2: "two", 3: "three", 4: "four"}.get(n, str(n))


def _plain(label: str) -> str:
    """A field label without the qualifiers code adds: 'Farthest territory (stated)'."""
    return re.sub(r"\s*\([^)]*\)", "", label).strip()


def _lower(label: str) -> str:
    """Lower-case a label's first word unless it is an acronym ('USDOT number' stays)."""
    return label[:1].lower() + label[1:] if label[1:2].islower() else label
