"""Runtime settings. Every knob comes from the environment or .env, never from code."""

import json
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import quote

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LANE_",
        env_file=(REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env"),
        extra="ignore",
    )

    env: str = "dev"
    data_dir: Path = REPO_ROOT / "var"
    # Where files (emails, attachments, page images) are kept. local: data_dir/blobs.
    # s3: a bucket (MinIO locally, S3 in production), the same sha256 keys.
    blob_backend: Literal["local", "s3"] = "local"
    s3_bucket: str = ""
    s3_prefix: str = "blobs/"
    s3_region: str = "us-east-1"
    s3_endpoint_url: str = ""  # MinIO: http://127.0.0.1:9000; empty for AWS
    # Empty: the standard AWS credential chain (a role in production)
    s3_access_key_id: str = ""
    s3_secret_access_key: SecretStr = SecretStr("")
    s3_sse: str = ""  # server-side encryption on each write: AES256 or aws:kms (empty for MinIO)
    database_url: str = ""
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    log_json: bool = False

    # Demo sign-in (production uses SSO; see docs/SECURITY.md). Tokens are HMAC-signed.
    session_secret: SecretStr = SecretStr("dev-only-secret-change-me")
    session_ttl_hours: int = 12
    # Role for a request that carries no token: read-only. The demo UI signs in explicitly.
    demo_default_role: str = "viewer"
    # Production: a request without a valid token is refused (health and sign-in excepted),
    # and the demo people are off; people sign in through Cognito instead
    require_sign_in: bool = False
    demo_login: bool = True
    # Cognito user pool for real sign-in (empty: off). The API checks ID tokens against it.
    cognito_region: str = "us-east-1"
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""

    # Secrets Manager (AWS): read once at start with the instance role, so no secret is in a
    # file. secrets_id holds ANTHROPIC_API_KEY and LANE_SESSION_SECRET; db_secret_id holds the
    # database password, filled into database_url where it says {password}.
    secrets_id: str = ""
    db_secret_id: str = ""
    secrets_region: str = "us-east-1"
    # Decision authority by role. "quote_at_risk": proceed to quote while the route is a decline
    # or potential decline, or a cleared decline awaits confirmation; "confirm_cleared_decline":
    # confirm a rule outcome the agent's documents moved away from decline (four-eyes, D114).
    decision_authority: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "underwriter": ["decline", "refer_back", "quote"],
            "senior_underwriter": [
                "decline",
                "refer_back",
                "quote",
                "quote_at_risk",
                "confirm_cleared_decline",
            ],
        }
    )
    # Mailpit email intake (Tier 2): the REST API URL, e.g. http://localhost:8025. Empty: off
    mailpit_url: str = ""
    mailpit_poll_s: float = 5.0
    # Real inbound email (SES on AWS): SES stores each message in s3_bucket under this prefix;
    # the worker ingests from there. Empty: off. Only an authenticated sender on the allowlist
    # (an address, or "@domain") is processed; anything else is held, never ingested.
    ses_inbox_prefix: str = ""
    intake_allowed_senders: list[str] = Field(default_factory=lambda: list[str]())
    # Live event stream (SSE)
    events_poll_s: float = 0.2
    events_heartbeat_s: float = 15.0
    events_idle_close_s: float = 5.0  # close this long after the run finishes and goes quiet
    # Postgres channel the worker's events travel on to the API (queue mode)
    events_channel: str = "lane_events"

    # Where long work runs. inline: on a thread of the process that starts it (tests, a quick
    # local run). queue: as a job in the database, run by a worker (python -m lane.worker)
    # that holds it under a lease; a job whose worker died is claimed again and resumed.
    run_mode: Literal["inline", "queue"] = "inline"
    worker_lease_s: float = 30.0
    worker_renew_s: float = 10.0
    worker_poll_s: float = 1.0
    worker_max_attempts: int = 3

    # Personal data masked in API responses and logs unless an underwriter reveals it.
    # Text in a document that tries to instruct a reader or a system is recorded and shown.
    injection_patterns: tuple[str, ...] = (
        r"ignore (all |any )?(the )?(prior|previous|above|earlier) (instructions|rules|guidelines)",
        r"disregard (all |any )?(the )?(prior|previous|above) (instructions|rules)",
        r"\b(approve|bind|accept) this (submission|risk|account|application)\b",
        r"\byou are (now )?(an?|the) (assistant|ai|model|system)\b",
        r"\bsystem prompt\b",
    )
    # A driver's identifiers only; business data (company addresses, phones, emails, FEIN) is not
    pii_attributes: frozenset[str] = frozenset({"license_number", "date_of_birth", "home_address"})

    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")

    # Intake limits. Anything over a limit is rejected with a visible reason, never dropped.
    intake_max_email_mb: int = 50
    intake_max_archive_depth: int = 3
    intake_max_archive_members: int = 500
    intake_max_file_mb: int = 100
    intake_max_expanded_mb: int = 500
    intake_max_compression_ratio: float = 200.0

    # Page triage. Thresholds were set by profiling the sample packet; see docs/DECISIONS.md.
    triage_render_dpi: int = 200
    triage_thumb_width: int = 320
    triage_text_min_chars: int = 40  # fewer extracted characters means "no usable text layer"
    triage_scan_image_coverage: float = 0.6  # share of the page covered by images
    triage_photo_min_colorfulness: float = 15.0
    triage_photo_max_white: float = 0.6
    triage_sharp_clean: float = 120.0
    triage_sharp_poor: float = 40.0
    triage_skew_warn_deg: float = 2.0
    # Page preparation before OCR (documents/prep.py)
    triage_prep: bool = True
    triage_deskew_min_deg: float = 0.5
    triage_flatten_min_area: float = 0.35
    triage_shadow_min_range: float = 40.0
    triage_ocr_min_width: int = 1700
    triage_ocr: bool = True  # OCR scans and photos for word boxes and a second reading
    # A file read before (same sha256, same triage version) is not read again: its pages are
    # reused. Underneath, OCR answers are cached by file, page, engine and settings.
    triage_reuse_pages: bool = True
    triage_ocr_cache: bool = True
    # RapidOCR models, "<PP-OCR version>/<size>" (lane.evals.ocr_bench compares them)
    triage_ocr_models: str = "PP-OCRv6/small"
    # A value OCR read verifies only at this word confidence or above (every word of an
    # identifier); below it the spot is unreadable, not a disagreement
    triage_ocr_min_word_score: float = 0.85
    # A decision value that did not verify is read again from its spot enlarged this much
    triage_ocr_reread_scale: float = 2.5

    # Models per task. Cheap and fast where the task is easy.
    llm_classify_model: str = "claude-haiku-5-5"
    llm_classify_effort: str = "low"
    # Extraction v2: decision-driven, verified extraction from the field catalog
    # (config/field_catalog.yaml), the default since it passed acceptance (docs/AI_DESIGN.md).
    # EXTRACT_V2=0 runs v1.
    extract_v2: bool = Field(
        default=True, validation_alias=AliasChoices("LANE_EXTRACT_V2", "EXTRACT_V2")
    )
    llm_extract_model: str = "claude-haiku-5-5"
    llm_extract_effort: str = "medium"
    # Re-read documents that fail checks with this model, only after a person approves.
    llm_escalation_model: str = "claude-sonnet-5-5"
    # Extraction v2: documents the stronger model reads first. Images of these types, and any
    # document with a page graded poor; the cheaper model then gives the independent second read.
    extract_strong_first_types: list[str] = Field(default_factory=lambda: ["drivers_license"])
    extract_strong_first_poor: bool = True
    llm_escalation_effort: str = "medium"
    llm_analyze_model: str = "claude-opus-5-5"
    llm_analyze_effort: str = "high"
    llm_analyze_max_tokens: int = 16000
    llm_draft_model: str = "claude-sonnet-5-5"
    llm_draft_effort: str = "low"
    llm_draft_max_tokens: int = 4000
    llm_max_concurrency: int = 16
    llm_timeout_s: float = 300.0
    llm_max_retries: int = 3
    # Replay: answer only from the cache and never call a model (set per replay, not in .env)
    llm_cache_only: bool = False
    llm_image_max_px: int = 1568  # longest edge sent to vision; larger images are downscaled
    # Server-side refusal fallback on models that support it (not Haiku). See docs/DECISIONS.md.
    llm_refusal_fallbacks: bool = True
    # USD per million tokens: (input, output). Cache reads/writes are priced from input.
    llm_prices: dict[str, tuple[float, float]] = Field(
        default_factory=lambda: {
            "claude-haiku-5-5": (0.10, 0.50),
            "claude-sonnet-5-5": (2.00, 10.00),
            "claude-opus-5-5": (4.00, 20.00),
        }
    )

    # Analyst (data minimization: what the model never sees in the clear)
    analyze_withheld_attributes: frozenset[str] = frozenset(
        {"mailing_address", "email", "phone", "garaging_address"}
    )
    analyze_tokenized_attributes: frozenset[str] = frozenset({"license_number", "fein"})
    analyze_dropped_detail_keys: frozenset[str] = frozenset(
        {"document_id", "fact_ids", "quote", "page", "bbox"}
    )
    analyze_tokenized_detail_keys: frozenset[str] = frozenset({"claimant"})
    analyze_summary_sentences: tuple[int, int] = (5, 7)
    analyze_retries: int = 1  # re-asks with the validation errors when citations fail
    # The analyst runs without asking only when its estimated cost is at or under this budget
    # (or the answer is cached). 0 means a person always approves a paid run (assumption A16).
    analyze_auto_budget_usd: float = 0.0
    analyze_expected_output_tokens: int = 12000  # answer plus reasoning, for the estimate

    # Display cues the guideline does not set (the screens mark these, rules do not use them)
    claim_report_lag_days: int = 14  # a claim reported later than this after the loss
    # The application's own definition of fast growth: more than this many units in a year
    analyze_fast_growth_units: int = 5
    ifta_drop_warning_pct: float = 15.0  # a quarter's miles falling by more than this share
    analyze_full_term_days: tuple[int, int] = (355, 375)  # shorter or longer terms are noted

    # Actions
    actions_signature: str = "Underwriting team"
    # Missing information not already asked for by a rule, grouped by category.
    actions_missing_headings: dict[str, str] = Field(
        default_factory=lambda: {
            "application": "Complete and sign the application",
            "operations": "Answer the questions left blank on the application",
            "vehicles": "Explain the vehicles below",
            "drivers": "Explain the drivers below",
            "loss_history": "Loss history",
        }
    )
    actions_helpful_heading: str = (
        "Also helpful: answer the questions left blank on the application"
    )
    actions_bind_headings: dict[str, str] = Field(
        default_factory=lambda: {"application": "Sign the application"}
    )

    # Document types that are one kind of record in different formats. A filename that names one
    # and content of another is a different format, not a misnamed file.
    doc_type_families: dict[str, list[str]] = Field(
        default_factory=lambda: {"loss history": ["loss_run", "loss_history_letter"]}
    )
    doc_format_names: dict[str, str] = Field(
        default_factory=lambda: {"loss_run": "loss run", "loss_history_letter": "letter"}
    )

    # Which tier a missing item is in, by code prefix. "required": the guideline needs it before
    # a quote (in the agent request by default). "helpful": a form blank that helps but is not
    # required (left out of the request until someone adds it). "bind": needed to bind, asked
    # for with the binding items. Anything unlisted is required.
    missing_tiers: dict[str, str] = Field(
        default_factory=lambda: {
            "APP_AUTHORITY": "helpful",
            "APP_BUSINESS_START": "helpful",
            "APP_HISTORY": "helpful",
            "Q_ENGLISH_PROFICIENCY": "helpful",
            "APP_SIGNATURE": "bind",
        }
    )

    # Fields that can hold several valid values at once: listed together, never a conflict.
    multi_valued_attributes: list[str] = Field(default_factory=lambda: ["phone", "fax", "email"])

    # How much each conflict matters, by "<entity kind>.<field>". Conflicts list blocking ones
    # first (they change a rule outcome), then high, medium, low; anything unlisted is medium.
    conflict_severity: dict[str, str] = Field(
        default_factory=lambda: {
            "operations.q_unlisted_vehicles": "high",
            "fleet.power_units": "high",
            "applicant.years_prior_insurance": "high",
            "applicant.entity_form": "medium",
            "operations.farthest_territory": "medium",
            "operations.refrigerated": "medium",
            "coverage_term.carrier": "medium",
            "coverage_history.physical_damage_carried": "medium",
            "agency.name": "low",
            "agency.identity": "low",
            "driver.date_of_birth": "low",
            "vehicle.make": "low",
            "coverage_request.um_limit": "low",
        }
    )

    # A conflict not asked on its own when a missing item (code prefix) already asks the same
    # thing: the unlisted vehicle question names the vehicle; the yes/no conflict does not.
    actions_conflicts_asked_by_missing: dict[str, str] = Field(
        default_factory=lambda: {
            "q_unlisted_vehicles": "VEH_UNLISTED",
            "power_units": "VEH_UNLISTED",
        }
    )
    # Conflicts a rule's own request already asks (who drives): asked once, inside that request
    actions_conflicts_asked_by_rule: dict[str, str] = Field(
        default_factory=lambda: {"owner_drives": "DRV-01"}
    )
    # Low-severity differences that are likely the same thing written two ways (an agency's
    # two trading names, a state minimum and its figures): one optional line, not one each
    actions_optional_conflicts: frozenset[str] = frozenset({"name", "um_limit"})

    # Open conflicts the agent can settle, asked as questions (by field attribute). {about} is
    # what the field belongs to (" for Ana Garcia"), {shown} the values the documents show, {n}
    # how many;
    # without {shown}, "The documents show A and B." follows. Others read "What is the
    # <field>{about}? The documents show A and B."
    actions_conflict_questions: dict[str, str] = Field(
        default_factory=lambda: {
            "owner_drives": "Does the owner drive? The documents show {shown}. If not, name them as an excluded driver.",
            "entity_form": "What is the legal entity form of the business?",
            "refrigerated": "Do you run refrigerated (reefer) units, and how many?",
            "power_units": "How many power units do you operate?",
            "q_unlisted_vehicles": "Do you operate any vehicles not on the vehicle schedule?",
            "q_ownership_change": "Has ownership, management or the business name changed?",
            "years_prior_insurance": "How many years of prior commercial auto insurance does the business have?",
            "physical_damage_carried": "Was physical damage carried on prior policies?",
            "carrier": "Which carrier wrote the policy{about}?",
            "farthest_territory": "What is the farthest state the trucks run to?",
            # Month and day only ({n}: how many dates the documents give)
            "date_of_birth": "The documents show {n} dates of birth{about}: {shown}. Which is correct?",
        }
    )

    # Classification
    classify_text_chars: int = 3000  # text-layer characters sent with each page
    classify_sheet_cells: int = 400  # worksheet cells sent with each sheet
    # Known layouts (tier 2), learned only from documents a person confirmed, type pages before
    # the model. The model re-checks this share of the pages they type (1.0 in evals); a
    # disagreement disables the layout and sends the document to review.
    templates_enabled: bool = True
    templates_audit_rate: float = 0.1

    def check_secrets(self) -> None:
        """Outside local development the demo sign-in secret must be set."""
        default = type(self).model_fields["session_secret"].default
        if (
            self.env != "dev"
            and self.session_secret.get_secret_value() == default.get_secret_value()
        ):
            raise RuntimeError(
                "Set LANE_SESSION_SECRET: the default is only allowed when LANE_ENV=dev"
            )

    @property
    def has_llm_key(self) -> bool:
        return bool(self.anthropic_api_key and self.anthropic_api_key.get_secret_value().strip())

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.data_dir / 'lane.db'}"

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"


def with_secrets(
    settings: Settings, fetch: Callable[[str], dict[str, str]] | None = None
) -> Settings:
    """Fill the secrets from Secrets Manager when the settings name them; else unchanged.

    `fetch(secret_id) -> dict` is for tests; by default it asks Secrets Manager."""
    if not settings.secrets_id and not settings.db_secret_id:
        return settings
    read = fetch or _secrets_manager(settings.secrets_region)
    update: dict[str, Any] = {}
    if settings.secrets_id:
        app = read(settings.secrets_id)
        if app.get("ANTHROPIC_API_KEY"):
            update["anthropic_api_key"] = SecretStr(app["ANTHROPIC_API_KEY"])
        if app.get("LANE_SESSION_SECRET"):
            update["session_secret"] = SecretStr(app["LANE_SESSION_SECRET"])
    if settings.db_secret_id:
        if "{password}" not in settings.database_url:
            raise RuntimeError("LANE_DATABASE_URL must say {password} where the password goes")
        password = quote(read(settings.db_secret_id)["password"], safe="")
        update["database_url"] = settings.database_url.replace("{password}", password)
    return settings.model_copy(update=update)


def _secrets_manager(region: str) -> Callable[[str], dict[str, str]]:
    import boto3

    client = cast(Any, boto3.client("secretsmanager", region_name=region))  # pyright: ignore[reportUnknownMemberType]

    def read(secret_id: str) -> dict[str, str]:
        raw = client.get_secret_value(SecretId=secret_id)["SecretString"]
        return cast(dict[str, str], json.loads(raw))

    return read


@lru_cache
def get_settings() -> Settings:
    return with_secrets(Settings())
