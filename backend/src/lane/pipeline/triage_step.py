"""Pipeline step: triage every readable attachment into pages."""

import enum
import hashlib
from collections import Counter
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from lane.audit import chain
from lane.db.models import (
    Attachment,
    AttachmentRole,
    AttachmentStatus,
    Fact,
    OcrCacheEntry,
    Page,
    PageCacheEntry,
    PageClassification,
    PageKind,
    QualityBand,
    StepStatus,
)
from lane.documents import ocr
from lane.documents.triage import (
    DAMAGED,
    TRIAGE_VERSION,
    PageResult,
    TriageConfig,
    TriageError,
    triage,
)
from lane.intake.sniff import SUPPORTED_KINDS, FileKind
from lane.pipeline.runner import ACTOR, StepContext, StepOutcome
from lane.security import injection
from lane.storage.records import ensure_blob


def triage_step(ctx: StepContext) -> StepOutcome:
    session, store = ctx.session, ctx.store
    cfg = TriageConfig.from_settings(ctx.settings)
    attachments = session.scalars(
        select(Attachment)
        .where(
            Attachment.submission_id == ctx.submission.id,
            Attachment.status == AttachmentStatus.ACCEPTED,
        )
        .order_by(Attachment.ordinal)
    ).all()

    kinds: Counter[str] = Counter()
    quality: Counter[str] = Counter()
    failures: list[dict[str, Any]] = []
    unsupported: list[str] = []
    reused = 0
    same_file = 0

    hidden_spans = 0
    cache = _DbOcrCache(session) if ctx.settings.triage_ocr_cache and not ctx.replay else None
    for att in attachments:
        if att.blob is None:
            continue
        file_kind = FileKind(att.blob.kind)
        if file_kind not in SUPPORTED_KINDS:
            unsupported.append(att.filename)
            att.read_error = f"{file_kind.value} files are not read automatically yet"
            continue

        existing = session.scalars(select(Page).where(Page.attachment_id == att.id)).all()
        reuse = ctx.settings.triage_reuse_pages and not ctx.replay  # a replay reads every page
        cached = _from_cache(session, att) if reuse and not existing else []
        twin = _read_before(session, att) if reuse and not existing and not cached else None
        if (
            existing
            and all(p.triage_version == TRIAGE_VERSION for p in existing)
            and not ctx.replay  # a replay reads every page again
        ):
            reused += 1
            pages = list(existing)
        elif cached:
            # Read before, possibly in a submission since deleted: kept by file and version
            same_file += 1
            pages = [_page_from(session, att, entry.page) for entry in cached]
            session.flush()
            for entry, saved in zip(cached, pages, strict=True):
                if entry.hidden:
                    hidden_spans += injection.record_hidden(
                        session, ctx.submission, saved.id, att.filename, saved.page_no, entry.hidden
                    )
        elif twin:
            # The same file (by sha256) was read before, in any submission: its pages are reused
            same_file += 1
            pages = [_copy_page(session, att, p) for p in twin]
            session.flush()
        else:
            try:
                results = triage(file_kind, store.get(att.blob.storage_key), cfg, cache)
            except TriageError as exc:
                att.read_error = str(exc)
                failures.append(
                    {"attachment_id": att.id, "filename": att.filename, "reason": str(exc)}
                )
                chain.record(
                    session,
                    actor=ACTOR,
                    action="attachment.unreadable",
                    subject_type="attachment",
                    subject_id=att.id,
                    payload={"filename": att.filename, "reason": str(exc)},
                )
                continue
            att.read_error = None
            by_no = {p.page_no: p for p in existing}
            pages = [_save_page(ctx, att, r, by_no.get(r.page_no)) for r in results]
            session.flush()
            if reuse:
                _keep(session, att, pages, results)
            for r, saved in zip(results, pages, strict=True):
                if r.hidden:
                    hidden_spans += injection.record_hidden(
                        session, ctx.submission, saved.id, att.filename, r.page_no, r.hidden
                    )
                    for h in r.hidden:
                        ctx.emit(
                            "security.hidden_text",
                            filename=att.filename,
                            page_no=r.page_no,
                            text=h["text"],
                            reason=h["reason"],
                        )
            _drop_vanished(
                session, [p for no, p in by_no.items() if no not in {r.page_no for r in results}]
            )
            if any(DAMAGED in r.warnings for r in results):
                chain.record(
                    session,
                    actor=ACTOR,
                    action="attachment.damaged",
                    subject_type="attachment",
                    subject_id=att.id,
                    payload={"filename": att.filename, "reason": DAMAGED},
                )
            chain.record(
                session,
                actor=ACTOR,
                action="attachment.triaged",
                subject_type="attachment",
                subject_id=att.id,
                payload={
                    "filename": att.filename,
                    "pages": len(pages),
                    "kinds": dict(Counter(p.kind.value for p in pages)),
                    "quality": dict(Counter(p.quality.value for p in pages)),
                    "triage_version": TRIAGE_VERSION,
                },
            )
        kinds.update(p.kind.value for p in pages)
        quality.update(p.quality.value for p in pages)
        session.commit()
        for p in pages:
            ctx.emit(
                "page.ready",
                attachment_id=att.id,
                filename=att.filename,
                page_id=p.id,
                page_no=p.page_no,
                kind=p.kind.value,
                quality=p.quality.value,
            )

    detections = injection.scan(session, store, ctx.settings, ctx.submission)
    if detections:
        injection.record(session, ctx.submission, detections)
        for d in detections:
            ctx.emit("security.injection", filename=d.filename, page_no=d.page_no, text=d.quote)

    return StepOutcome(
        status=StepStatus.DEGRADED if failures else StepStatus.SUCCEEDED,
        summary={
            "pages": sum(kinds.values()),
            "injection_attempts": len(detections),
            "hidden_text": hidden_spans,
            "kinds": dict(kinds),
            "quality": dict(quality),
            "unreadable": failures,
            "unsupported": unsupported,
            "reused_attachments": reused,
            "same_file_reused": same_file,
            "ocr_pages_read": cache.read if cache else 0,
            "ocr_pages_cached": cache.hits if cache else 0,
        },
    )


# Not carried over: the page's own identity and the document it was cut into in its submission
PAGE_COPY_SKIP = {"id", "submission_id", "attachment_id", "created_at", "document_id"}


def _cache_key(file_sha: str, page_no: int) -> str:
    return hashlib.sha256(f"{file_sha}|{TRIAGE_VERSION}|{page_no}".encode()).hexdigest()


def _keep(session: Session, att: Attachment, pages: list[Page], results: list[PageResult]) -> None:
    """Keep each page as read, by file, triage version and page number."""
    assert att.blob_sha256 is not None
    for page, r in zip(pages, results, strict=True):
        key = _cache_key(att.blob_sha256, page.page_no)
        values = {
            c.key: _plain(getattr(page, c.key))
            for c in Page.__table__.columns
            if c.key not in PAGE_COPY_SKIP
        }
        row = session.get(PageCacheEntry, key) or PageCacheEntry(key=key)
        row.file_sha256 = att.blob_sha256
        row.triage_version = TRIAGE_VERSION
        row.page_no = page.page_no
        row.page = values
        row.hidden = list(r.hidden)
        session.add(row)


def _plain(value: Any) -> Any:
    return value.value if isinstance(value, enum.Enum) else value


def _from_cache(session: Session, att: Attachment) -> list[PageCacheEntry]:
    if att.blob_sha256 is None:
        return []
    return list(
        session.scalars(
            select(PageCacheEntry)
            .where(
                PageCacheEntry.file_sha256 == att.blob_sha256,
                PageCacheEntry.triage_version == TRIAGE_VERSION,
            )
            .order_by(PageCacheEntry.page_no)
        )
    )


def _page_from(session: Session, att: Attachment, values: dict[str, Any]) -> Page:
    v = dict(values)
    v["kind"] = PageKind(v["kind"])
    v["quality"] = QualityBand(v["quality"])
    page = Page(**v, submission_id=att.submission_id, attachment_id=att.id)
    session.add(page)
    return page


def _read_before(session: Session, att: Attachment) -> list[Page]:
    """The pages of another attachment with the same bytes, read by this triage version."""
    other = session.scalar(
        select(Page.attachment_id)
        .join(Attachment, Attachment.id == Page.attachment_id)
        .where(
            Attachment.blob_sha256 == att.blob_sha256,
            Attachment.id != att.id,
            Page.triage_version == TRIAGE_VERSION,
        )
        .limit(1)
    )
    if other is None:
        return []
    pages = list(
        session.scalars(select(Page).where(Page.attachment_id == other).order_by(Page.page_no))
    )
    return pages if all(p.triage_version == TRIAGE_VERSION for p in pages) else []


def _copy_page(session: Session, att: Attachment, src: Page) -> Page:
    """A page of this attachment from the same file read before: every reading (kind, quality,
    words, images) is the same; the images and layout are content-addressed blobs, so shared."""
    values = {
        c.key: getattr(src, c.key) for c in Page.__table__.columns if c.key not in PAGE_COPY_SKIP
    }
    page = Page(**values, submission_id=att.submission_id, attachment_id=att.id)
    session.add(page)
    return page


class _DbOcrCache:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.hits = 0
        self.read = 0

    def get(self, key: str) -> list[list[Any]] | None:
        row = self.session.get(OcrCacheEntry, key)
        if row is not None:
            self.hits += 1
            return row.words
        return None

    def put(self, key: str, words: list[list[Any]]) -> None:
        self.read += 1
        if self.session.get(OcrCacheEntry, key) is None:
            self.session.add(OcrCacheEntry(key=key, engine=ocr.ENGINE, words=words))


def _save_page(ctx: StepContext, att: Attachment, r: PageResult, existing: Page | None) -> Page:
    """Update the page in place when it exists, so facts and classifications keep pointing at it."""
    session, store = ctx.session, ctx.store
    kind = r.kind
    if kind is PageKind.EMAIL_BODY and att.role is not AttachmentRole.EMAIL_BODY:
        kind = PageKind.TEXT  # a plain-text attachment, not the message itself

    def put(data: bytes | None, *, json: bool = False) -> str | None:
        if data is None:
            return None
        if json:
            return ensure_blob(session, store, data, kind="json", mime="application/json").sha256
        return ensure_blob(session, store, data).sha256

    page = existing or Page(
        submission_id=att.submission_id, attachment_id=att.id, page_no=r.page_no
    )
    page.kind = kind
    page.triage_version = TRIAGE_VERSION
    page.width = r.width
    page.height = r.height
    page.rotation = r.rotation
    page.has_text_layer = r.has_text_layer
    page.text_chars = r.text_chars
    page.image_coverage = r.image_coverage
    page.sharpness = r.sharpness
    page.contrast = r.contrast
    page.colorfulness = r.colorfulness
    page.skew_deg = r.skew_deg
    page.quality = r.quality
    page.warnings = r.warnings
    page.sheet_name = r.sheet_name
    page.sheet_hidden = r.sheet_hidden
    page.text_source = r.text_source
    page.ocr_confidence = r.ocr_confidence
    page.render_sha256 = put(r.render_png)
    page.thumb_sha256 = put(r.thumb_png)
    page.original_sha256 = put(r.original_png)
    page.prep = r.prep
    page.layout_sha256 = put(r.layout_json, json=True)
    session.add(page)
    session.flush()
    return page


def _drop_vanished(session: Session, pages: list[Page]) -> None:
    """Pages that no longer exist after re-triage go, with what was derived from them."""
    if not pages:
        return
    ids = [p.id for p in pages]
    session.execute(update(Fact).where(Fact.page_id.in_(ids)).values(page_id=None))
    session.execute(delete(PageClassification).where(PageClassification.page_id.in_(ids)))
    session.execute(delete(Page).where(Page.id.in_(ids)))
