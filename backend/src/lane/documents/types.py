"""Business document types and the classifier's per-page output schema."""

import enum
from typing import Literal

from pydantic import BaseModel, field_validator


class DocType(enum.StrEnum):
    APPLICATION = "application"
    QUOTE_FORM = "quote_form"
    DRIVER_SCHEDULE = "driver_schedule"
    VEHICLE_SCHEDULE = "vehicle_schedule"
    MVR = "mvr"
    DRIVERS_LICENSE = "drivers_license"
    LOSS_RUN = "loss_run"
    LOSS_HISTORY_LETTER = "loss_history_letter"
    IFTA_RETURN = "ifta_return"
    # Typed by tier 1 signals only (the model's page types do not include it, so adding it does
    # not change the classification prompt or its cache)
    DRIVER_EXCLUSION = "driver_exclusion"
    EMAIL_BODY = "email_body"
    OTHER = "other"


DOC_TYPE_LABELS: dict[DocType, str] = {
    DocType.APPLICATION: "Application",
    DocType.QUOTE_FORM: "Quote form",
    DocType.DRIVER_SCHEDULE: "Driver schedule",
    DocType.VEHICLE_SCHEDULE: "Vehicle schedule",
    DocType.MVR: "Motor vehicle record",
    DocType.DRIVERS_LICENSE: "Driver's license",
    DocType.LOSS_RUN: "Loss run",
    DocType.LOSS_HISTORY_LETTER: "Loss history letter",
    DocType.IFTA_RETURN: "IFTA return",
    DocType.DRIVER_EXCLUSION: "Driver exclusion",
    DocType.EMAIL_BODY: "Email body",
    DocType.OTHER: "Other",
}

# Types a classifier may return for a page. The email body is typed by intake, not the model.
PageDocType = Literal[
    "application",
    "quote_form",
    "driver_schedule",
    "vehicle_schedule",
    "mvr",
    "drivers_license",
    "loss_run",
    "loss_history_letter",
    "ifta_return",
    "other",
]


class PageVerdict(BaseModel):
    """What the classifier says about one page. Field docs live in the prompt."""

    doc_type: PageDocType
    starts_new_document: bool
    subject_name: str | None
    subject_id: str | None
    period: str | None
    document_date: str | None
    title: str
    confidence: Literal["high", "medium", "low"]
    evidence: list[str]

    @field_validator("evidence")
    @classmethod
    def _keep_three(cls, value: list[str]) -> list[str]:
        # The prompt asks for up to three; extra phrases are harmless, so trim rather than fail.
        return value[:3]
