"""FastAPI application factory."""

import json
import re
import threading
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from lane import __version__
from lane.api import (
    actions,
    analysis,
    audit,
    auth,
    documents,
    evals,
    events,
    evidence,
    extractions,
    how,
    intake,
    ops,
    overview,
    pages,
    resolution,
    rules,
    submissions,
)
from lane.api.auth import read_claims, token_of
from lane.api.privacy import mask_ssn, pii_texts
from lane.config import Settings, get_settings
from lane.db.base import get_session, get_sessionmaker
from lane.documents import ocr
from lane.events import notify
from lane.intake import mailpit
from lane.log import configure_logging
from lane.pipeline import background as work
from lane.pipeline.runner import recover_interrupted


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    database: Literal["ok", "unavailable"]
    llm: Literal["configured", "missing_key"]


log = structlog.get_logger()
router = APIRouter(prefix="/api/v1")


@router.get("/health", tags=["health"], operation_id="get_health")
def health(session: Annotated[Session, Depends(get_session, scope="function")]) -> Health:
    try:
        session.execute(text("SELECT 1"))
        database: Literal["ok", "unavailable"] = "ok"
    except SQLAlchemyError:
        database = "unavailable"
    llm: Literal["configured", "missing_key"] = (
        "configured" if get_settings().has_llm_key else "missing_key"
    )
    return Health(
        status="ok" if database == "ok" else "degraded",
        version=__version__,
        database=database,
        llm=llm,
    )


def _operation_id(route: APIRoute) -> str:
    return route.name


class Lifespan:
    """Startup work: check secrets, then recover runs a restart interrupted."""

    def __init__(self, _app: FastAPI) -> None:
        self._stops: list[threading.Event] = []

    async def __aenter__(self) -> None:
        settings = get_settings()
        settings.check_secrets()
        if settings.run_mode == "queue":
            # Workers run the pipeline and watch Mailpit; their events reach this process's
            # live view over Postgres NOTIFY. A worker's lease, not a restart, decides
            # whether a run was cut off.
            if notify.available(settings):
                self._stops.append(notify.start_listening(settings))
            return
        try:
            with get_sessionmaker()() as session:
                recovered = recover_interrupted(session)
            if recovered:
                log.warning("runs.recovered", count=recovered)
        except SQLAlchemyError:
            log.warning("runs.recovery_skipped", reason="database not ready")
        if settings.triage_ocr:
            # Load the OCR models now, off the request path, so the first scanned page of the
            # first submission does not wait for them
            threading.Thread(
                target=ocr.warm_up, args=(settings.triage_ocr_models,), daemon=True
            ).start()
        if settings.mailpit_url:  # agents' emails arrive by SMTP and are picked up here
            self._stops.append(mailpit.start_in_background(settings, work.process))

    async def __aexit__(self, *_exc: object) -> None:
        for stop in self._stops:
            stop.set()


# Responses the last privacy pass leaves alone: a reveal returns the real value by design.
_UNSCRUBBED = re.compile(r"/reveal$")
# Submission data only: health, sign-in, evals and the type catalogs hold no driver data
_SCRUBBED = re.compile(r"^/api/v1/(submissions|documents|fields|pages|facts|request-items)\b")


async def scrub_personal_data(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """The last privacy pass: every driver identifier the database holds, in every printed form,
    is masked in every JSON response, whatever produced it."""
    response = await call_next(request)
    path = request.url.path
    if not _SCRUBBED.match(path) or "application/json" not in response.headers.get(
        "content-type", ""
    ):
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])  # type: ignore[attr-defined]
    headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
    # Social Security numbers: masked for every role, in every response, reveals included
    body = mask_ssn(body.decode("utf-8")).encode("utf-8")
    settings = get_settings()
    if _UNSCRUBBED.search(path):
        return Response(body, status_code=response.status_code, headers=headers)
    # Off the event loop: waiting here for a connection must not stop the requests that would
    # free one. If the identifiers cannot be read, the response is withheld, never sent unmasked.
    try:
        texts = await run_in_threadpool(_pii_texts, settings)
    except SQLAlchemyError:
        log.error("privacy.scrub_unavailable", path=path)
        return JSONResponse(
            {"detail": "Personal data could not be checked, so this response was withheld."},
            status_code=503,
        )
    if texts:
        text = body.decode("utf-8")
        for raw in sorted(texts, key=len, reverse=True):
            if raw in text:
                text = text.replace(
                    json.dumps(raw)[1:-1], json.dumps(texts[raw], ensure_ascii=False)[1:-1]
                )
        body = text.encode("utf-8")
    return Response(body, status_code=response.status_code, headers=headers)


def _pii_texts(settings: Settings) -> dict[str, str]:
    with get_sessionmaker()() as session:
        return pii_texts(session, settings)


# Open without a sign-in even when sign-in is required: health, and signing in itself
_OPEN = re.compile(r"^/api/v1/(health$|auth/)")


async def require_sign_in(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """With LANE_REQUIRE_SIGN_IN (production), the API answers only signed-in people: no
    anonymous read-only access to submissions. Off by default, so local use is unchanged."""
    settings = get_settings()
    path = request.url.path
    if (
        settings.require_sign_in
        and request.method != "OPTIONS"
        and path.startswith("/api/v1/")
        and not _OPEN.match(path)
    ):
        token = token_of(
            request.headers.get("authorization"), request.query_params.get("access_token")
        )
        if token is None or read_claims(token, settings) is None:
            return JSONResponse({"detail": "Sign in to use Lane"}, status_code=401)
    return await call_next(request)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(json=settings.log_json, pii=settings.pii_attributes)
    app = FastAPI(
        lifespan=Lifespan,
        title="Lane API",
        version=__version__,
        # Operation ids are the handler names, so the generated client reads naturally.
        generate_unique_id_function=_operation_id,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.middleware("http")(scrub_personal_data)
    app.middleware("http")(require_sign_in)
    # Added last, so outermost: the privacy pass reads plain bodies, then they are compressed.
    # Event streams are never compressed (Starlette leaves text/event-stream alone).
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.include_router(router)
    app.include_router(intake.router)
    app.include_router(pages.router)
    app.include_router(documents.router)
    app.include_router(submissions.router)
    app.include_router(extractions.router)
    app.include_router(resolution.router)
    app.include_router(rules.router)
    app.include_router(analysis.router)
    app.include_router(actions.router)
    app.include_router(auth.router)
    app.include_router(overview.router)
    app.include_router(evidence.router)
    app.include_router(events.router)
    app.include_router(audit.router)
    app.include_router(evals.router)
    app.include_router(ops.router)
    app.include_router(how.router)
    return app


app = create_app()
