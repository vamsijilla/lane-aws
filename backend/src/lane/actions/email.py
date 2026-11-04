"""The draft email to the agent. Nothing here sends mail.

The model only words the opening, the closing and one sentence per request item, from item
headlines. Details (names, dates, VINs) are added by code under each item, so they never go to
the model and cannot be changed by it. Code checks the result: every item is present and no
internal terms leak. Without a model, a plain template produces the same structure.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field

from pydantic import BaseModel
from sqlalchemy.orm import Session

from lane.actions.build import Item
from lane.config import Settings
from lane.intake.link import tag_subject
from lane.llm.gateway import GatewayFactory, LlmRequest, LlmUnavailable, TextPart
from lane.llm.prompts import load_prompt

PROMPT = "draft_agent_email"
_INTERNAL = re.compile(r"\b[A-Z]{2,5}-\d{2}\b|\brules? engine\b|\bpredicate\b", re.IGNORECASE)


class ItemWording(BaseModel):
    id: str
    text: str


class DraftOut(BaseModel):
    subject: str
    opening: str
    items: list[ItemWording]
    closing: str


@dataclass
class Draft:
    to: str | None
    subject: str
    body: str
    method: str  # model | template
    model: str | None
    items_sha256: str
    cost_usd: float = 0.0
    problems: list[str] = field(default_factory=lambda: list[str]())


@dataclass
class Parties:
    applicant: str
    agent_name: str | None
    agent_email: str | None


def items_sha(items: list[Item]) -> str:
    material = [(i.group, i.text, i.details) for i in items]
    return hashlib.sha256(json.dumps(material).encode()).hexdigest()


def _template(items: list[Item], parties: Parties) -> DraftOut:
    return DraftOut(
        subject=f"{parties.applicant}: information needed before we can quote",
        opening=(
            f"Thank you for the submission for {parties.applicant.rstrip('.')}. "
            "We have reviewed it and need the items below to continue."
        ),
        items=[
            ItemWording(id=str(n), text=i.text.rstrip(".") + ".") for n, i in enumerate(items, 1)
        ],
        closing="Once we have these, we will complete our review and come back to you.",
    )


def _unnamed(text: str, first: str) -> str:
    """The greeting names the agent; the opening and closing do not name them again
    ('Thank you for the submission, Daniel.' -> 'Thank you for the submission.')."""
    if not first:
        return text
    name = re.escape(first)
    text = re.sub(rf",\s*{name}(?=[.!?,])", "", text)
    text = re.sub(rf"^{name},\s*(\w)", lambda m: m.group(1).upper(), text)
    return text


def render(
    out: DraftOut, items: list[Item], parties: Parties, groups: dict[str, str], signature: str
) -> tuple[str, list[str]]:
    """Assemble the email body; returns it with any problems found in the model's wording."""
    problems: list[str] = []
    wording = {w.id.strip(): w.text.strip() for w in out.items}
    first = parties.agent_name.split()[0] if parties.agent_name else ""
    lines = [
        f"Hi {first}," if first else "Hello,",
        "",
        _unnamed(out.opening.strip(), first),
        "",
    ]
    n = 0
    for group in dict.fromkeys(i.group for i in items):
        lines += [groups.get(group, group.replace("_", " ").capitalize()), ""]
        for idx, item in enumerate(items, 1):
            if item.group != group:
                continue
            n += 1
            text = wording.get(str(idx))
            if not text:
                problems.append(f"Item {idx} was missing from the draft; used the plain wording")
                text = item.text.rstrip(".") + "."
            elif _INTERNAL.search(text):
                problems.append(f"Item {idx} mentioned internal terms; used the plain wording")
                text = item.text.rstrip(".") + "."
            lines.append(f"{n}. {text}")
            lines += [f"   - {d}" for d in item.details]
        lines.append("")
    lines += [_unnamed(out.closing.strip(), first), "", "Thank you,", signature]
    body = "\n".join(lines)
    if _INTERNAL.search(out.opening + out.closing + out.subject):
        problems.append("The opening, closing or subject mentioned internal terms")
    return body, problems


def draft_email(
    session: Session,
    submission_id: str,
    items: list[Item],
    parties: Parties,
    groups: dict[str, str],
    settings: Settings,
    gateway_factory: GatewayFactory,
    *,
    run_id: str | None,
) -> Draft:
    sha = items_sha(items)
    template = _template(items, parties)
    out, method, model, cost = template, "template", None, 0.0
    problems: list[str] = []
    try:
        gateway = gateway_factory(settings)
        listing = "\n".join(
            f"{n}. [{groups.get(i.group, i.group)}] {i.text}" for n, i in enumerate(items, 1)
        )
        request = LlmRequest(
            purpose="draft_email",
            subject_id=submission_id,
            model=settings.llm_draft_model,
            effort=settings.llm_draft_effort,
            prompt=load_prompt(PROMPT),
            parts=[
                TextPart(
                    f"Applicant: {parties.applicant}\nAgent: {parties.agent_name or 'unknown'}"
                ),
                TextPart(listing, label="request items"),
            ],
            output_type=DraftOut,
            max_tokens=settings.llm_draft_max_tokens,
        )
        [result] = gateway.run_many(session, [request], run_id=run_id)
        cost = result.call.cost_usd
        if result.output is not None:
            out, method, model = result.output, "model", settings.llm_draft_model
        else:
            problems.append(f"Drafting model unavailable ({result.error}); used the template")
    except LlmUnavailable as exc:
        problems.append(f"Drafting model unavailable ({exc}); used the template")

    body, render_problems = render(out, items, parties, groups, settings.actions_signature)
    subject = out.subject.strip()
    if _INTERNAL.search(subject):
        subject = template.subject
    subject = tag_subject(subject, submission_id)  # how the agent's reply finds its way back
    return Draft(
        parties.agent_email, subject, body, method, model, sha, cost, problems + render_problems
    )
