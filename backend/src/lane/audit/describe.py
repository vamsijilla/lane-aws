"""Audit events in words: who or what acted, and one plain sentence about it, for the timeline.

The chain stores raw payloads; this only describes them. Values of personal-data fields pass
through `mask(attribute, value)` so a resolved license number never appears in the timeline."""

from collections.abc import Callable
from typing import Any, Literal, cast

from lane.api.display import model_label

Kind = Literal["human", "security", "model", "system"]
Masker = Callable[[str | None, Any], str]

_TYPES = {
    "mvr": "motor vehicle record",
    "ifta_return": "IFTA return",
    "email_body": "email body",
}


def _label(doc_type: Any) -> str:
    text = str(doc_type or "document")
    return _TYPES.get(text, text.replace("_", " "))


def _short(h: Any) -> str:
    return str(h)[:12] if h else "none"


def kind(action: str, actor: str, payload: dict[str, Any]) -> Kind:
    if action.startswith("security.") or action == "pii.revealed":
        return "security"
    if actor.startswith("user:"):
        return "human"
    if action in ("submission.analyzed", "document.extracted") and payload.get("model"):
        return "model"
    return "system"


def summary(action: str, payload: dict[str, Any], mask: Masker) -> str:
    p = payload
    attribute = p.get("attribute")
    match action:
        case "email.received":
            return f"Email received from {p.get('from')}: {p.get('subject')}"
        case "email.resent":
            return "The same email arrived again; the existing submission was kept"
        case "intake.completed":
            return (
                f"Intake finished: {p.get('files', 0)} files, {p.get('duplicates', 0)} duplicates "
                f"merged, {p.get('rejected', 0)} rejected"
            )
        case "attachment.accepted":
            return f"Accepted {p.get('filename')}"
        case "attachment.duplicate":
            return f"{p.get('filename')} was sent twice; the copy was merged"
        case "attachment.expanded":
            return f"Opened the archive {p.get('filename')}"
        case "attachment.rejected" | "attachment.unsupported":
            return f"{p.get('filename')} was not read: {p.get('reason') or 'unsupported'}"
        case "attachment.triaged":
            n = p.get("pages", 0)
            return f"Read {p.get('filename')}: {n} {'page' if n == 1 else 'pages'}"
        case "attachment.unreadable" | "attachment.damaged":
            return f"{p.get('filename')}: {p.get('reason')}"
        case "document.classified":
            pages = cast(list[Any], p.get("pages") or [])
            where = f"pages {pages[0]} to {pages[1]}" if len(pages) == 2 else "pages unknown"
            text = f"Recognised a {_label(p.get('doc_type'))} ({where}, {p.get('confidence')})"
            return text + (", the filename says otherwise" if p.get("filename_mismatch") else "")
        case "document.extracted":
            # Named by its document; a status other than a clean read is said after it
            what = f" from {p['title']}" if p.get("title") else ""
            status = p.get("status")
            tail = f" ({str(status).replace('_', ' ')})" if status and status != "ok" else ""
            return f"Extracted {p.get('facts', 0)} facts{what}{tail}"
        case "submission.resolved":
            fields = cast(dict[str, int], p.get("fields") or {})
            return (
                f"Reconciled the documents: {fields.get('known', 0)} values known, "
                f"{fields.get('conflict', 0)} in conflict, "
                f"{fields.get('missing', 0)} required fields not provided"
            )
        case "submission.decided":
            pd = ", potential decline" if p.get("potential_decline") else ""
            return f"{p.get('headline')}{pd} (rules {p.get('ruleset')})"
        case "submission.analyzed":
            return (
                f"Expert review on {model_label(str(p.get('model')))}: {p.get('findings', 0)} findings, "
                f"${float(p.get('cost_usd') or 0):.2f}"
            )
        case "analysis.approved":
            return f"Approved the expert review, about ${float(p.get('estimate_usd') or 0):.2f}"
        case "escalation.approved":
            return "Approved re-reading a document with the stronger model"
        case "agent_request.drafted":
            return (
                f"Drafted the agent request, version {p.get('version')}, "
                f"{p.get('items', 0)} items, not sent"
            )
        case "agent_request.item_included" | "agent_request.item_excluded":
            verb = "Included" if action.endswith("included") else "Left out"
            return f"{verb} in the agent request: {p.get('text')}"
        case "run.started":
            return f"Run started ({p.get('trigger')}): {', '.join(p.get('steps') or [])}"
        case "run.finished":
            return f"Run {p.get('status')}, output {_short(p.get('output_hash'))}"
        case "run.interrupted":
            return str(p.get("reason") or "The run was interrupted")
        case "run.replayed":
            if p.get("identical"):
                return f"Replay: output identical ({_short(p.get('after_hash'))})"
            if p.get("not_cached"):
                return f"Replay: {p.get('not_cached')} model answers were not in the cache"
            return f"Replay: output differs in {', '.join(p.get('differences') or [])}"
        case "conflict.resolved":
            return (
                f"Resolved {p.get('conflict')} to {mask(attribute, p.get('chosen'))}: "
                f"{p.get('reason')}"
            )
        case "conflict.reopened":
            return f"Reopened {p.get('conflict')}"
        case "field.overridden":
            return (
                f"Corrected {p.get('field')} from {mask(attribute, p.get('before'))} to "
                f"{mask(attribute, p.get('after'))}: {p.get('reason')}"
            )
        case "field.override_cleared":
            return f"Removed the correction to {p.get('field')}"
        case "document.relabeled":
            return f"Labelled {p.get('title')} as {p.get('to')} (was {p.get('from')}): {p.get('reason')}"
        case "document.label_cleared":
            return f"Removed the label on {p.get('title')}"
        case "pii.revealed":
            return f"Revealed {p.get('field')}"
        case "security.injection_detected":
            return (
                f"Instructions found in {p.get('filename')}, page {p.get('page')}; "
                "treated as data and ignored"
            )
        case _ if action.startswith("finding."):
            state = action.removeprefix("finding.")
            verb = {"accepted": "Accepted", "dismissed": "Dismissed", "open": "Reopened"}.get(
                state, state.capitalize()
            )
            reason = f": {p.get('reason')}" if p.get("reason") else ""
            return f"{verb} the finding {p.get('title')}{reason}"
        case _ if action.startswith("step."):
            ms = p.get("duration_ms")
            took = f" in {ms / 1000:.1f} s" if isinstance(ms, int | float) else ""
            return f"Step {p.get('name')} {action.removeprefix('step.')}{took}"
        case _:
            return action.replace(".", " ").replace("_", " ").capitalize()
