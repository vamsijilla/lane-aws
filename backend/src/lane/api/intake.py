"""Email intake endpoint. Accepts a raw .eml as the request body."""

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response, status
from pydantic import BaseModel

from lane.api.deps import BlobStoreDep, SessionDep, SettingsDep
from lane.intake.eml import EmailParseError
from lane.intake.service import IntakeLimits, IntakeRejected, ingest_email
from lane.pipeline import background as work

router = APIRouter(prefix="/api/v1/intake", tags=["intake"])


class IngestResponse(BaseModel):
    submission_id: str
    created: bool
    files: int
    unique_files: int
    duplicates: int
    expanded_archives: int
    rejected: int
    unsupported: int
    notes: list[str]


@router.post(
    "/eml",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {
            "model": IngestResponse,
            "description": "Same email received before; existing submission returned",
        }
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"message/rfc822": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def ingest_eml(
    request: Request,
    response: Response,
    session: SessionDep,
    store: BlobStoreDep,
    settings: SettingsDep,
    background: BackgroundTasks,
) -> IngestResponse:
    limits = IntakeLimits.from_settings(settings)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > limits.max_email_bytes:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Email is over the size limit")
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Send the .eml file as the request body")

    try:
        result = ingest_email(session, store, bytes(raw), source="upload", limits=limits)
    except (IntakeRejected, EmailParseError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    if result.created and result.process_id:
        work.start(background, session, "process", result.process_id, trigger=result.trigger)
    else:
        response.status_code = status.HTTP_200_OK
    return IngestResponse(
        submission_id=result.submission_id,
        created=result.created,
        files=result.files,
        unique_files=result.unique_files,
        duplicates=result.duplicates,
        expanded_archives=result.expanded_archives,
        rejected=result.rejected,
        unsupported=result.unsupported,
        notes=result.notes,
    )
