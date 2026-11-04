"""Pages, page images and layouts, and pipeline runs for a submission."""

import io
import json
import re
from datetime import datetime, timedelta
from typing import Any, Literal, cast

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response, status
from PIL import Image, ImageFilter
from pydantic import BaseModel
from sqlalchemy import case, func, select

from lane.api.auth import ContentViewer, Processor
from lane.api.deps import BlobStoreDep, SessionDep
from lane.api.privacy import pii_texts, scrub
from lane.audit import chain
from lane.config import get_settings
from lane.db.models import (
    Attachment,
    Blob,
    Document,
    EmailMessage,
    LlmCall,
    Page,
    Run,
    Submission,
)
from lane.documents.layout import load_layout
from lane.domain import normalize as nz
from lane.llm.gateway import NOT_CACHED
from lane.pipeline import background as work
from lane.review import replay as replays
from lane.storage.blobs import BlobStore

router = APIRouter(prefix="/api/v1", tags=["pages"])

IMMUTABLE = "private, max-age=31536000, immutable"


class PageOut(BaseModel):
    id: str
    attachment_id: str
    filename: str
    page_no: int
    kind: str
    quality: str
    warnings: list[str]
    has_text_layer: bool
    text_chars: int
    image_coverage: float
    sharpness: float | None
    colorfulness: float | None
    skew_deg: float | None
    rotation: int
    width: float | None
    height: float | None
    sheet_name: str | None
    sheet_hidden: bool
    has_image: bool
    has_layout: bool
    prep: list[str]  # what preparation did to the page before it was read
    has_original: bool  # the page as it came is kept (variant=original)
    # A license card's photo: its image is blurred until an underwriter reveals it
    personal_photo: bool = False


class StepOut(BaseModel):
    name: str
    version: str
    status: str
    attempt: int
    duration_ms: int | None
    summary: dict[str, Any]
    error: str | None


class ModelCallsOut(BaseModel):
    calls: int
    from_cache: int
    not_cached: int  # replay only: answers the cache did not have, never sent
    input_tokens: int
    output_tokens: int
    cost_usd: float


class ReplayOut(BaseModel):
    of: str | None  # the run whose results were replayed
    identical: bool
    before_hash: str
    not_cached: int
    versions_changed: list[str]
    differences: list[str]  # output sections that differ


class RunOut(BaseModel):
    id: str
    trigger: str
    email: str | None = None  # the email that started it: "Subject, from sender"
    status: str
    started_at: datetime
    finished_at: datetime | None
    steps: list[StepOut]
    input_hash: str | None
    output_hash: str | None
    ruleset: str | None
    prompts: list[str]
    model_calls: ModelCallsOut
    replay: ReplayOut | None
    error: str | None  # why a replay could not run


def _email_line(session: SessionDep, email_id: str | None) -> str | None:
    email = session.get(EmailMessage, email_id) if email_id else None
    return f"{email.subject or '(no subject)'}, from {email.from_addr}" if email else None


def _submission(session: SessionDep, submission_id: str) -> Submission:
    sub = session.get(Submission, submission_id)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    return sub


def _page(session: SessionDep, page_id: str) -> Page:
    page = session.get(Page, page_id)
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    return page


@router.get("/submissions/{submission_id}/pages")
def list_pages(submission_id: str, session: SessionDep) -> list[PageOut]:
    _submission(session, submission_id)
    rows = session.execute(
        select(Page, Attachment.filename)
        .join(Attachment, Page.attachment_id == Attachment.id)
        .where(Page.submission_id == submission_id)
        .order_by(Attachment.ordinal, Page.page_no)
    ).all()
    licenses = set(
        session.scalars(
            select(Document.id).where(
                Document.submission_id == submission_id, Document.doc_type == "drivers_license"
            )
        )
    )
    return [
        PageOut(
            id=p.id,
            attachment_id=p.attachment_id,
            filename=filename,
            page_no=p.page_no,
            kind=p.kind.value,
            quality=p.quality.value,
            warnings=p.warnings,
            has_text_layer=p.has_text_layer,
            text_chars=p.text_chars,
            image_coverage=p.image_coverage,
            sharpness=p.sharpness,
            colorfulness=p.colorfulness,
            skew_deg=p.skew_deg,
            rotation=p.rotation,
            width=p.width,
            height=p.height,
            sheet_name=p.sheet_name,
            sheet_hidden=p.sheet_hidden,
            has_image=p.render_sha256 is not None,
            has_layout=p.layout_sha256 is not None or p.kind.value == "email_body",
            prep=p.prep,
            has_original=p.original_sha256 is not None,
            personal_photo=p.document_id in licenses,
        )
        for p, filename in rows
    ]


def _blob_response(
    request: Request, session: SessionDep, store: BlobStore, sha: str, media_type: str
) -> Response:
    etag = f'"{sha}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
    blob = session.get(Blob, sha)
    if blob is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact missing")
    return Response(
        store.get(blob.storage_key),
        media_type=media_type,
        headers={"ETag": etag, "Cache-Control": IMMUTABLE},
    )


@router.get(
    "/pages/{page_id}/image",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}, "description": "Rendered page"}},
)
def page_image(
    page_id: str,
    request: Request,
    session: SessionDep,
    store: BlobStoreDep,
    viewer: ContentViewer,
    variant: Literal["full", "thumb", "original"] = "full",
    reveal: bool = False,
) -> Response:
    """A license card's photo is personal data: it is blurred unless an underwriter reveals it,
    and each reveal is an audit event."""
    page = _page(session, page_id)
    sha = {
        "full": page.render_sha256,
        "thumb": page.thumb_sha256,
        "original": page.original_sha256,
    }[variant]
    if sha is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This page has no image")
    if not _is_license_photo(session, page):
        return _blob_response(request, session, store, sha, "image/png")
    if reveal:
        if not viewer.can("reveal_pii"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an underwriter can reveal a photo")
        chain.record(
            session,
            actor=viewer.actor,
            action="pii.photo_revealed",
            subject_type="page",
            subject_id=page.id,
            payload={"submission_id": page.attachment.submission_id, "variant": variant},
        )
        session.commit()
        blob = session.get(Blob, sha)
        if blob is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact missing")
        return Response(
            store.get(blob.storage_key),
            media_type="image/png",
            headers={"Cache-Control": "private, no-store"},
        )
    etag = f'"{sha}-blurred"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
    blob = session.get(Blob, sha)
    if blob is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact missing")
    return Response(
        blurred_png(store.get(blob.storage_key)),
        media_type="image/png",
        headers={"ETag": etag, "Cache-Control": IMMUTABLE, "X-Lane-Blurred": "1"},
    )


def _is_license_photo(session: SessionDep, page: Page) -> bool:
    doc = session.get(Document, page.document_id) if page.document_id else None
    return doc is not None and doc.doc_type == "drivers_license"


def blurred_png(png: bytes) -> bytes:
    """Blurred past reading: shapes and color survive, text and the face do not."""
    img = Image.open(io.BytesIO(png)).convert("RGB")
    radius = max(img.size) / 30
    out = io.BytesIO()
    img.filter(ImageFilter.GaussianBlur(radius)).save(out, format="PNG")
    return out.getvalue()


@router.get(
    "/pages/{page_id}/layout",
    response_class=Response,
    responses={
        200: {"content": {"application/json": {}}, "description": "Word boxes, cells or text"}
    },
)
def page_layout(
    page_id: str,
    request: Request,
    session: SessionDep,
    store: BlobStoreDep,
    _viewer: ContentViewer,
) -> Response:
    """Word boxes for PDF pages, the cell grid for worksheets, the text for the email body.
    Driver identifiers are masked in words, cells and text; worksheet dates read as dates."""
    page = _page(session, page_id)
    texts = pii_texts(session, get_settings())
    if page.layout_sha256:
        blob = session.get(Blob, page.layout_sha256)
        if blob is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact missing")
        layout = load_layout(store.get(blob.storage_key))
        return _private_json(present_layout(layout, texts))
    blob = page.attachment.blob
    if page.kind.value == "email_body" and blob is not None:
        text = store.get(blob.storage_key).decode("utf-8", errors="replace")
        return _private_json({"unit": "text", "text": scrub(text, texts)})
    raise HTTPException(status.HTTP_404_NOT_FOUND, "This page has no layout")


def _private_json(body: dict[str, Any]) -> Response:
    # Masked per request: never cached as an immutable artifact
    return Response(
        json.dumps(body, ensure_ascii=False),
        media_type="application/json",
        headers={"Cache-Control": "private, no-cache"},
    )


EXCEL_EPOCH = datetime(1899, 12, 30)
_DATE_FMT = re.compile(r"[dmy]{1,4}[/-][dmy]{1,4}", re.IGNORECASE)


def _cell_date(iso: str, texts: dict[str, str]) -> str:
    """A worksheet date as people read it; a date of birth masked."""
    return texts.get(iso) or texts.get(f"{iso}T00:00:00") or nz.long_date(iso) or iso


def present_layout(layout: dict[str, Any], texts: dict[str, str]) -> dict[str, Any]:
    """A layout as people may see it: identifiers masked, worksheet dates as dates."""
    out = dict(layout)
    if "cells" in layout:
        cells: list[dict[str, Any]] = []
        for c in cast(list[dict[str, Any]], layout["cells"]):
            v = c.get("v")
            if isinstance(v, str) and v in texts:
                c = {**c, "v": texts[v], "t": "s", "fmt": None}
            elif c.get("t") == "d" and isinstance(v, str):
                c = {**c, "v": _cell_date(v[:10], texts), "t": "s", "fmt": None}
            elif isinstance(v, (int, float)) and _DATE_FMT.search(str(c.get("fmt") or "")):
                # An Excel serial date (days since 1899-12-30) in a date-formatted cell
                day = (EXCEL_EPOCH + timedelta(days=float(v))).date().isoformat()
                c = {**c, "v": _cell_date(day, texts), "t": "s", "fmt": None}
            elif isinstance(v, str):
                c = {**c, "v": scrub(v, texts)}
            cells.append(c)
        out["cells"] = cells
    if "words" in layout:
        out["words"] = [
            [*w[:4], scrub(str(w[4]), texts), *w[5:]] if len(w) > 4 else w
            for w in cast(list[list[Any]], layout["words"])
        ]
    if isinstance(layout.get("text"), str):
        out["text"] = scrub(layout["text"], texts)
    return out


@router.get("/submissions/{submission_id}/runs")
def list_runs(submission_id: str, session: SessionDep) -> list[RunOut]:
    _submission(session, submission_id)
    runs = session.scalars(
        select(Run).where(Run.submission_id == submission_id).order_by(Run.started_at.desc())
    ).all()
    calls = {
        run_id: ModelCallsOut(
            calls=n,
            from_cache=int(hits or 0),
            not_cached=int(missed or 0),
            input_tokens=int(tin or 0),
            output_tokens=int(tout or 0),
            cost_usd=round(float(cost or 0), 4),
        )
        for run_id, n, hits, missed, tin, tout, cost in session.execute(
            select(
                LlmCall.run_id,
                func.count(),
                func.sum(case((LlmCall.cache_hit, 1), else_=0)),
                func.sum(case((LlmCall.error == NOT_CACHED, 1), else_=0)),
                func.sum(LlmCall.input_tokens),
                func.sum(LlmCall.output_tokens),
                func.sum(LlmCall.cost_usd),
            )
            .where(LlmCall.run_id.in_([r.id for r in runs]))
            .group_by(LlmCall.run_id)
        )
    }
    none = ModelCallsOut(
        calls=0, from_cache=0, not_cached=0, input_tokens=0, output_tokens=0, cost_usd=0
    )
    return [
        RunOut(
            id=r.id,
            trigger=r.trigger,
            email=_email_line(session, r.email_id),
            status=r.status.value,
            started_at=r.started_at,
            finished_at=r.finished_at,
            steps=[
                StepOut(
                    name=s.name,
                    version=s.version,
                    status=s.status.value,
                    attempt=s.attempt,
                    duration_ms=s.duration_ms,
                    summary=s.summary,
                    error=s.error,
                )
                for s in r.steps
            ],
            input_hash=r.input_hash,
            output_hash=r.output_hash,
            ruleset=r.versions.get("ruleset"),
            prompts=list(r.versions.get("prompts", [])),
            model_calls=calls.get(r.id, none),
            replay=ReplayOut(of=r.replay_of, **r.versions["replay"])
            if "replay" in r.versions
            else None,
            error=r.versions.get("error"),
        )
        for r in runs
    ]


@router.post(
    "/submissions/{submission_id}/process",
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="process_submission",
)
def reprocess(
    submission_id: str,
    session: SessionDep,
    background: BackgroundTasks,
    _viewer: Processor,
) -> dict[str, str]:
    _submission(session, submission_id)
    work.start(background, session, "process", submission_id, trigger="rerun")
    return {"status": "accepted"}


class RunStartedOut(BaseModel):
    run_id: str


@router.post(
    "/submissions/{submission_id}/replay",
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="replay_submission",
)
def replay(
    submission_id: str,
    session: SessionDep,
    background: BackgroundTasks,
    _viewer: Processor,
) -> RunStartedOut:
    """Run every step again from the stored files, with model answers only from the cache, and
    compare everything produced with what is stored now. Nothing stored is changed; no model is
    called."""
    _submission(session, submission_id)
    run = replays.start(session, submission_id)
    work.start(background, session, "replay", submission_id, trigger="replay", replay_run_id=run.id)
    return RunStartedOut(run_id=run.id)
