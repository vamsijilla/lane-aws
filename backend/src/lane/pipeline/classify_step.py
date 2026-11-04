"""Pipeline step: classify every page, split files into logical documents, mark supersession."""

import hashlib
from collections import Counter
from typing import Any

from sqlalchemy import delete, select, update

from lane.audit import chain
from lane.db.models import (
    Attachment,
    Blob,
    ConfidenceBand,
    Document,
    DocumentExtraction,
    DocumentLabel,
    EscalationApproval,
    Fact,
    Page,
    PageClassification,
    PageKind,
    StepStatus,
)
from lane.documents import layouts, templates, tier1
from lane.documents.classify import (
    CLASSIFIER_VERSION,
    PageInput,
    audited,
    build_request,
    email_body_call,
    fuse,
    fuse_layout,
    fuse_tier1,
    layout_signals,
    page_text,
    settled_by_layout,
    settled_by_tier1,
)
from lane.documents.formats import format_note, same_family
from lane.documents.layout import load_layout
from lane.documents.signals import filename_hint
from lane.documents.split import LogicalDoc, PageCall, mark_supersession, split_pages
from lane.documents.templates import Match
from lane.documents.tier1 import Decision
from lane.documents.types import DOC_TYPE_LABELS, DocType, PageVerdict
from lane.llm.gateway import LlmResult, LlmUnavailable
from lane.pipeline.runner import ACTOR, StepContext, StepOutcome


def classify_step(ctx: StepContext) -> StepOutcome:
    session, store, settings = ctx.session, ctx.store, ctx.settings
    rows = session.execute(
        select(Page, Attachment)
        .join(Attachment, Page.attachment_id == Attachment.id)
        .where(Page.submission_id == ctx.submission.id)
        .order_by(Attachment.ordinal, Page.page_no)
    ).all()
    page_counts = Counter(att.id for _, att in rows)

    def blob_bytes(sha: str | None) -> bytes | None:
        if sha is None:
            return None
        blob = session.get(Blob, sha)
        return store.get(blob.storage_key) if blob else None

    inputs: list[PageInput] = []
    for page, att in rows:
        if page.kind is PageKind.EMAIL_BODY:
            continue
        raw_layout = blob_bytes(page.layout_sha256)
        layout = load_layout(raw_layout) if raw_layout else None
        is_image = page.kind in (PageKind.SCAN, PageKind.PHOTO)
        inputs.append(
            PageInput(
                page=page,
                filename=att.filename,
                page_count=page_counts[att.id],
                text=page_text(page, layout, settings),
                image_png=blob_bytes(page.render_sha256)
                if is_image or page.kind is PageKind.TEXT
                else None,
                layout=layout,
            )
        )

    # Tier 1, deterministic signals, before anything learned or any model.
    decisions: dict[str, Decision] = {}
    for item in inputs:
        d = tier1.decide(
            text=item.text,
            layout=item.layout,
            is_sheet=item.page.kind is PageKind.SHEET,
            sheet_name=item.page.sheet_name,
            sheet_hidden=item.page.sheet_hidden,
        )
        if d is not None:
            decisions[item.page.id] = d

    # Tier 2, known layouts: a complete, unchallenged match types the page with no model call.
    # Learned only from documents a person confirmed (lane.documents.layouts); none ship
    known = layouts.active(session) if settings.templates_enabled else []
    matches: dict[str, Match] = {}
    for item in inputs:
        m = templates.match_page(
            known, text=item.text, layout=item.layout, sheet_name=item.page.sheet_name
        )
        if m is not None:
            matches[item.page.id] = m
    settled = {
        item.page.id
        for item in inputs
        if settled_by_tier1(item, decisions.get(item.page.id))
        or (
            settled_by_layout(item, matches.get(item.page.id))
            and not audited(item.page.id, settings.templates_audit_rate)
        )
    }
    to_model = [item for item in inputs if item.page.id not in settled]

    # Ask the model. If it is unavailable, keep going on keywords alone.
    results: dict[str, LlmResult[PageVerdict]] = {}
    llm_note: str | None = None
    try:
        gateway = ctx.llm_factory(settings)
        requests = [build_request(item, settings) for item in to_model]
        for result in gateway.run_many(session, requests, run_id=ctx.run.id):
            assert result.request.subject_id is not None
            results[result.request.subject_id] = result
        llm_stats: dict[str, Any] = {
            "tier1_pages": len(decisions),
            "layout_pages": len(settled)
            - sum(1 for i in inputs if settled_by_tier1(i, decisions.get(i.page.id))),
            "calls_avoided": len(settled),
            "calls": gateway.stats.calls,
            "cache_hits": gateway.stats.cache_hits,
            "failures": gateway.stats.failures,
            "cost_usd": gateway.stats.cost_usd,
        }
    except LlmUnavailable as exc:
        llm_note = f"AI classification unavailable: {exc}"
        llm_stats = {"layout_pages": len(settled), "calls": 0, "unavailable": str(exc)}

    calls: dict[str, PageCall] = {}
    methods: dict[str, str] = {}
    for item in inputs:
        result = results.get(item.page.id)
        verdict = result.output if result else None
        error = result.error if result else (llm_note if item.page.id not in settled else None)
        match = matches.get(item.page.id)
        decision = decisions.get(item.page.id)
        if decision is not None:
            call, vote = fuse_tier1(item, decision, match, verdict, error)
            extra: dict[str, Any] = {"method": "tier1", **decision.signals}
            if match is not None:
                extra["layout"] = layout_signals(match, verdict)
        elif match is not None:
            call, vote = fuse_layout(item, match, verdict, error)
            extra = layout_signals(match, verdict)
            if verdict is not None:  # the model re-checked this layout's page
                agrees = verdict.doc_type == match.template.doc_type.value or (
                    verdict.doc_type in {t.value for t in match.template.also_reads_as}
                )
                layouts.record_audit(
                    session,
                    match.template.id,
                    agrees=agrees,
                    page_id=item.page.id,
                    model_type=verdict.doc_type,
                )
        else:
            call, vote = fuse(item, verdict, error)
            extra = {"method": "ai" if verdict else "keywords"}
        calls[item.page.id] = call
        methods[item.page.id] = str(extra["method"])
        session.merge(
            PageClassification(
                page_id=item.page.id,
                classifier_version=CLASSIFIER_VERSION,
                model_output=verdict.model_dump(mode="json") if verdict else None,
                signals={
                    "keyword_vote": vote.doc_type.value if vote.doc_type else None,
                    "keyword_scores": vote.scores,
                    "reasons": list(call.reasons),
                    **extra,
                },
                llm_call_id=result.call.id if result else None,
                error=error if verdict is None else None,
            )
        )
    for page, _ in rows:
        if page.kind is PageKind.EMAIL_BODY:
            calls[page.id] = email_body_call(page)

    ordered = [calls[page.id] for page, _ in rows]
    docs = split_pages(ordered)
    mark_supersession(docs)
    saved = _save_documents(ctx, docs, {att.id: att for _, att in rows})
    session.commit()
    for doc, d in zip(docs, saved, strict=True):
        ctx.emit(
            "document.found",
            document_id=d.id,
            attachment_id=d.attachment_id,
            filename=d.attachment.filename,
            doc_type=d.doc_type,
            title=d.title,
            pages=[d.page_from, d.page_to],
            filename_hint=d.filename_hint,
            filename_mismatch=d.filename_mismatch,
            superseded=d.superseded_by_id is not None,
            known_layout=all(methods.get(pc.page_id) == "layout" for pc in doc.pages),
        )

    types = Counter(d.doc_type for d in saved)
    mismatches = [
        {"filename": d.attachment.filename, "reads_as": d.doc_type, "hint": d.filename_hint}
        for d in saved
        if d.filename_mismatch
    ]
    superseded = [d.title for d in saved if d.superseded_by_id]
    failures = sum(1 for r in results.values() if not r.ok)
    degraded = llm_note is not None or failures > 0
    return StepOutcome(
        status=StepStatus.DEGRADED if degraded else StepStatus.SUCCEEDED,
        summary={
            "documents": len(saved),
            "types": dict(types),
            "filename_mismatches": mismatches,
            "superseded": superseded,
            "needs_review": sum(1 for d in saved if d.needs_review),
            "llm": llm_stats,
        },
    )


def document_id(submission_id: str, key: str) -> str:
    """Stable across reruns, so later records can keep pointing at the same document."""
    return hashlib.sha256(f"{submission_id}:{key}".encode()).hexdigest()[:32]


def _save_documents(
    ctx: StepContext, docs: list[LogicalDoc], attachments: dict[str, Attachment]
) -> list[Document]:
    session = ctx.session
    sub_id = ctx.submission.id
    ids = {doc.key: document_id(sub_id, doc.key) for doc in docs}

    # Upsert by stable id. Documents that no longer exist (a different split) are removed with
    # everything derived from them; surviving documents keep their facts and approvals.
    session.execute(update(Page).where(Page.submission_id == sub_id).values(document_id=None))
    existing = {
        d.id: d for d in session.scalars(select(Document).where(Document.submission_id == sub_id))
    }
    for d in existing.values():
        d.superseded_by_id = None
    session.flush()
    gone = [i for i in existing if i not in set(ids.values())]
    if gone:
        for model in (Fact, DocumentExtraction, EscalationApproval):
            session.execute(delete(model).where(model.document_id.in_(gone)))
        session.execute(delete(Document).where(Document.id.in_(gone)))
        session.flush()

    # A filename "mismatches" when no document inside the file is what the name suggests.
    by_attachment: dict[str, list[LogicalDoc]] = {}
    for doc in docs:
        by_attachment.setdefault(doc.attachment_id, []).append(doc)
    hints: dict[str, DocType | None] = {}
    mismatch: dict[str, bool] = {}
    for att_id, group in by_attachment.items():
        hint = filename_hint(attachments[att_id].filename)
        hints[att_id] = hint
        mismatch[att_id] = hint is not None and all(
            d.doc_type != hint and not same_family(ctx.settings, d.doc_type, hint) for d in group
        )

    labels = {
        lb.document_id: lb
        for lb in session.scalars(
            select(DocumentLabel).where(DocumentLabel.submission_id == sub_id)
        )
    }
    saved: list[Document] = []
    for ordinal, doc in enumerate(docs, start=1):
        hint = hints[doc.attachment_id]
        review = sorted({r for p in doc.pages for r in p.needs_review} | set(doc.review))
        reasons = sorted({r for p in doc.pages for r in p.reasons})
        note = format_note(ctx.settings, hint.value if hint else None, doc.doc_type)
        if note:
            reasons.append(note)
        if mismatch[doc.attachment_id] and hint:
            reasons.append(
                f"Filename suggests {DOC_TYPE_LABELS[hint].lower()}; content reads as "
                f"{DOC_TYPE_LABELS[DocType(doc.doc_type)].lower()}"
            )
        record = existing.get(ids[doc.key]) or Document(id=ids[doc.key], submission_id=sub_id)
        record.attachment_id = doc.attachment_id
        record.ordinal = ordinal
        record.doc_type = doc.doc_type
        record.page_from = doc.first.page_no
        record.page_to = doc.pages[-1].page_no
        record.title = doc.first.title
        record.subject_id = doc.best("subject_id")
        record.subject_name = doc.best("subject_name")
        record.period = doc.best("period")
        record.document_date = doc.best("document_date")
        record.confidence = doc.confidence
        record.confidence_reasons = reasons
        record.filename_hint = hint.value if hint else None
        record.filename_mismatch = mismatch[doc.attachment_id]
        record.needs_review = bool(review)
        record.review_reasons = review
        record.classifier_version = CLASSIFIER_VERSION
        if label := labels.get(record.id):  # a person said what this document is
            record.doc_type = label.doc_type
            record.confidence = ConfidenceBand.HIGH
            record.confidence_reasons = [
                f"Labelled by {label.actor.removeprefix('user:')}: {label.reason}"
            ]
            record.needs_review = False
            record.review_reasons = []
        record.attachment = attachments[doc.attachment_id]
        session.add(record)
        saved.append(record)
    session.flush()
    for doc, record in zip(docs, saved, strict=True):
        if doc.superseded_by:
            record.superseded_by_id = ids[doc.superseded_by]
        session.execute(
            update(Page)
            .where(Page.id.in_([p.page_id for p in doc.pages]))
            .values(document_id=record.id)
        )
        chain.record(
            session,
            actor=ACTOR,
            action="document.classified",
            subject_type="document",
            subject_id=record.id,
            payload={
                "submission_id": sub_id,
                "attachment_id": record.attachment_id,
                "doc_type": record.doc_type,
                "pages": [record.page_from, record.page_to],
                "confidence": record.confidence.value,
                "filename_mismatch": record.filename_mismatch,
                "superseded_by": record.superseded_by_id,
                "needs_review": record.needs_review,
            },
        )
    session.flush()
    return saved
