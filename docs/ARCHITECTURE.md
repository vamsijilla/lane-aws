# Lane architecture

Lane takes an agent's email of insurance documents and gives an underwriter one page: what each
document is, what it says, where documents disagree, what is missing, how the underwriting
guideline lands rule by rule, and what to ask the agent next. A model reads and explains; code
applies the guideline; a person decides.

This document describes Lane in three environments, read from the code in this repository:

1. **Local:** the whole stack on one Mac.
2. **AWS today:** the deployment running at https://laneunder.online, defined by
   `deploy/cloudformation.yml`.
3. **Production:** the design for running Lane at Knight. Each item is marked built, config or
   new.

The application itself (its layers, packages, pipeline, data model and screens) is the same in all
three, and is described once.

## How to read this document

Every environment is described in the same order, so each part can be drawn on its own:

- **Boundaries:** what contains what (a machine, a cloud account, a region, a network, a subnet,
  an instance).
- **Layers and components:** each component with its tool, version and job.
- **Connections:** each with its source, target, protocol and port.
- **Workflows:** numbered steps, naming every hop.

File paths are relative to the repository root. Nothing here is aspirational except the
production part, which says so item by item.

## Contents

- [Application architecture](#application-architecture)
  - [Layers, top to bottom](#layers-top-to-bottom)
  - [Backend packages](#backend-packages)
  - [API](#api)
  - [The pipeline](#the-pipeline)
  - [Intake](#intake)
  - [Documents and OCR](#documents-and-ocr)
  - [Extraction and trust](#extraction-and-trust)
  - [Resolution, rules, analysis and actions](#resolution-rules-analysis-and-actions)
  - [LLM gateway](#llm-gateway)
  - [Data model](#data-model)
  - [Frontend](#frontend)
  - [Security and privacy](#security-and-privacy)
  - [End-to-end flows](#end-to-end-flows)
- [Local deployment: runtime and tooling](#local-deployment-runtime-and-tooling)
  - [At a glance](#at-a-glance)
  - [Processes and services](#processes-and-services)
  - [Numbered connections](#numbered-connections)
  - [Environment variables](#environment-variables)
  - [Makefile targets](#makefile-targets)
  - [Developer tooling](#developer-tooling)
  - [Scripts (`scripts/`)](#scripts-scripts)
  - [Sample data](#sample-data)
  - [Evals (`evals/`)](#evals-evals)
  - [CI (`.github/workflows/ci.yml`)](#ci-githubworkflowsciyml)
  - [Local rehearsal of the AWS stack (Docker)](#local-rehearsal-of-the-aws-stack-docker)
  - [Flows](#flows)
- [Lane on AWS: the deployment as built](#lane-on-aws-the-deployment-as-built)
  - [As deployed (read-only check)](#as-deployed-read-only-check)
  - [1. The CloudFormation template, resource by resource](#1-the-cloudformation-template-resource-by-resource)
  - [2. The deploy scripts](#2-the-deploy-scripts)
  - [3. The runtime on the instance](#3-the-runtime-on-the-instance)
  - [4. App code that exists for AWS](#4-app-code-that-exists-for-aws)
  - [5. CI/CD](#5-cicd)
  - [6. Security posture as built](#6-security-posture-as-built)
  - [7. Flows](#7-flows)
  - [8. Cost per month](#8-cost-per-month)
  - [Notes, uncertainties and differences between code and deployment](#notes-uncertainties-and-differences-between-code-and-deployment)
- [Production (design)](#production-design)
  - [What does not change](#what-does-not-change)
  - [Layers, outside in](#layers-outside-in)
  - [Network, subnet by subnet](#network-subnet-by-subnet)
  - [Compute](#compute)
  - [Verification beyond the documents](#verification-beyond-the-documents)
  - [Data protection and recovery](#data-protection-and-recovery)
  - [Delivery](#delivery)
  - [Production flows (numbered as on the diagram)](#production-flows-numbered-as-on-the-diagram)
  - [From today to production, step by step](#from-today-to-production-step-by-step)

## Application architecture

This section describes Lane as an application, independent of where it runs. Everything here is read from the code in `backend/src/lane/`, `backend/rules/`, `backend/prompts/`, `config/` and `frontend/src/`. Paths are relative to the repository root. Where the code and the README differ, the code wins.

Two processes make up the backend: the API (`backend/src/lane/api/main.py`, a FastAPI app) and the worker (`backend/src/lane/worker.py`). One process makes up the frontend: a Next.js 16.4.3 app (`frontend/`). They share one relational database (SQLite by default, Postgres when `LANE_DATABASE_URL` names one) and one content-addressed blob store (a local folder or an S3 bucket).

### Layers, top to bottom

Eight layers, top to bottom, each calling only the layers below it. What passes between each pair:

1. **Experience** to **API**: HTTPS JSON with a Bearer token; Server-Sent Events and page images with the token in the URL (`?access_token=`).
2. **API** to **Orchestration**: `work.start(...)` on a thread (inline mode) or a row in the `jobs` table (queue mode).
3. **Orchestration** to **Intelligence**: each pipeline step calls the services of its stage.
4. **Intelligence** to **Verification gate**: facts carrying tier, verified flag, grounding and confidence.
5. **Verification gate** to **Decision**: only facts that pass the gate may decide.
6. **Decision** to **Evidence**: every result is written (35 tables, the audit hash chain, blobs by sha256, run fingerprints).
7. **Policy as data** is read by all of the above at startup or per call (the ruleset, the field catalog, the classification vocabulary, the prompts, `lane/config.py` settings).


| Layer | Lives in | Holds | May call | Never does |
|---|---|---|---|---|
| 1 Experience | `frontend/src/app/*`, `frontend/src/components/*`, `frontend/src/lib/*` | Screens, session token (localStorage `lane.session`), TanStack Query cache, evidence lens state | API over HTTP via the generated client `frontend/src/lib/api/index.ts`; SSE and images through `authedUrl()`; Cognito hosted login (`/oauth2/authorize`, `/oauth2/token`) | Decide outcomes, compute rules, call a model, hold shapes of its own (all types come from `schema.d.ts`) |
| 2 API | `backend/src/lane/api/*.py` | Request validation, auth and permissions, masking, display tidying (`api/display.py`), response models (`api/models.py`) | Services in layers 3 to 7; `pipeline/background.start()` to start long work | Run the pipeline on the request path (except the synchronous resolve and rules rerun after a person's change, which is code only), send email |
| 3 Orchestration | `backend/src/lane/pipeline/*`, `backend/src/lane/worker.py`, `backend/src/lane/intake/*`, `backend/src/lane/events/*` | Run and step records, job leases, worker heartbeats, live event buffers | Every step function; the job table; Postgres NOTIFY; Mailpit REST; S3 inbox | Decide anything about a submission; a failed step never crashes a run (`runner._execute` catches every exception) |
| 4 Intelligence | `backend/src/lane/documents/*`, `backend/src/lane/extract/*`, `backend/src/lane/analyze/*`, `backend/src/lane/actions/email.py`, `backend/src/lane/llm/*` | Pages, OCR words, page verdicts, documents, extracted data, analyst findings, draft wording | The LLM gateway only (the one door to models), local OCR (RapidOCR), PyMuPDF | Approve, decline or route; a model never writes a rule outcome |
| 5 Verification gate | `backend/src/lane/extract/verify.py`, `extract/ocr_check.py`, `extract/validators.py`, `extract/checks.py`, `extract/engine_v2.py` (`finish`, `_confidence`), `resolve/service.py` (`decides`), `rules/view.py` (`unverified_reads`), `rules/engine.py` (`_unverified`) | Per fact: `tier`, `verified`, `verification`, `second_read`, `grounding`, `confidence`, `confidence_reasons` | Page words, worksheet cells, OCR re-read of a crop | Let a decision-tier value that was read once and not verified decide a rule |
| 6 Decision | `backend/src/lane/resolve/*`, `backend/src/lane/rules/*`, `backend/src/lane/actions/build.py`, `actions/points.py`, `actions/asks.py`, `backend/src/lane/review/service.py`, `backend/src/lane/overview/service.py` | Entities, fields with all candidates, conflicts, missing items, rule results, the recommendation, request items | Resolved fields only (rules read `SubmissionView`, never facts or model output directly) | Call a model; pick silently between conflicting values |
| 7 Evidence | `backend/src/lane/db/*`, `backend/src/lane/audit/*`, `backend/src/lane/storage/*`, `backend/src/lane/pipeline/fingerprint.py`, `backend/src/lane/review/replay.py` | Every row, every blob by sha256, the append-only audit chain, run fingerprints | Database, blob store | Rewrite a blob, edit or delete an audit event |
| 8 Policy as data | `backend/rules/knight_trucking.v1.yaml`, `config/field_catalog.yaml`, `config/field_catalog.d/driver_exclusion.yaml`, `config/classification.yaml`, `backend/prompts/`, `backend/src/lane/config.py` | Rule thresholds, assumptions, teams, request wording; fields to extract and their tiers; tier 1 vocabulary; prompts; model ids, prices, limits | Nothing (read by code) | Contain anything specific to one sample packet (packet data lives in `data/` and `evals/gold/`) |

### Backend packages

All under `backend/src/lane/`. "Writes" lists tables written; table names are from `db/models.py`.

#### Top-level modules

| Module | Responsibility | Key names |
|---|---|---|
| `config.py` | Every runtime knob from env `LANE_*` or `.env` (pydantic-settings). Secrets Manager fill-in. | `Settings`, `get_settings()` (lru cached), `with_secrets()`, `Settings.check_secrets()`, `has_llm_key`, `resolved_database_url` |
| `worker.py` | Queue worker: claims jobs, renews leases, heartbeats, starts Mailpit and SES watchers, forwards events over NOTIFY | `main()`, `run_job()`, `Presence` (heartbeat every `HEARTBEAT_S = 5.0` s), `_keep_lease()` |
| `log.py` | structlog setup; JSON in production; PII keys masked | `configure_logging()`, `mask_pii()` |
| `__init__.py` | `__version__` | |

#### `intake/`: the ways in

| Module | Responsibility | Inputs / outputs | Writes |
|---|---|---|---|
| `service.py` | Turn one raw `.eml` into a stored submission; idempotent | `ingest_email(session, store, raw, source, limits, link_related=True) -> IngestResult`; `IntakeLimits`, `IntakeRejected`, `summarize()` | `blobs`, `email_messages`, `submissions`, `attachments`, `audit_events` |
| `eml.py` | Parse RFC 5322 into headers, readable body, parts | `parse_eml() -> ParsedEmail`, `EmailPart`, `safe_filename()`, `html_to_text()`; keeps `KEPT_HEADERS` (From, To, Cc, Reply-To, Subject, Date, Message-ID, In-Reply-To, References) | |
| `sniff.py` | Container format from magic bytes; filename and declared MIME ignored | `sniff(data) -> Sniffed(kind, mime)`; `FileKind`; `SUPPORTED_KINDS` | |
| `archive.py` | Defensive zip reading in memory | `read_zip(data, ArchiveLimits) -> ArchiveListing` | |
| `link.py` | Is a new email about an existing submission? | `place() -> Placement`, `attach()`, `keep_separate()`, `reference()`, `tag_subject()` | `submissions` (relation columns), `attachments` (moved), `audit_events` |
| `mailpit.py` | Poll Mailpit's REST API (SMTP port 1025) | `poll_once()`, `watch()`, `start_in_background()`, `MailpitClient`, `Mailbox` protocol | via `ingest_email` |
| `ses.py` | Real inbound mail: SES writes each message to S3 under `ses_inbox_prefix`; same poller | `S3Mailbox`, `held_reason()`, `authenticated()`, `allowed()`, `start_in_background()` | via `ingest_email`; moves S3 objects to `done/` or `held/` |
| `cli.py` | `python -m lane.intake.cli path.eml ...` | | via `ingest_email` |
| `remove.py` | Delete a submission and everything derived; or delete and re-ingest its original email | `delete_submission()`, `delete_and_reingest()`, `BY_SUBMISSION` | deletes derived rows; adds `submission.deleted` audit event; keeps audit, blobs, `llm_calls`, `llm_cache`, `page_cache`, `ocr_cache` |
| `reset_cli.py` | Delete every submission (`make reset`) | | |

#### `documents/`: pages, OCR, classification

| Module | Responsibility | Key names |
|---|---|---|
| `triage.py` | Physical triage of each file into pages: kind (text, scan, photo, sheet, email_body) and quality band | `TRIAGE_VERSION = "5"`, `triage()`, `triage_pdf()`, `triage_image()`, `triage_text()`, `triage_xlsx()`, `TriageConfig`, `PageResult`, `OcrScope`, `TriageError`, `DAMAGED` |
| `pdf.py` | Typed adapter over PyMuPDF; the only file that touches it; finds hidden text | `iter_pages()`, `PdfPage`, `Word`, `HiddenSpan`, `PdfError`, `TINY_FONT_PT = 2.0`, `WHITE = 0.95` |
| `imaging.py` | Image quality measures on a grayscale copy at fixed width | `sharpness()`, `contrast()`, `colorfulness()`, `white_ratio()`, `estimate_skew()`, `looks_sideways()`, `open_image()` (EXIF orientation), `thumbnail_png()`; `ANALYSIS_WIDTH = 1200` |
| `prep.py` | Make scans and photos readable before reading: flatten a photographed page, turn upright (by OCR), deskew, even out light; keep the original | `prepare()`, `find_page()`, `flatten()`, `upright_turn()`, `even_light()`, `for_ocr()`, `PrepConfig`, `Prepared` |
| `ocr.py` | Typed adapter over RapidOCR (PP-OCR on ONNX Runtime, local) | `read_words()`, `warm_up()`, `OcrCache`, `OcrWord`, `DEFAULT_MODELS = "PP-OCRv6/small"`, `ENGINE` |
| `layout.py` | Readable text from stored page layouts (word boxes, worksheet grids) | `load_layout()`, `words_to_text()`, `sheet_to_text()` |
| `signals.py` | Keyword votes per doc type and a weaker filename hint | `keyword_vote()`, `filename_hint()`, `MIN_SCORE = 3.0` |
| `tier1.py` | Tier 1 classification: printed form identifiers, worksheet headers, key phrases, lookup sheets, from `config/classification.yaml` | `decide()`, `Decision`, `Vocabulary`, `FormIdentifier` |
| `templates.py` | Template matching engine (landmarks in regions, form markers, sheet headers) | `match_page()`, `read_fields()`, `render_title()`, `layout_audits()`, `Template`, `Match`, `FUZZ_MIN = 90` |
| `layouts.py` | Tier 2 known layouts, learned only from documents a person confirmed (none ship) | `active()`, `all_layouts()`, `learn()`, `record_audit()` (a disagreeing model audit disables the layout) |
| `template_tool.py` | CLI to propose and check templates | `propose`, `check` |
| `classify.py` | Tier 3: ask the model per page, fuse with keyword signals, set confidence | `CLASSIFIER_VERSION = "4"`, `PROMPT_NAME = "classify_page"`, `build_request()`, `fuse()`, `fuse_tier1()`, `fuse_layout()`, `settled_by_tier1()`, `settled_by_layout()`, `audited()`, `email_body_call()`, `PageInput` |
| `split.py` | Pages to logical documents; supersession across the submission | `split_pages()`, `mark_supersession()`, `LogicalDoc`, `PageCall`, `SUPERSEDABLE` |
| `types.py` | Business doc types and the page verdict schema | `DocType`, `DOC_TYPE_LABELS`, `PageVerdict` |
| `titles.py` | Unique document titles, refined after extraction | `refine_titles()`, `refine_submission_titles()` |
| `formats.py` | "Different format" versus "misnamed" (a letter in a loss history family) | `same_family()`, `format_note()` |

Writes (through the triage and classify steps): `pages`, `page_cache`, `ocr_cache`, `blobs`, `page_classifications`, `documents`, `layouts`, `audit_events`.

#### `extract/`: reading values with evidence

| Module | Responsibility | Key names |
|---|---|---|
| `catalog.py` | Loads `config/field_catalog.yaml` (+ `config/field_catalog.d/`); generates schemas, prompt field lists, path lookup | `catalog()`, `Catalog`, `DocSpec`, `FieldSpec`, `TableSpec`, `Entry`, `entries()`, `entry_for()`, `pdf_schema()`, `sheet_schema()`, `prompt_fields()` |
| `schemas.py` | Pydantic shapes the model returns. PDF/image: every scalar a `Val` (value, verbatim quote, page). Worksheets: cell references only (`CellRef`, `ColumnMap`) | `Val`, `Row`, `MvrExtraction`, `LicenseExtraction`, `LossRunExtraction`, `LossLetterExtraction`, `IftaExtraction`, `QuoteFormExtraction`, `ApplicationPdfExtraction`, `EmailExtraction`, `DriverSheetExtraction`, `VehicleSheetExtraction`, `ApplicationSheetExtraction`, `DriverListExtraction`, `VehicleListExtraction` |
| `engine.py` | v1 extractor (`EXTRACTOR_VERSION = "1"`), plus shared types and normalization used by v2 | `DocInput`, `DocPage`, `FactDraft`, `ExtractionOutcome`, `normalize_value()`, `resolve_sheet()`, `escalation_cost()` |
| `engine_v2.py` | v2 extractor (`EXTRACTOR_VERSION = "2"`), the default (`extract_v2 = True`) | `build_request()`, `prompt_for()`, `cache_salt()`, `tidy()`, `finish()`, `_confidence()`, `keep_both_reads()` |
| `verify.py` | The second, independent read by code at the value's anchor; typed comparison | `locate()`, `compare()`, `by_label()`, `record_rows()`, `in_block()`, `in_column()`, `own_line()`, `in_statement()`, `anywhere()`, `fold()` (O/0, I/1, S/5, B/8, Z/2), `NAME_WORD_MIN = 90` |
| `ocr_check.py` | OCR word confidence gate on scans and photos; re-read an enlarged crop | `gate()`, `reread()`, `Spot`, `OcrCheck` |
| `validators.py` | Named self-checks from the catalog, and recall checks | `VALIDATORS` (vin, dot, mc, phone, state, license_format, issue_before_expiry, age_at_order, report_after_loss, paid_reserved_incurred, vin_year), `run_validators()`, `recall_checks()` |
| `checks.py` | Arithmetic self-checks against printed totals | `ifta_checks()`, `loss_run_checks()`, `share_checks()`, `run_checks()`, `MONEY_TOLERANCE = 0.05` |
| `grounding.py` | Find a quote on page word boxes (exact or fuzzy) | `FUZZY_THRESHOLD = 90`, `PageWords`, `Grounding` |
| `sheets.py` | Resolve model cell references against stored worksheet grids (merged ranges resolve to top-left) | `Workbook`, `Sheet`, `CellValue` |
| `service.py` | Run extraction for a submission and persist | `extract_submission()`, `_extract_v2()`, `reads_strong_first()`, `pending_escalations()`, `approve_escalations()`, `load_input()`, `ExtractSummary` |

Writes: `document_extractions`, `facts`, `escalation_approvals`, `llm_calls`, `llm_cache`, `audit_events`.

#### `resolve/`: one evidence board per submission

| Module | Responsibility | Key names |
|---|---|---|
| `build.py` | Facts and extracted data to entities and candidate values, by doc type and field meaning | `Builder`, `DriverObs`, `VehicleObs`, `term_years()`, `loss_run_line()`, `commercial_flag()` |
| `board.py` | Board model and the field state decision | `Board`, `Entity`, `Field`, `Candidate`, `Source`, `resolve_field()` (states `known`, `conflict`, `invalid`, `missing`, `unverified`) |
| `compare.py` | Per-kind comparators returning (key, norm, display) so formatting never creates conflicts | `COMPARATORS` (text, company, entity_form, person, date, money, number, year, ...), `compare()`, `person_key()`, `same_person()` |
| `derive.py` | Whole-submission rules: supersession by date, validators, mappings, derived conflicts, aggregates (IFTA, coverage history, UM, commodities) | `finalize()`, `SUPERSEDE_BY_DATE`, `CONFLICT_TYPES`, `HINTS`, `aggregate_ifta()`, `coverage_history()` |
| `missing.py` | Missing or incomplete items in request language | `find_missing()`, `MissingItem`, `HELD_BY` |
| `unread.py` | Sources that could not be read (so "nothing found" is not "nothing there") | `unread_sources()`, `UnreadSource` |
| `severity.py` | Conflict severity and order | `severity()`, `conflict_order()` |
| `tiers.py` | Missing item tier: required, bind, helpful (from `Settings.missing_tiers`) | `tier_of()` |
| `service.py` | Run resolution and persist; apply people's choices; stable ids | `resolve_submission()`, `build_board()`, `decides()`, `stable_id()` |

Entity kinds built: `applicant`, `agency`, `coverage_request`, `operations`, `fleet`, `coverage_history` (singletons), and `driver`, `vehicle`, `coverage_term`, `claim`, `ifta_quarter`.
Writes: `resolved_entities`, `resolved_fields`, `conflicts`, `missing_info`, `audit_events` (reads `field_resolutions`).

#### `rules/`: the guideline as code plus data

| Module | Responsibility | Key names |
|---|---|---|
| `model.py` | Outcome enum, ruleset file format, loading from `backend/rules/` | `Outcome` (pass, flag, refer, decline, unknown, n/a), `SEVERITY`, `RuleSpec`, `Reading`, `Ruleset`, `Verdict`, `RuleResult` |
| `predicates.py` | One predicate per rule; read resolved facts only; never call a model | `REGISTRY`, `predicate()` decorator, `RuleContext`, `power_unit_counts()`, `team_for()`, `route_rows()`, and one function per rule (for example `has_fein`, `loss_runs_current`, `drivers_hold_cdl`, `final_route`) |
| `view.py` | What rules can see: resolved fields, read-tracked and overridable for sensitivity | `SubmissionView`, `FieldView`, `EntityView`, `TrackedDetails`, `unverified_reads()`, `UnverifiedRead` |
| `engine.py` | Evaluate every rule, apply the safeguards, decide the recommendation | `evaluate()`, `pick_anchor()`, `Decision`, `_sensitivity()`, `_decline_needs_certainty()`, `_readings()`, `_unread()`, `_unverified()`, `_decide()`, `_final_route()` |
| `points.py` | Driver points in the guideline windows | `count()`, `events()`, `event_date()` |
| `service.py` | Run the ruleset and persist | `decide_submission()`, `ruleset()` (cached), `outcome_counts()` |

Writes: `rule_results`, `decisions`, `conflicts.blocking_rules`, `rule_confirmations` (from the rules step), `audit_events`.

#### `analyze/`: the expert review (optional step)

| Module | Responsibility | Key names |
|---|---|---|
| `context.py` | The minimized brief: resolved facts, conflicts, missing items, rule results and computed figures, each with a citation id; no raw documents | `build_brief() -> Brief`, `Ref`, `_Figures` (growth, claim rates, terms, mileage) |
| `engine.py` | Ask the model, validate citations, retry once with the problems | `analyze()`, `check()`, `footnoted()`, `first_request()`, `estimate_usd()`, `AnalystOutput`, `Finding`, `Analysis`, `PROMPT = "analyze_submission"`, `DEGRADED_MESSAGE` |
| `service.py` | Spend approval, persistence, finding review | `analyze_submission()`, `needs_approval()`, `approve_analysis()`, `review_finding()`, `review_is_stale()` |

Writes: `analyses`, `findings`, `llm_calls`, `llm_cache`, `audit_events`.

#### `actions/`: the agent request

| Module | Responsibility | Key names |
|---|---|---|
| `build.py` | Request items built in code from rule results, missing items, open conflicts and accepted findings | `build_items()`, `Item`, `GROUP_ORDER = (before_quote, before_binding, inform)`, `conflict_details()` |
| `email.py` | Draft email; the model words opening, closing and one sentence per item; code adds details and checks | `draft_email()`, `render()`, `items_sha()`, `Draft`, `DraftOut`, `PROMPT = "draft_agent_email"`, `_INTERNAL` leak check |
| `asks.py` | What the request asks for each rule | `asks_by_rule()` |
| `points.py` | Measure a reply against what the agent was asked | `baseline()`, `item_rows()`, `points_of()`, `diff()`, `PointDiff` |
| `service.py` | Persist items and draft; keep a person's include/exclude choices | `build_actions()`, `included_sha()`, `parties()`, `ActionsResult` |

Writes: `agent_request_items`, `email_drafts`, `llm_calls`, `llm_cache`.

#### `llm/`: the one door to models

| Module | Responsibility | Key names |
|---|---|---|
| `gateway.py` | Typed request in, schema-validated output out, cached and recorded | `LlmGateway.run_many()`, `LlmGateway.cached()`, `LlmRequest`, `LlmResult`, `TextPart`, `ImagePart`, `AnthropicTransport`, `Transport` protocol, `LlmUnavailable`, `cache_key()`, `wrap_untrusted()`, `wire_schema()`, `blank_to_none()`, `fit_image()`, `FALLBACK_MODELS`, `NOT_CACHED` |
| `prompts.py` | Versioned prompt files `backend/prompts/<name>.v<N>.md`; highest version wins | `load_prompt() -> Prompt(name, version, sha256, text)` |

Writes: `llm_calls`, `llm_cache`.

#### `pipeline/`: orchestration

| Module | Responsibility | Key names |
|---|---|---|
| `registry.py` | The ordered step list | `PIPELINE` |
| `runner.py` | Run steps in order, record each, emit events, fingerprint the run | `process_submission()`, `StepDef`, `StepContext`, `StepOutcome`, `TransientStepError`, `interrupt_run()`, `recover_interrupted()`, `ACTOR = "system:pipeline"` |
| `queue.py` | Jobs in the database under leases | `enqueue()`, `claim()`, `renew()`, `finish()`, `Claimed` |
| `background.py` | Start long work inline (thread) or as a job | `start()`, `process()`, `replay()`, `analysis()` |
| `fingerprint.py` | Input hash, versions, canonical output hash and per-section hashes | `input_hash()`, `versions()`, `output()`, `output_hash()`, `section_hashes()`, `stamp()` |
| `triage_step.py`, `classify_step.py`, `extract_step.py`, `resolve_step.py`, `rules_step.py`, `analyze_step.py`, `actions_step.py` | One function per step | `triage_step()`, `classify_step()`, `document_id()`, `extract_step()`, `resolve_step()`, `rules_step()`, `analyze_step()`, `actions_step()` |
| `cli.py` | `python -m lane.pipeline.cli process|pages|documents ...` | |

Writes: `runs`, `steps`, `jobs`, `worker_heartbeats`, `submissions.status`, `audit_events`.

#### `review/`: people's changes and replay

| Module | Responsibility | Key names |
|---|---|---|
| `service.py` | Resolve or reopen a conflict, override or clear a field, then rerun resolve and rules synchronously | `resolve_conflict()`, `reopen_conflict()`, `override_field()`, `clear_override()`, `rerun()`, `RERUN = ("resolve", "rules")`, `Rerun`, `Change`, `OverrideError` |
| `replay.py` | Deterministic replay against a scratch copy of the database with cache-only model calls | `start()`, `replay()`, `Verdict` |

Writes: `field_resolutions`, `conflicts.resolution`, `runs`, `steps`, `audit_events`.

#### Other packages

| Package / module | Responsibility | Key names |
|---|---|---|
| `overview/service.py` | The decision brief: route, summary, blockers, key numbers, counts | `blockers()`, `key_numbers()`, `group_counts()`, `checks_at_quote()`, `rule_kind()`, `run_cost()`, `Blocker`, `KeyNumber` |
| `audit/chain.py` | Hash-chained append-only audit log | `record()`, `verify()`, `compute_hash()`, `GENESIS`, `APPEND_LOCK = 0x4C414E45` |
| `audit/describe.py` | Audit events as plain sentences, PII masked | `summary()`, `kind()` |
| `events/bus.py` | In-process live event buffer per submission | `Bus`, `Event`, `BUS`, `capacity = 2000` per submission, `Bus.forward` hook |
| `events/notify.py` | Events between processes over Postgres LISTEN/NOTIFY | `Publisher`, `listen()`, `start_listening()`, `available()`, `encode()`, `LIMIT = 7900` bytes |
| `storage/blobs.py` | Content-addressed store, local folder or S3/MinIO, key = sha256 | `BlobStore`, `S3BlobStore`, `blob_store()`, `sha256_hex()` |
| `storage/records.py` | Write bytes and the `blobs` row in one call | `ensure_blob()` |
| `security/injection.py` | Scan visible and hidden page text for instructions; record hidden spans | `scan()`, `record()`, `record_hidden()`, `ACTION = "security.injection_detected"`, `HIDDEN_ACTION = "security.hidden_text"` |
| `domain/normalize.py` | Deterministic parsing of numbers, money, percent, dates, yes/no, absence | `number()`, `money()`, `percent()`, `to_date()`, `yes_no()`, `is_absent()` |
| `domain/vin.py` | VIN check digit (position 9), model year (position 10), WMI maker, offline | `check()`, `model_year()`, `edit_distance_one()` |
| `domain/ages.py` | Ages counted from the application date; masked DOB differences | `age_on()`, `application_date()`, `difference()` |
| `domain/cdl.py` | Earliest evidence of a CDL | `earliest_cdl()`, `describe()` |
| `domain/geo.py` | State centers and great-circle miles | `miles_between()`, `to_code()` |
| `domain/taxonomy.py` | Industry vocabularies: commodity classes, questions, entity forms | `commodity_class()`, `question_key()`, `entity_form_from_name()` |
| `domain/order.py` | Stable natural ordering for hashed outputs | `natural_key()`, `stable_items()` |
| `db/base.py` | Engine, sessions, declarative base; SQLite pragmas (WAL, foreign keys); Postgres pool 20 + 20 overflow, UTC sessions | `get_engine()`, `get_sessionmaker()`, `get_session()`, `Base`, `TimestampMixin` |
| `db/models.py` | All 35 ORM tables | see the data model section |
| `db/scratch.py` | Scratch copy for replay and what-if scoring; SQLite to Postgres move | `copy_rows()`, `scratch_copy()` |
| `evals/*` | Offline evaluation CLIs (not on the request path): `score.py`, `gold.py`, `perturb.py`, `follow_ups.py`, `extraction_report.py`, `decision_gold.py`, `compare_runs.py`, `ocr_bench.py`, `layout_speed.py`, `verify_offline.py`, `render.py` | Reports under `evals/report/`, read by `api/evals.py` |

### API

#### Application factory and middleware order

`backend/src/lane/api/main.py` `create_app()` builds the app with `generate_unique_id_function=_operation_id` (the operation id is the handler name, so the generated client reads naturally). Middlewares are added in this order: `CORSMiddleware`, `scrub_personal_data`, `require_sign_in`, `GZipMiddleware(minimum_size=1024)`. Starlette wraps each new middleware around the previous ones, so a request passes through them outermost first:

Order of the middlewares: a request passes GZip, then `require_sign_in`, then `scrub_personal_data`, then CORS, then the router and its handler; the response passes back through the same middlewares in reverse order.


| Middleware | Function | Behavior |
|---|---|---|
| GZip | `GZipMiddleware` | Compresses bodies over 1024 bytes after the privacy pass; leaves `text/event-stream` alone |
| Sign-in requirement | `require_sign_in` | When `LANE_REQUIRE_SIGN_IN` is true: every `/api/v1/` path except `health` and `auth/*` (regex `_OPEN`), and except `OPTIONS`, needs a valid token from the `Authorization: Bearer` header or the `access_token` query parameter; else 401 "Sign in to use Lane". Off by default |
| Privacy scrub | `scrub_personal_data` | Only for paths matching `^/api/v1/(submissions|documents|fields|pages|facts|request-items)` with JSON bodies. Masks SSNs (`mask_ssn`) in every such response, reveals included. Then, except for paths ending `/reveal`, replaces every driver identifier the database holds (`privacy.pii_texts()`) with its masked form. If the identifiers cannot be read, the response is withheld with 503 |
| CORS | `CORSMiddleware` | `allow_origins = settings.cors_origins` (default `http://localhost:3000`), all methods and headers |

Lifespan (`Lifespan` class): checks secrets (`check_secrets()` refuses the default session secret outside `LANE_ENV=dev`). In queue mode it only starts the NOTIFY listener. In inline mode it marks runs still `running` as failed (`recover_interrupted`), warms OCR models on a thread, and starts the Mailpit watcher when `LANE_MAILPIT_URL` is set.

Shared dependencies (`api/deps.py`): `SessionDep` (one session per request, closed before the response is sent), `SettingsDep`, `BlobStoreDep`.

#### Routers

All routes are under `/api/v1`. "Permission" is the dependency in the handler signature (`api/auth.py`); blank means any caller (a viewer, or anonymous when sign-in is not required).

| Router file | Prefix | Endpoints (method path -> operation id) | Permission |
|---|---|---|---|
| `main.py` | `/api/v1` | GET `/health` -> `get_health` (database ok or unavailable, llm configured or missing_key) | none |
| `intake.py` | `/api/v1/intake` | POST `/eml` (body `message/rfc822`, streamed, 413 over the size limit) -> `ingest_eml`; 201 new, 200 resend | none |
| `submissions.py` | `/api/v1` | GET `/submissions` -> `list_submissions`; DELETE `/submissions/{id}` -> `delete_submission`; POST `/submissions/{id}/restart` -> `restart_submission`; POST `/submissions/{id}/attach` -> `attach_follow_up`; POST `/submissions/{id}/keep-separate` -> `keep_separate`; POST `/submissions/{id}/senior-review` -> `request_senior_review`; POST `/rule-confirmations/{id}/confirm` -> `confirm_rule_change`; POST `/submissions/{id}/decision` -> `record_decision` | delete (DELETE, restart); review (attach, keep-separate, senior-review, confirm, decision) plus decision authority |
| `overview.py` | `/api/v1` | GET `/submissions/{id}` -> `get_overview`; GET `/submissions/{id}/emails/{email_id}` -> `get_submission_email` | email: view_content |
| `pages.py` | `/api/v1` | GET `/submissions/{id}/pages` -> `list_pages`; GET `/pages/{id}/image` (variants full, thumb, original; license photos blurred unless `reveal`) -> `page_image`; GET `/pages/{id}/layout` -> `page_layout`; GET `/submissions/{id}/runs` -> `list_runs`; POST `/submissions/{id}/process` -> `process_submission`; POST `/submissions/{id}/replay` -> `replay_submission` | image, layout: view_content (photo reveal: reveal_pii); process, replay: process |
| `documents.py` | `/api/v1` | GET `/submissions/{id}/documents`; GET `/document-types`; POST and DELETE `/documents/{id}/label` -> `label_document`, `clear_document_label` (both rerun the pipeline with trigger `relabel`); GET `/known-layouts`; POST `/documents/{id}/confirm` -> `confirm_document` (learns layouts) | label, confirm: review |
| `extractions.py` | `/api/v1` | GET `/submissions/{id}/extractions` (data only with view_content); GET `/documents/{id}/facts`; GET `/submissions/{id}/escalations`; POST `/submissions/{id}/escalations/approve` -> `approve_escalations` (reruns with trigger `escalation`) | facts: view_content; approve: approve_spend |
| `resolution.py` | `/api/v1` | GET `/submissions/{id}/entities`, `/conflicts`, `/missing`; POST `/conflicts/{id}/resolve`; DELETE `/conflicts/{id}/resolution` -> `reopen_conflict`; POST and DELETE `/fields/{id}/override` -> `override_field`, `clear_override` (each reruns resolve and rules synchronously) | writes: review |
| `evidence.py` | `/api/v1` | GET `/fields/{id}` -> `get_field`; GET `/facts?ids=` -> `list_facts_by_id`; POST `/fields/{id}/reveal` -> `reveal_field` (audited) | facts: view_content; reveal: reveal_pii |
| `rules.py` | `/api/v1` | GET `/submissions/{id}/rules`; GET `/ruleset` -> `get_ruleset` | none |
| `analysis.py` | `/api/v1` | GET `/submissions/{id}/analysis`; GET `/submissions/{id}/findings`; POST `/findings/{id}/review` -> `review_finding` (then rebuilds request items without redraft); POST `/submissions/{id}/analysis/approve` (202, starts work kind `analysis`) | review; approve: approve_spend |
| `actions.py` | `/api/v1` | GET `/submissions/{id}/actions`; PATCH `/request-items/{id}` -> `set_request_item_included`; POST `/submissions/{id}/actions/mark-sent` -> `mark_request_sent` (writes `request_snapshots`); POST `/submissions/{id}/actions/redraft` -> `redraft_email` | GET: view_content; writes: review |
| `audit.py` | `/api/v1` | GET `/submissions/{id}/audit` -> `get_audit` (events described in words, plus whole-chain verification) | none |
| `events.py` | `/api/v1` | GET `/submissions/{id}/events` -> `stream_events` (SSE) | none in the handler; the sign-in middleware applies |
| `how.py` | `/api/v1` | GET `/submissions/{id}/how` -> `how_lane_works` (stages, proofs, people steps, figures) | none |
| `ops.py` | `/api/v1/ops` | GET `/health` -> `get_ops_health` (runs, step timings, model calls, cost, failures); GET `/queue` -> `get_ops_queue` (workers by heartbeat, jobs and leases, spend today) | ops |
| `evals.py` | `/api/v1/evals` | GET `` -> `list_evals`; GET `/latest`; GET `/perturbations`; GET `/{name}` (reads `evals/report/*.json`) | none |
| `auth.py` | `/api/v1/auth` | GET `/people` -> `list_demo_people`; POST `/demo-login` -> `demo_login`; GET `/me` -> `get_session`; GET `/config` -> `get_sign_in_config`; POST `/cognito` -> `cognito_login` | none |

Supporting API modules with no router: `api/privacy.py` (masking), `api/display.py` (text tidied for people; never seen by resolution or rules), `api/models.py` (typed shapes of stored JSON for the generated client), `api/summaries.py` (typed per-kind summaries of resolved entities, thresholds from the ruleset params).

#### Auth, roles, permissions, decision authority

Tokens (`api/auth.py`): a Lane session token is `base64url(json{role, exp, name?})` + `.` + HMAC-SHA256 hex over the body with `LANE_SESSION_SECRET`; lifetime `session_ttl_hours = 12`. `read_claims()` checks the signature with `hmac.compare_digest`, the role, and expiry. `get_viewer()` reads the bearer header, or `access_token` in the query (for `<img>` and `EventSource`, which cannot send headers). No token gives `Viewer(demo_default_role="viewer", signed_in=False)`; a bad or expired token is 401.

Demo sign-in: `/auth/people` lists `PEOPLE` (priya underwriter, daniel senior_underwriter, guest viewer, sam ops, alex admin); `/auth/demo-login` issues a token. Both return 404 when `LANE_DEMO_LOGIN` is false.

Cognito exchange: `/auth/cognito` takes an ID token, fetches the pool's JWKS (`PyJWKClient`, cached one hour), verifies RS256, audience `cognito_client_id`, issuer `https://cognito-idp.<region>.amazonaws.com/<pool>`, required claims `exp iat iss aud sub`, and `token_use == "id"`. The role is the first of `ROLE_ORDER` (senior_underwriter, underwriter, admin, ops, viewer) found in `cognito:groups`; none gives 403. Lane then issues its own HMAC token, so everything downstream is identical for both sign-ins.

| Role | Permissions (`PERMISSIONS`) | Decision authority (`Settings.decision_authority`) |
|---|---|---|
| underwriter | view_content, reveal_pii, review, process, approve_spend, delete | decline, refer_back, quote |
| senior_underwriter | same as underwriter | decline, refer_back, quote, quote_at_risk, confirm_cleared_decline |
| viewer | view_content | none |
| ops | process, ops | none |
| admin | view_content, ops, evals | none |

Dependencies per permission: `ContentViewer`, `Reviewer`, `Revealer`, `Processor`, `SpendApprover`, `Deleter`, `OpsViewer`. `record_decision` requires `quote_at_risk` instead of `quote` when the submission is at risk (route is decline or potential decline, or a cleared decline awaits confirmation); `confirm_rule_change` requires `confirm_cleared_decline` (four eyes). The `evals` permission exists in the matrix but no endpoint checks it.

#### Live events

How live progress travels, step by step:

1. Step code calls `ctx.emit()`, which writes to the in-process event bus (`events/bus.py`, a deque of 2,000 events per submission).
2. Inline mode: the API is that process, so the event is already there. Queue mode: the worker's bus forwards it with `pg_notify` on the channel `lane_events` (payload at most 7,900 bytes).
3. The API's lifespan task (`notify.start_listening()`) runs `LISTEN lane_events` and republishes each event into its own bus.
4. `GET /submissions/{id}/events` streams them as `text/event-stream`: first a `snapshot` message (submission, attachments, latest run, decision), then new events every 0.2 s (`events_poll_s`), a keep-alive comment every 15 s (`events_heartbeat_s`), and `end` after 5 s of quiet once the run has finished (`events_idle_close_s`). A reconnect resumes from the `Last-Event-ID` header.


Event types emitted by the pipeline: `run.started`, `step.started`, `step.finished`, `page.ready`, `security.hidden_text`, `security.injection`, `document.found`, `document.extracted`, `conflict.found`, `missing.found`, `rule.evaluated`, `decision.made`, `analysis.done`, `actions.ready`, `run.finished`.

### The pipeline

#### Steps

`backend/src/lane/pipeline/registry.py` `PIPELINE`, run in order by `runner.process_submission()`. Every step is version `"1"` and `max_attempts = 1`. A failed required step stops the run (`failed`); a failed optional step or a `degraded` step makes the run `degraded`.

| # | Step (file) | Required | Inner versions | Reads | Writes | Model (purpose, setting, default id) | Without a model |
|---|---|---|---|---|---|---|---|
| 1 | triage (`triage_step.py`) | yes | `TRIAGE_VERSION = "5"` | accepted `attachments`, blobs | `pages`, `blobs` (render, thumb, original, layout JSON), `page_cache`, `ocr_cache`, `attachments.read_error`, audit (`attachment.triaged`, `attachment.unreadable`, `attachment.damaged`, `security.hidden_text`, `security.injection_detected`) | none (local RapidOCR) | unaffected |
| 2 | classify (`classify_step.py`) | yes | `CLASSIFIER_VERSION = "4"` | pages, layouts, `layouts` table, `document_labels` | `page_classifications`, `documents` (stable id `sha256(submission:key)[:32]`), `layouts` audit counts; deletes facts, extractions and approvals of documents that vanished | `classify_page`, `llm_classify_model` = `claude-haiku-5-5`, effort low | keyword signals carry text pages; image pages go to review; step `degraded` |
| 3 | extract (`extract_step.py`) | yes | `EXTRACTOR_VERSION = "2"` (v2) | documents, pages, layouts, `escalation_approvals` | `document_extractions`, `facts`, refined `documents.title` | `extract_<doc_type>`, `llm_extract_model` = `claude-haiku-5-5` (effort medium); stronger `llm_escalation_model` = `claude-sonnet-5-5` | every document saved `failed` "AI extraction unavailable"; step `degraded` |
| 4 | resolve (`resolve_step.py`) | yes | | facts, extractions, `field_resolutions`, unread sources | `resolved_entities`, `resolved_fields`, `conflicts`, `missing_info` | none | unaffected |
| 5 | rules (`rules_step.py`) | yes | ruleset `version: 1.1.0` + sha256 | resolved fields, conflicts | `rule_results`, `decisions`, `conflicts.blocking_rules`, `rule_confirmations`, audit `submission.decided`, `rule_change.pending` | none | unaffected |
| 6 | analyze (`analyze_step.py`) | no | prompt `analyze_submission.v1` | the brief | `analyses`, `findings` | `analyze`, `llm_analyze_model` = `claude-opus-5-5`, effort high, 16000 max tokens | status `degraded` with `DEGRADED_MESSAGE`; the last good analysis stays visible; step `degraded`. Awaiting spend approval makes the step `skipped` |
| 7 | actions (`actions_step.py`) | yes | prompt `draft_agent_email.v1` | rule results, missing, conflicts, accepted findings | `agent_request_items`, `email_drafts` | `draft_email`, `llm_draft_model` = `claude-sonnet-5-5`, effort low | plain template with the same structure (`method = "template"`) |

Steps are idempotent: triage reuses existing pages of the same triage version, and the classify step upserts documents by stable id so surviving documents keep their facts and approvals.

#### Run records and fingerprints

`process_submission()` creates a `runs` row (trigger one of `intake`, `follow_up`, `rerun`, `relabel`, `escalation`, `resolution`, `reopen`, `override`, `replay`, `cli`), sets the submission `processing`, writes audit `run.started`, then one `steps` row per step (`step.<status>` audit event each). At the end `fingerprint.stamp()` records:

- `input_hash`: sha256 of the raw email blob hash and the sorted attachment blob hashes.
- `versions`: step versions, the ruleset version and sha256 prefix, every prompt name, version, sha prefix and model used by the run's model calls, and per-section output hashes.
- `output_hash`: sha256 of a canonical dump (no ids, no timestamps) of documents, facts, fields, conflicts, missing codes, rules, decision, findings, request items and the latest draft.

Audit `run.finished` carries both hashes.

#### Replay

`POST /submissions/{id}/replay` calls `review/replay.start()` (a `runs` row with trigger `replay` and `replay_of` the latest base run), then work kind `replay`. `review/replay.replay()` copies the database into a scratch copy (`db/scratch.py`; a temporary SQLite file or a temporary Postgres database filled through the models), runs the whole `PIPELINE` there with `llm_cache_only = True` and `replay = True` (triage reads every page again, OCR cache off), and records only the verdict (status, hashes, versions, copied steps, model calls) in the real database. A model call not in the cache is recorded with error `NOT_CACHED` and never sent.

#### Job queue (queue mode)

`Settings.run_mode` is `inline` (default: the API runs work on a FastAPI background thread) or `queue` (a `jobs` row, run by `python -m lane.worker`). `pipeline/background.start()` chooses. Job kinds: `process`, `replay`, `analysis`.

| Mechanism | Code | Values |
|---|---|---|
| Enqueue | `queue.enqueue()`; audit `job.queued` | status `queued` |
| Claim | `queue.claim()`: oldest job that is `queued`, or `running` with `lease_until < now`, `ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED`; audit `job.claimed` with `lease_ran_out_for` | `worker_lease_s = 30` |
| Retries | A reclaimed job with `attempts >= worker_max_attempts` is closed `failed` ("Stopped after N attempts") | `worker_max_attempts = 3` |
| Lease renewal | `worker._keep_lease()` thread calls `queue.renew()` (UPDATE where owner and running); returns false when taken over | every `worker_renew_s = 10` |
| Resume | On a reclaimed job the worker calls `interrupt_run()` on the cut-off run (`job.run_id`, kept via `on_run`), then runs the pipeline again; steps skip finished work | |
| Poll | Idle wait between claims | `worker_poll_s = 1` |
| Heartbeat | `Presence.keep()` upserts `worker_heartbeats` (owner `host:pid`, `seen_at`, `job_id`, `jobs_done`); deleted on clean stop | every 5 s |
| Finish | `queue.finish()`; audit `job.done` or `job.failed` | |

In inline mode, `recover_interrupted()` at API startup marks every `running` run failed with the reason "Interrupted: the service restarted...".

### Intake

#### Ways in

| Way in | Entry point | Source label | Starts processing via |
|---|---|---|---|
| Upload | `POST /api/v1/intake/eml` (`api/intake.py`), frontend `uploadEmail()` in `frontend/src/lib/api/index.ts` | `upload` | `work.start(..., "process", process_id, trigger)` |
| CLI | `python -m lane.intake.cli path.eml ...` (`intake/cli.py`) | `cli` | nothing: it only ingests and prints counts; processing is started separately (`python -m lane.pipeline.cli process`) |
| Mailpit | `intake/mailpit.py` `watch()` polls every `mailpit_poll_s = 5` s; started by the API lifespan (inline) or the worker (queue) | `smtp` | `background.process` (inline) or `queue.enqueue` (queue) |
| SES S3 inbox | `intake/ses.py` `S3Mailbox` over `s3_bucket/ses_inbox_prefix`, same `mailpit.watch()` loop; started by the worker when `ses_inbox_prefix` is set | `smtp` (through `poll_once`) | `queue.enqueue` |

SES gate (`ses.held_reason()`), checked before ingest: `X-SES-Virus-Verdict` must be PASS, `X-SES-Spam-Verdict` must be PASS, the From address must be on `intake_allowed_senders` (an address or `@domain`), and `Authentication-Results` must show DMARC pass or a DKIM pass for the From domain. Anything else moves to `held/`; ingested messages move to `done/`.

#### Ingest (`intake/service.py`)

1. Reject the whole email if larger than `intake_max_email_mb = 50` MB, or if `sniff()` does not say EML.
2. Parse (`eml.parse_eml`). Idempotency key: `mid:<Message-ID>`, else `sha:<sha256 of raw>`. An existing key returns the existing submission (audit `email.resent`, HTTP 200).
3. Store the raw email as a blob; create `email_messages` and `submissions` rows; audit `email.received`.
4. Add the body as an attachment with role `email_body`, then every part as role `attachment`.
5. For each file: empty is `rejected`; store the blob; same sha256 as an earlier file in this email is `duplicate` (with `duplicate_of_id`); a zip is expanded (members get role `archive_member`, `depth + 1`); an unsupported kind is `accepted` with a reason ("not read automatically yet"); else `accepted`.
6. Audit `intake.completed` with counts; then `link.place()` (unless `link_related=False`), commit.
7. A concurrent ingest of the same email that loses the unique-key race returns the winner's submission.

#### Magic-byte sniffing (`intake/sniff.py`)

From the first bytes only: `%PDF-` PDF; `FF D8 FF` JPEG; PNG signature; `GIF87a`/`GIF89a`; `II*\0`/`MM\0*` TIFF; `RIFF....WEBP`; `ftyp` + `heic|heix|mif1|heim` HEIC; `PK\3\4` or `PK\5\6` opens the zip and looks for `xl/workbook.xml` (XLSX) or `word/document.xml` (DOCX), else ZIP; text with a `From:` header plus `Message-ID:` or `MIME-Version:` in the first 64 KB header block is EML; UTF-8 without NUL is TEXT; else UNKNOWN. `SUPPORTED_KINDS` = PDF, XLSX, TEXT and the six image kinds; DOCX, ZIP members of other kinds and UNKNOWN are stored but not read.

#### Zip limits (`intake/archive.py`, values from `config.py`)

| Limit | Setting | Value |
|---|---|---|
| Nesting depth | `intake_max_archive_depth` | 3 (an archive at depth 3 is rejected "nested too deeply") |
| Members per archive | `intake_max_archive_members` | 500 (whole archive rejected over it) |
| Per file | `intake_max_file_mb` | 100 MB |
| Total expanded | `intake_max_expanded_mb` | 500 MB |
| Compression ratio | `intake_max_compression_ratio` | 200 |

Per member also: `__MACOSX/`, `.DS_Store`, `Thumbs.db`, `desktop.ini` ignored; absolute paths, drive letters and `..` rejected; encrypted members rejected; symbolic links rejected; at most cap + 1 bytes read, so a member that expands beyond its declared size is rejected. Members stay in memory and are stored by hash, never written by name.

#### Duplicate detection

Three levels: the same email (idempotency key), the same bytes inside one email (`first_by_sha`, status `duplicate`), and the same files across submissions (`link.place` step 1, relation `same_files`, nothing processed). When files are moved into a target by `link._attach`, a file the target already holds becomes `duplicate` of the earlier one. Downstream, triage reuses pages of any file read before by sha256 (`page_cache`, or a twin attachment).

#### Follow-up linking order (`intake/link.py` `place()`)

Candidates are open submissions: not `failed`, and not themselves folded into another (relation null or `kept_separate`), newest first.

| Order | Test | Result | `relation_how` |
|---|---|---|---|
| 1 | Every file hash of the new email is already in an open submission (archives and members included) | `same_files`; nothing processed; new submission marked ready | `files` |
| 2 | Subject or body holds `[Ref LN-xxxxxxxx]` (regex `TOKEN`) matching an open submission's id prefix | `follow_up`: files moved to the target, which reruns (trigger `follow_up`) | `reference` |
| 3 | `In-Reply-To` or `References` names a Message-ID of an open submission or its follow-ups | `follow_up` | `thread` |
| 4 | A DOT, MC or FEIN (regex `IDENTITY`, from subject, body, first 3 PDF pages) matches the applicant of an open submission, and the sender domain matches | `follow_up` | `identity` |
| 4b | Same identity, different domain | `possible_follow_up` (a person attaches or keeps separate) | `identity` |
| 5 | The applicant's legal name (suffixes like LLC stripped, at least 6 characters) appears in the subject or text | `possible_follow_up` | `name` |
| none | | New submission, trigger `intake` | |

The reference `LN-` + first 8 hex characters of the submission id is stamped into every draft subject by `tag_subject()` (`actions/email.py`, `api/actions.py`). People act on suggestions with `POST /submissions/{id}/attach` (audit `submission.amended`) or `/keep-separate` (audit `submission.kept_separate`).

### Documents and OCR

#### Triage (`documents/triage.py`, `triage_step.py`)

| File kind | Handling |
|---|---|
| PDF | `pdf.iter_pages(dpi=200)`. A page with at least `triage_text_min_chars = 40` characters has a usable text layer (kind `text`, `text_source = "layer"`, words saved as layout JSON). If images cover at least `triage_scan_image_coverage = 0.6`, the image is judged too ("Image page with an existing text layer; text not verified"). Without a text layer the page is prepared (`prep.prepare`) and OCR'd. A damaged file keeps the pages that survived, with the `DAMAGED` warning and at least `degraded` quality |
| Images | `triage_image()`: EXIF orientation, preparation, OCR |
| XLSX | `triage_xlsx()`: one page per worksheet (kind `sheet`, `sheet_name`, `sheet_hidden`), cell grid saved as layout |
| Text | `triage_text()`: the email body, kind `email_body` |

Kind of an image page: `photo` when colorfulness >= `triage_photo_min_colorfulness = 15` or white ratio <= `triage_photo_max_white = 0.6`, else `scan`.

Quality bands (`_assess_image`): sharpness < `triage_sharp_poor = 40` is `poor` ("Very blurry"); sharpness < `triage_sharp_clean = 120`, or any warning, is `degraded`; else `clean`. Skew at or above `triage_skew_warn_deg = 2.0` warns. OCR unsure of more than 20 percent of words (score < 0.8) warns. Poor quality later caps fact confidence at medium and routes the document to the stronger model first.

Preparation (`documents/prep.py`, on when `triage_prep`): flatten a photographed page (outline area at least `triage_flatten_min_area = 0.35`), turn 90/180/270 by which way OCR reads best, deskew from `triage_deskew_min_deg = 0.5`, even out light when the range is at least `triage_shadow_min_range = 40`. Each step is recorded in words in `pages.prep`; the original is kept as `pages.original_sha256`. OCR gets a contrast-boosted copy upscaled to at least `triage_ocr_min_width = 1700` px.

Hidden text (`documents/pdf.py`): spans in invisible render mode, fully transparent, font under 2.0 pt, or white (all channels >= 0.95) on a white background are removed from the page's words, added as page warnings, recorded once per page as audit `security.hidden_text`, and emitted live.

Injection scanning (`security/injection.py`): after triage, every page's visible words plus its recorded hidden text are matched against `Settings.injection_patterns` (ignore or disregard previous instructions, approve/bind/accept this submission, "you are now an assistant", "system prompt"). Each detection is recorded once per page as audit `security.injection_detected` and emitted as `security.injection`. Detection makes attempts visible; it does not block processing.

OCR engine (`documents/ocr.py`): RapidOCR with PP-OCR models (`triage_ocr_models = "PP-OCRv6/small"`), local on ONNX Runtime; words in reading order with boxes and scores. Warmed up at startup on a thread.

OCR layers on a page: the PDF's own text layer when present (`text_source = "layer"`), else OCR words (`text_source = "ocr"`, `ocr_confidence`). The render (`render_sha256`), thumbnail (`thumb_sha256`, width 320), original (`original_sha256`) and layout JSON (`layout_sha256`) are separate blobs.

Caches:

| Cache | Table | Key | Effect |
|---|---|---|---|
| Page cache | `page_cache` | file sha256 + triage version + page number | A file read before, even in a deleted submission, is not triaged again (`triage_reuse_pages`) |
| Twin reuse | (attachments) | same blob sha256 in any submission | pages copied |
| OCR cache | `ocr_cache` | sha256 of [file sha256, page, OCR engine, models, DPI, prep config, triage version] (`OcrScope.key`) | OCR words reused (`triage_ocr_cache`); off during replay |

#### Classification tiers (`classify_step.py`)

Classification tiers, in order, per page:

1. **Tier 1, deterministic** (`documents/tier1.py`, `config/classification.yaml`). Settled pages (worksheet headers with data, the lookup sheet) get no model call.
2. **Tier 2, known layouts** (`documents/layouts.py`, `templates.match_page`). A complete, unchallenged match outside the audit sample gets no model call. The audit sample (`templates_audit_rate = 0.1`) is also read by the model; a disagreement disables the layout.
3. **Tier 3, the model** (`documents/classify.py`, prompt `classify_page`, `claude-haiku-5-5`), fused with keyword votes (`documents/signals.py`). Agreement raises confidence; disagreement sends the page to review.


Document types (`documents/types.py`): application, quote_form, driver_schedule, vehicle_schedule, mvr, drivers_license, loss_run, loss_history_letter, ifta_return, driver_exclusion (tier 1 only), email_body (set by intake), other. Each page's verdict, signals, method (`tier1`, `layout`, `ai`, `keywords`) and model call id go to `page_classifications`.

Splitting (`documents/split.py` `split_pages`), within one file: a new document starts at the first page, when a page says it starts a new document (worksheets of one type in one workbook stay together), when the type changes, or when both pages show differing identifiers and differing subject names.

Supersession (`mark_supersession`), across the submission: documents of a type in `SUPERSEDABLE` (mvr, drivers_license, loss_run, loss_history_letter, ifta_return) about the same subject and period are copies; the most recent by document date is active, the rest get `superseded_by_id`. Superseded documents are not extracted; supersession is shown, never a conflict.

Filename check: `filename_hint()` gives a type from the filename; `filename_mismatch` is set when no document in the file matches the hint and the two are not one family (`Settings.doc_type_families`).

People's labels: `POST /documents/{id}/label` writes `document_labels`; every classification run applies it (type set, confidence high, review cleared). `POST /documents/{id}/confirm` calls `layouts.learn()` to learn a layout from a confirmed document (`layouts` table with source document, page, who, when).

### Extraction and trust

#### Field catalog (`config/field_catalog.yaml`, version 2)

Doc types with fields: mvr, drivers_license, loss_run, loss_history_letter, ifta_return, quote_form, application, email_body, driver_schedule, vehicle_schedule, plus `config/field_catalog.d/driver_exclusion.yaml`. Per field: `entity`, `attribute`, `type` (identifier, number, money, date, name, enum, text), `tier`, `role` (fact, check, display), `labels` (printed label synonyms, the anchors for the second read), `validators`, `used_by` (rule ids, `conflicts`, `match:<entity>`, `missing:<code>`, `check:<name>`, `screen:<name>`), `shapes` (pdf, sheet), `description`. Tables are records (claims, jurisdictions, drivers, vehicles, MVR events), laid out in a workbook as `columns` or `cells`.

| Tier | Meaning | Needs |
|---|---|---|
| decision | A rule, the route or a missing item reads it | Two reads that agree (verified) |
| crosscheck | Compared across documents (conflicts, matching) | One read plus grounding |
| context | Shown on a screen only | One read plus grounding |

Nothing outside the catalog becomes a fact (`catalog.entry_for`).

#### The two reads (`extract/engine_v2.py`, `extract/verify.py`)

| Page shape | First read | Second, independent read |
|---|---|---|
| Worksheet | The model returns a cell reference (`CellRef`, `ColumnMap`); code reads the value from the stored grid (`extract/sheets.py`) | The cell itself |
| Text layer | The model copies the value verbatim with a quote and page | Code finds the value's anchor on the page words and reads it there |
| Scan or photo | The model reads the image | Code reads OCR words at the same anchor, gated by OCR confidence |

Anchors (`verify.locate`): beside a catalog label (right of it, under it, or before it), in a table record's own rows (`record_rows`, `in_block`), under a column header (`in_column`), as a whole line (`own_line`), as a verbatim statement (`in_statement`), or anywhere for long distinctive values (`anywhere`). Comparison is by type: identifiers equal after folding look-alikes (O/0, Q/0, D/0, I/1, L/1, S/5, B/8, Z/2), numbers and money the same digits, dates the same date (an ambiguous day/month order is marked), names every word present with an OCR-sized typo (score >= 90) allowed. Short values that never ground (3 characters or fewer, yes/no, one number) are recognized by `is_short()`.

OCR gate (`extract/ocr_check.py`): a value read by code on a scan verifies only when its words meet `triage_ocr_min_word_score = 0.85` (every word of an identifier, the mean otherwise); below it the spot is "unreadable" (`grounding = "ocr_unsure"`), neither a confirmation nor a disagreement. A decision value that did not verify is read again from an enlarged crop (`triage_ocr_reread_scale = 2.5`) of its anchor band.

#### Confidence computation (`engine_v2._confidence`)

Rules, applied in order:

- Verified: **high**.
- Not verified but grounded on the page: **medium** for crosscheck and context fields, **low** for decision fields.
- Otherwise: **low**.
- Then the caps:
  - A failed validator on the value: **medium** if verified ("the document says so"), else **low**.
  - Its table failed a recall check: at most **medium**.
  - Its page quality is poor: at most **medium**.
  - An ambiguous day or month: at most **medium**.
- After an escalation where two model reads agree (but code did not check): **medium**, never high.


Each fact stores `confidence` and `confidence_reasons` (the verification note first).

#### Escalation

| Trigger | When | Approval |
|---|---|---|
| Strong first | `reads_strong_first()`: any page graded poor (`extract_strong_first_poor`), or an image of a type in `extract_strong_first_types` (default `drivers_license`) | none; `claude-sonnet-5-5` reads first, `claude-haiku-5-5` gives the second read |
| Auto escalation | A decision field not verified, a recall check that found unread rows, or a validator failing on a single read (`ExtractionOutcome.auto_escalate`) | none; re-read now with the other model; `keep_both_reads()` stores both reads side by side in `facts.reads` |
| Arithmetic failure | IFTA or loss run totals that do not add up | a person: status `needs_escalation` with `escalation_cost_usd`; `POST /submissions/{id}/escalations/approve` (approve_spend) writes `escalation_approvals` and reruns; approved documents use the stronger model on every run |
| Still failing on the stronger model | | status `needs_review`, both reads kept |

Extraction status values: `ok`, `needs_escalation`, `needs_review`, `failed`, `skipped`.

#### Validators and arithmetic checks

Validators (`extract/validators.py`, named in the catalog): `vin` (length, characters, check digit at position 9 via `domain/vin.check`), `vin_year` (position 10 against the model year), `dot` (1 to 8 digits), `mc` (1 to 7 digits), `phone`, `state`, `license_format` (per-state formats in `LICENSE_FORMATS`), `issue_before_expiry`, `age_at_order`, `report_after_loss`, `paid_reserved_incurred`. A validator failing on a value two reads agree on is the document's own error (shown); on a single read it escalates.

Recall checks (`recall_checks`): claims read against a printed claim count, IFTA jurisdiction lines without a record, MVR event lines (regex `_EVENT_LINE`) without an event, worksheet rows outside the mapped table.

Arithmetic (`extract/checks.py`, money tolerance 0.05): IFTA (rows present, each column sums to the printed total, each row's tax equals gallons times rate, subtotal plus penalty plus interest equals total due); loss run (each claim's lines add up to the claim total, claims add up to each term total, claims read match the header count); quote form and application (radius and commodity shares add up to 100 percent).

#### The gate

`resolve/service.decides(fact)` decides whether a fact may feed the board: v1 facts (no tier) need grounding other than `none`; decision-tier facts need `verified`; crosscheck and context facts need grounding not in (`none`, `mismatch`, `ocr_mismatch`). Facts that fail are passed to `Builder` as `ungrounded` and their fields end in state `unverified`. In the rules engine, `_unverified()` turns a `pass` or `n/a` into `unknown` when a value the rule reads was read once and never verified, naming the documents and adding "Confirm <path> on <document>" items. A refer or decline found on verified values stands.

### Resolution, rules, analysis and actions

#### Resolution (`resolve/`)

1. `build_board()`: load documents in order, extraction data, facts; mark facts that fail `decides()`; `Builder(...).build()` maps each document type's fields to entities and candidates, each candidate keeping its document and fact ids; `derive.finalize()` applies supersession by date, validators, mappings between differently shaped forms, derived values (IFTA aggregates, coverage history, reefer from commodities, UM state minimums), multi-valued contact fields, and derived conflicts.
2. `resolve_field()` per field: agreeing candidates (by comparator key) merge; a newer official record supersedes older copies (shown, not queued); unverified candidates give `unverified`; none gives `missing`; one value is `known` (or `invalid` when a validator failed); anything else is `conflict` with all candidates and an authority hint (`derive.HINTS`).
3. `find_missing()` lists missing items with codes (prefixes `APP_`, `Q_`, `VEH_`, `DRV_`, ...), category and the documents that would hold them; unread sources are considered.
4. Persist: delete and rewrite entities, fields, conflicts and missing items with stable ids (`stable_id(submission, kind, key)` for entities, `stable_id(entity, attribute)` for fields, one conflict per field), applying `field_resolutions` (a person's choice or override) during resolution.

Conflict types: `identity`, `value`, `cardinality`, `reference` (settled by code, for example a VIN's own decode). Severity comes from `Settings.conflict_severity`; any conflict that changes a decline-capable rule is high; order is blocking, high, medium, low (`resolve/severity.py`).

#### Rules engine (`backend/rules/knight_trucking.v1.yaml`, `rules/engine.py`)

Ruleset `knight_trucking` version `1.1.0`, source guideline `Knight_Guideline(Mockup).docx`. File sections: `params` (anchor, available and selective states, windows, thresholds, ineligible units, prohibited commodities, unacceptable history patterns), `request_groups` (before_quote, before_binding, inform), `assumptions` A01 to A12, `teams` (small_fleet "Small Fleet underwriting", fleet "Fleet underwriting"), `rules`.

29 rules; 8 are decline-capable (`decline: true`): ELIG-01 to ELIG-06, DRV-01, DRV-06. Two are external (A07): ELIG-06 (SAFER) and SEL-01 (premium per unit). ELIG-02 has an alternative `readings` entry (A03).

| Group | Rules |
|---|---|
| Submission requirements | APP-01 FEIN or SSN, APP-02 MC or docket, DOC-01 loss runs current (60 days), DOC-02 loss runs cover expiring term and 3 prior years, DOC-03 four recent IFTA quarters, DOC-04 current MVR per driver (90 days), DOC-05 vehicle schedule complete |
| Appetite and states | ELIG-01 semi-tractors, ELIG-02 available state, ELIG-03 Texas north of I-10, ELIG-04 prohibited cargo or operations, ELIG-05 50 miles from Mexico border, ELIG-06 SAFER, ELIG-07 new venture |
| Coverage | COV-01 no auto liability deductible, COV-02 physical damage not offered |
| Drivers | DRV-01 valid CDL, DRV-02 two years CDL experience, DRV-03 age 23, DRV-04 medical exam at 65, DRV-05 points (6 in 3 years, 4 in 12 months), DRV-06 unacceptable history |
| Prior insurance | INS-01 three years continuous coverage (lapse <= 30 days), INS-02 losses for every prior term |
| Selective | SEL-01 premium per unit or fleet size, SEL-02 auto haulers and box trucks |
| Routing | DATA-01 documents agree, ROUTE-01 team for fleet size (1-10 small fleet, 11+ fleet), ROUTE-02 final route |

Outcomes (`rules/model.Outcome`): `pass`, `flag`, `refer`, `decline`, `unknown`, `n/a`; severity order n/a < pass < flag < unknown < refer < decline. Counts shown on screens add a separate `potential decline` bucket (`rules/service.outcome_counts`).

Per rule, `evaluate()` runs in this order:

| Step | Function | Effect |
|---|---|---|
| Anchor | `pick_anchor()` | Count windows from the application date (param `anchor`, A02), else effective date, else received date |
| Predicate | `_run()` | Verdict with reason, items, evidence, waiting_on; reads recorded as inputs |
| Unread sources | `_unread()` | A `refer` that rests on something not found becomes `unknown` when a file that could hold it was never read |
| Unverified reads | `_unverified()` | A `pass` or `n/a` becomes `unknown` when a value it reads is unverified |
| Sensitivity | `_sensitivity()` | Re-run once per candidate of each conflicted input; a changing outcome makes that conflict blocking; an `unknown` that is the same under every combination (up to 16) takes that outcome |
| Decline needs certainty | `_decline_needs_certainty()` | A `decline` with a blocking conflict becomes `refer` with `potential_decline = true` |
| Readings | `_readings()` | Evaluate alternative readings; a plausible declining reading makes a potential decline; an implausible one only flags |
| Other anchor | (inline) | Re-run from the other date; a different outcome is recorded as `anchor_note` (and `anchor_items`) without changing the outcome |

Decision (`_decide()`): the route team comes from every power-unit count the documents support (`power_unit_counts`); counts on both sides of the 10-unit line go to the fleet team, `route_stable = false`. External rules still `unknown` are excluded and listed as "To check at quote". The worst remaining outcome decides: decline gives "Decline recommended"; refer or unknown gives "Refer to <team>"; else "Ready for <team> review". ROUTE-02 is filled in from the decision. `DecisionRecord.outcome` is `decline`, `refer` or `ready`. Lane recommends; the underwriter decides (`underwriter_decisions`).

Rule confirmations (`rules_step._confirmations`): on a `follow_up` run, a rule that moves away from decline or potential decline creates a pending `rule_confirmations` row (audit `rule_change.pending`); it is voided if the rule becomes severe again. A senior underwriter confirms it (`confirm_cleared_decline`).

#### Analysis (`analyze/`)

The brief (`context.build_brief`) holds resolved fields, entities, conflicts, missing items, rule results and computed figures, each with a citation id: `F<n>` field, `E<n>` entity, `C<n>` conflict, `M<n>` missing item, `R:<RULE-ID>` rule, `X<n>` figure computed by code. Minimization (`Settings`): `analyze_withheld_attributes` (mailing_address, email, phone, garaging_address) withheld; `analyze_tokenized_attributes` (license_number, fein) and `analyze_tokenized_detail_keys` (claimant) tokenized; dates of birth become ages; `analyze_dropped_detail_keys` (document_id, fact_ids, quote, page, bbox) dropped. The token map never leaves the server.

Citation checks (`engine.check`): a finding needs at least one citation that matches `^(?:[FECMX]\d+|R:[A-Z]+-\d+)$` and exists in the brief; a finding that tells the underwriter to approve, bind, accept, decline or reject the risk is rejected; a hypothesis needs a basis; every summary sentence needs a valid citation; the summary must have 5 to 7 sentences. Problems are sent back once (`analyze_retries = 1`); what is still invalid is dropped and stored in `analyses.rejected`. `footnoted()` numbers each distinct citation once in reading order; `analyses.refs` maps each cited id (and what it cites in turn) to evidence for the lens.

Spend approval (`service.needs_approval`): a paid run needs a person unless already approved, the estimate (`estimate_usd`, about 3.5 characters per token in plus `analyze_expected_output_tokens = 12000` out) is at or under `analyze_auto_budget_usd = 0.0`, or the exact request is cached. Waiting sets status `awaiting_approval` with `estimate_usd`. `POST /analysis/approve` starts work kind `analysis` (`background.analysis`: analyze then rebuild actions without redraft). One approval covers one run.

#### Actions (`actions/`)

Request items (`build.build_items`) come from rules whose outcome is refer, unknown or decline and that have `request` text (rules sharing `request_key` become one item; `covers` suppresses missing items already asked), missing items by tier (required on by default, helpful off, bind grouped before binding; headings from `Settings.actions_missing_headings`), open conflicts worded as questions (`actions_conflict_questions`; some conflicts are asked by a missing item or by a rule's own request instead), and findings an underwriter accepted. Each item has a group (before_quote, before_binding, inform), text, details (lines with `detail_keys`), source kind and refs. A person's `included` choice survives rebuilds (stable item id).

Draft email (`email.draft_email`): the model (`claude-sonnet-5-5`) words only the opening, closing and one sentence per item from item headlines; code adds details (names, dates, VINs) under each item, checks every item is present and no internal terms leak (rule ids, "rules engine", "predicate"), and falls back to a template. The subject carries `[Ref LN-xxxxxxxx]`. A new draft version is written only when the included items' sha256 changes (or on explicit redraft). Nothing is sent: `POST /actions/mark-sent` records a `request_snapshots` row (who, when, every item) after a person sends it themselves.

Reply measurement (`actions/points.py`, used by `actions_step`): the baseline is the latest request marked sent, else the last draft. After a rebuild, each point (an entity and topic) is classed answered, answered not requested, still open, or new, counted per group and per item, and stored in the actions step summary (`points`).

### LLM gateway

`backend/src/lane/llm/gateway.py`, class `LlmGateway`. Constructing it without `ANTHROPIC_API_KEY` raises `LlmUnavailable`, which every caller turns into a degraded, visible state.

| Purpose | Caller | Model setting | Default model | Effort | Prompt file(s) |
|---|---|---|---|---|---|
| `classify_page` | `documents/classify.py` | `llm_classify_model` | `claude-haiku-5-5` | low | `classify_page.v2.md` (v1 kept) |
| `extract_<doc_type>` | `extract/engine_v2.py` (v1: `extract/engine.py`) | `llm_extract_model` / `llm_escalation_model` | `claude-haiku-5-5` / `claude-sonnet-5-5` | medium / medium | `v2_extract_document.v1.md` plus per type `v2_extract_<type>.v1.md` (mvr, drivers_license, loss_run, quote_form, vehicle_schedule, application_pdf, driver_exclusion, email_body); v1 prompts `extract_*.vN.md` |
| `analyze` | `analyze/engine.py` | `llm_analyze_model` | `claude-opus-5-5` | high (16000 max tokens) | `analyze_submission.v1.md` |
| `draft_email` | `actions/email.py` | `llm_draft_model` | `claude-sonnet-5-5` | low (4000 max tokens) | `draft_agent_email.v1.md` |

Request path (`run_many`):

1. Build content blocks: images resized to `llm_image_max_px = 1568` and sent as base64 PNG with a label; labelled text parts wrapped by `wrap_untrusted(label, text)` as `<document_content label="..." id="NONCE"> ... </document_content id="NONCE">`, the nonce being the first 12 hex characters of the text's sha256 so document text cannot close the block early. The prompt states that wrapped content is data.
2. Cache key (`cache_key`): sha256 of canonical JSON of model, effort, max_tokens, prompt sha256, wire schema, content (images replaced by their sha256), and an optional `cache_salt` (v2 extraction salts with document, catalog version and extractor version). This is the (model, prompt hash, input hash) key, extended with effort and schema.
3. Hits come from `llm_cache` and are recorded as `llm_calls` rows with `cache_hit = true` and zero cost.
4. Misses are sent in a thread pool of up to `llm_max_concurrency = 16` (database work stays on the caller's thread), unless `llm_cache_only` (replay), which records `NOT_CACHED`.
5. `AnthropicTransport` calls the Messages API with structured output (`output_config.format = json_schema`) and `effort`; if the grammar is too large, it asks for the JSON in the system prompt instead. Models in `FALLBACK_MODELS` use the beta `server-side-fallback-2026-07-01` when `llm_refusal_fallbacks`; `served_by` records a different serving model. SDK retries `llm_max_retries = 3`, timeout `llm_timeout_s = 300`.
6. Validation: refusal gives status `refused`; no JSON or schema errors give `invalid`; transport errors give `error`; else `ok`. Only `ok` outputs enter `llm_cache` (`source_call_id` links the call).
7. Cost (`_cost`): `(input * price_in + cache_read * price_in * 0.1 + cache_write * price_in * 1.25 + output * price_out) / 1e6`, prices per million tokens from `llm_prices` (haiku 0.10/0.50, sonnet 2.00/10.00, opus 4.00/20.00). One structured log line `llm.call` per call (purpose, model, cache_hit, status, cost_usd, latency_ms) feeds the spend alarm.

Prompt versioning (`llm/prompts.py`): files `backend/prompts/<name>.v<N>.md`; `load_prompt(name)` takes the highest N; every call records `prompt_name`, `prompt_version` and `prompt_sha256`.

Spend approval points: analysis (above) and arithmetic escalations (`escalation_approvals`). Auto escalation for unverified decision fields needs no approval.

### Data model

`backend/src/lane/db/models.py`: 35 tables. Ids are 32-character hex (uuid4 or stable sha256 prefixes). Every table except `audit_events`, `steps` and `worker_heartbeats` has `created_at`. Migrations: 28 Alembic revisions in `backend/migrations/versions/` (`0001_baseline.py` to `0028_rule_evidence.py`).

#### Intake and files

| Table | Key columns | Relations |
|---|---|---|
| `blobs` | `sha256` PK, size, kind (sniffed), mime, storage_key | |
| `email_messages` | id, `idempotency_key` unique, message_id, source (upload, cli, smtp), from_addr, to_addrs, subject, sent_at, received_at, headers | `raw_sha256` -> blobs |
| `submissions` | id, status (received, processing, ready, failed), relation (same_files, follow_up, possible_follow_up, kept_separate), relation_how, relation_reason, relation_at, relation_by | `email_id` -> email_messages (unique); `related_to_id` -> submissions |
| `attachments` | id, ordinal, role (email_body, attachment, archive_member), filename, declared_mime, archive_path, depth, status (accepted, duplicate, expanded, rejected), reason, read_error | `submission_id` -> submissions; `parent_id`, `duplicate_of_id` -> attachments; `blob_sha256` -> blobs |

#### Pages and documents

| Table | Key columns | Relations |
|---|---|---|
| `pages` | id, page_no, kind (text, scan, photo, sheet, email_body), triage_version, width, height, rotation, has_text_layer, text_chars, image_coverage, sharpness, contrast, colorfulness, skew_deg, quality (clean, degraded, poor), warnings, sheet_name, sheet_hidden, prep, text_source, ocr_confidence | submissions, attachments, documents; `render_sha256`, `thumb_sha256`, `original_sha256`, `layout_sha256` -> blobs |
| `page_cache` | key PK, file_sha256, triage_version, page_no, page (JSON), hidden | |
| `ocr_cache` | key PK, engine, words | |
| `documents` | id (stable), ordinal, doc_type, page_from, page_to, title, subject_id, subject_name, period, document_date, confidence (high, medium, low), confidence_reasons, filename_hint, filename_mismatch, needs_review, review_reasons, classifier_version | submissions, attachments; `superseded_by_id` -> documents |
| `page_classifications` | `page_id` PK, classifier_version, model_output, signals, error | pages; `llm_call_id` -> llm_calls |
| `document_labels` | `document_id` PK, doc_type, was, reason, actor | submissions |
| `layouts` | id, name, doc_type, status (active, disabled), definition, source_document_id, source_submission_id, source_page_no, confirmed_by, confirmed_at, audits, agreements, disabled_reason, disabled_at | (no FKs) |

#### Extraction

| Table | Key columns | Relations |
|---|---|---|
| `document_extractions` | `document_id` PK, doc_type, status, method (sheet, text, vision, none), model, extractor_version, data, checks, escalation_reasons, escalation_cost_usd, auto_escalated, coverage, cost_usd, latency_ms, error | documents, submissions, llm_calls |
| `facts` | id, path (for example `claims[1].total_paid`), value_raw, value_norm, page_no, bbox, cell_ref, quote, method, grounding, confidence, confidence_reasons, tier, verified, verification, second_read, reads, model | submissions, documents, pages |
| `escalation_approvals` | `document_id` PK, approved_by, estimated_cost_usd | documents |

#### Resolution

| Table | Key columns | Relations |
|---|---|---|
| `resolved_entities` | id (stable), kind, key, label, found_in, details | submissions |
| `resolved_fields` | id (stable), attribute, label, kind, state (known, conflict, invalid, missing, unverified), resolution, value_display, value, candidates, notes, invalid | submissions, resolved_entities |
| `conflicts` | id (one per field), type (identity, value, cardinality, reference), title, hint, candidates, blocking_rules, notes, resolution | submissions, resolved_fields, resolved_entities |
| `missing_info` | id (one per code), code, title, detail, category, document_ids | submissions, resolved_entities |
| `field_resolutions` | `field_id` PK, display, value, reason, actor, kind (choice or override), before | submissions |

#### Rules and decisions

| Table | Key columns | Relations |
|---|---|---|
| `rule_results` | id (submission + rule), ordinal, rule_id, title, section, quote, decline_capable, outcome, reason, items, request, inputs (field ids), potential_decline, blocking_conflicts, sensitivity, anchor_note, readings, assumptions, waiting_on, evidence (fact ids), absent, anchor_items, ruleset_version | submissions, runs |
| `decisions` | `submission_id` PK, outcome (decline, refer, ready), headline, team, team_label, route_reason, route_stable, potential_decline, reasons, anchor, anchor_date, ruleset_version, ruleset_sha256, counts | submissions, runs |
| `rule_confirmations` | id, rule_id, title, before, after, status (pending, confirmed, void), decided_by, decided_at | submissions, runs |
| `underwriter_decisions` | id, decision (decline, refer_back, quote), reason, conditions, recommendation (Lane's, at the time), decided_by, decided_at | submissions |

#### Analysis and actions

| Table | Key columns | Relations |
|---|---|---|
| `analyses` | `submission_id` PK, status (ok, degraded, awaiting_approval, running), model, prompt_version, prompt_sha256, summary, footnotes, refs, rejected, security_notes, attempts, cost_usd, error, estimate_usd, approved_by, analyzed_at, approved_at | submissions, runs |
| `findings` | id (submission + title), ordinal, title, body, kind (observation, hypothesis, recommendation), severity, confidence, basis, citations, review_state (open, accepted, dismissed), review_reason, reviewed_by, reviewed_at | submissions, runs |
| `agent_request_items` | id (submission + key), ordinal, group, text, details, detail_keys, source_kind (rule, missing, conflict, finding), source_refs, included | submissions |
| `email_drafts` | id, version, to, subject, body, method (model, template), model, items_sha256, problems | submissions |
| `request_snapshots` | id, draft_version, subject, items, sent_by, sent_at | submissions |

#### Runs, models, jobs, audit

| Table | Key columns | Relations |
|---|---|---|
| `runs` | id, trigger, status (running, succeeded, degraded, failed), started_at, finished_at, versions, input_hash, output_hash, replay_of | submissions, email_messages |
| `steps` | id, name, version, status (running, succeeded, degraded, failed, skipped), attempt, duration_ms, summary, error | runs |
| `llm_calls` | id, purpose, subject_id, model, prompt_name, prompt_version, prompt_sha256, cache_key, cache_hit, status, stop_reason, input/output/cache tokens, cost_usd, latency_ms, request_id, served_by, error | runs |
| `llm_cache` | `cache_key` PK, model, output | `source_call_id` -> llm_calls |
| `jobs` | id, kind (process, replay, analysis), trigger, status (queued, running, done, failed), attempts, lease_owner, lease_until, run_id, replay_run_id, error, started_at, finished_at | submissions |
| `worker_heartbeats` | `owner` PK (host:pid), started_at, seen_at, job_id, jobs_done | |
| `audit_events` | `seq` PK, at, actor, action, subject_type, subject_id, payload, `prev_hash` unique, `hash` unique | (no FKs) |

### Frontend

`frontend/` is Next.js 16.4.3 with React 19.3.0, TypeScript strict, Tailwind 4. All data comes from the API through a generated typed client.

#### App routes (`frontend/src/app/`)

| Route | File | Renders |
|---|---|---|
| `/` | `app/page.tsx` | `Inbox` (`components/inbox/inbox.tsx`): work queue views, age against `TURNAROUND_HOURS` (`lib/queue.ts`), upload of `.eml` |
| `/evals` | `app/evals/page.tsx` | `Evals` (`components/evals/evals.tsx`) |
| `/rules-library` | `app/rules-library/page.tsx` | `RulesLibrary` and `KnownLayouts` (`components/rules-library/*`) |
| `/ops` | `app/ops/page.tsx` | `Ops` and live queue (`components/ops/ops.tsx`, `live.tsx`) |
| `/how-lane-works` | `app/how-lane-works/page.tsx` | `HowLaneWorks` (`components/how/how-lane-works.tsx`) |
| `/auth/callback` | `app/auth/callback/page.tsx` | `SignInReturn` (finishes Cognito PKCE) |
| `/submissions/[id]` | `app/submissions/[id]/layout.tsx` + `page.tsx` | `SubmissionShell` with `EvidenceProvider`; Brief (`components/submission/overview.tsx`), or `LiveIntake` while the run is live or there is no route yet |
| `/submissions/[id]/operations` | `operations/page.tsx` | Applicant and operations |
| `/submissions/[id]/coverage` | `coverage/page.tsx` | Coverage and losses |
| `/submissions/[id]/documents` | `documents/page.tsx` | Documents (label, confirm type) |
| `/submissions/[id]/audit` | `audit/page.tsx` | Audit timeline and runs (`audit.tsx`, `audit-runs.tsx`, `lib/timeline.ts`) |
| `/submissions/[id]/drivers` | `drivers/page.tsx` | Drivers |
| `/submissions/[id]/vehicles` | `vehicles/page.tsx` | Vehicles |
| `/submissions/[id]/conflicts` | `conflicts/page.tsx` | Conflicts (resolve, reopen, correct a value) |
| `/submissions/[id]/missing` | `missing/page.tsx` | Missing items |
| `/submissions/[id]/rules` | `rules/page.tsx` | Rules |
| `/submissions/[id]/findings` | `findings/page.tsx` | AI findings (accept, dismiss, approve spend) |
| `/submissions/[id]/actions` | `actions/page.tsx` | Next actions (request items, draft, mark sent) |

Submission tabs are grouped in `components/submission/shell.tsx` `VIEWS`: Overview (Brief, Applicant and operations, Coverage and losses), Documents (Documents, Audit), Drivers and vehicles, Conflicts and missing, Rules, Findings and actions.

#### Shell

| Component | File | Does |
|---|---|---|
| Root layout | `app/layout.tsx` | Fonts (Inter, JetBrains Mono, Newsreader, Overpass), theme and density bootstrap scripts, `Providers`, `AppShell` |
| Providers | `components/providers.tsx` | `QueryClient` (staleTime 60 s, no refetch on focus, retry 1, queries enabled only with a token except `session` and `people`), `SessionProvider`, `HintProvider`, `SignInGate` |
| App shell | `components/shell/app-shell.tsx` | `NavRail`, `Commands`, `ApiStatus`, `RoleSwitcher`, `ThemeToggle`, `LaneMark` |
| Nav rail | `components/shell/nav-rail.tsx` | Inbox, Evals, Rules library, Operations, How Lane works |
| Command palette | `components/ui/command-palette.tsx`, `components/ui/commands.tsx` | `cmdk` "Go to" palette and keyboard shortcuts (`SHORTCUTS`) |
| Role switcher | `components/shell/role-switcher.tsx` | Shows the person and role; "Switch" signs out |
| Sign-in gate | `components/shell/sign-in.tsx` | Covers the app (`inert`) until signed in; lists demo people (`GET /auth/people`) or starts Cognito |
| API status | `components/shell/api-status.tsx` | Polls `GET /health`; shows database and "AI not configured" states |

#### Typed client, queries and session

Generation (`Makefile` target `api-types`): `scripts/export_openapi.py` writes `create_app().openapi()` to `frontend/src/lib/api/openapi.json`; `npx openapi-typescript` writes `frontend/src/lib/api/schema.d.ts`. `frontend/src/lib/api/index.ts` creates `api = createClient<paths>({ baseUrl: NEXT_PUBLIC_LANE_API_URL or http://localhost:8000, cache: "no-store" })` with an `auth` middleware that adds `Authorization: Bearer <token>`, and answers non-open requests locally with 401 when no one is signed in. `authedUrl(path)` adds `access_token` for images and event streams.

TanStack Query (`frontend/src/lib/queries.ts`): query keys start `["submission", id, part]` so one invalidation refreshes a whole submission; `fetchPart` maps parts (overview, documents, pages, extractions, entities, conflicts, missing, rules, analysis, actions, runs, audit) to endpoints; background prefetch of every tab is limited to 4 requests at once (`PREFETCH_MAX`). Polling: overview every 2 s while an amendment is in progress; analysis every 3 s while running; runs every 2 s while a run is running; ops queue every 2 s; ops health every 10 s. Mutations (resolve, reopen, override, clear override, decision, senior review, approve analysis, replay, follow-up decision, confirm document, reveal) invalidate the submission keys.

Session (`frontend/src/lib/session.tsx`): token kept in `localStorage` key `lane.session` and restored before the first request; `GET /auth/me` gives role, permissions and authority (`can()`, `mayDecide()`). Demo sign-in posts `/auth/demo-login`; Cognito sign-in posts `/auth/cognito`; both clear the cache and reload. Sign-out clears the token and, with Cognito, goes to Cognito's `/logout`.

Cognito PKCE (`frontend/src/lib/cognito.ts`): enabled when `NEXT_PUBLIC_LANE_COGNITO_DOMAIN` and `NEXT_PUBLIC_LANE_COGNITO_CLIENT_ID` are set. `startSignIn()` stores a verifier (48 random bytes), state and return path in `sessionStorage` key `lane.signin`, then redirects to `<domain>/oauth2/authorize` with `code_challenge_method=S256`, scope `openid email profile`, redirect `/auth/callback`. `finishSignIn()` checks state and posts the code and verifier to `<domain>/oauth2/token`, returning the ID token.

#### Evidence lens and footnotes

`components/evidence/lens.tsx` `EvidenceProvider` holds one `EvidenceTarget`: `field` (a resolved field, fetched by `GET /fields/{id}`), `facts` (fact ids via `GET /facts`, with note, inputs and a panel link), `entity`, `page` (with boxes), or `refs` (a run of footnotes). `LensPanel` (`lens-panel.tsx`) renders `PageView` (page image from `/pages/{id}/image` with highlight boxes from `/pages/{id}/layout`), `SheetView` (cell), `TextView` (email body quote), `SourceCrop`, `SourceSwitcher`, `ConflictStrip`, `CorrectValue` (override). Keys: E opens the focused value, Esc closes, Left and Right move between sources. `lens-items.ts` turns field candidates and facts into items; `targets.ts` `refTarget()` maps an analysis ref (field, entity, figure, rule, conflict, missing) to a target. License photos are blurred server-side until revealed (`lib/photo-reveal.ts`).

Footnotes (`frontend/src/lib/footnotes.ts`): `clusters()` groups consecutive footnote numbers, `clusterLabel()` labels runs ("3-7" style with an en dash), `clusterTarget()` opens one footnote or a `refs` run, `topicOf()` and `paragraphs()` break the summary into paragraphs by what each sentence cites.

#### Live intake

`components/submission/live-intake/use-intake-stream.ts` opens `new EventSource(authedUrl("/api/v1/submissions/{id}/events"))`, listens for every type in `EVENTS` (snapshot, run.started, step.started, step.finished, page.ready, document.found, document.extracted, conflict.found, missing.found, rule.evaluated, decision.made, analysis.done, actions.ready, run.finished), reduces them with `reducer.ts`, and closes on `end`. `tile.tsx` and `marks.tsx` draw the pages and documents as they arrive.

#### Libraries (`frontend/package.json`)

| Dependency | Version | Use |
|---|---|---|
| next | 16.4.3 | App router |
| react, react-dom | 19.3.0 | UI |
| @tanstack/react-query | ^5.105.0 | Server state |
| openapi-fetch | ^0.17.0 | Typed client |
| @radix-ui/react-dialog | ^1.3.1 | Dialogs |
| @radix-ui/react-tooltip | 1.4.1 | Tooltips |
| cmdk | ^1.1.1 | Command palette |
| motion | ^14.9.0 | Animation |
| sonner | ^2.0.8 | Toasts |
| lucide-react | ^1.65.0 | Icons |
| clsx | ^2.1.1 | Class names |
| d3-geo ^3.1.1, topojson-client ^3.1.0, us-atlas ^3.0.1 | | Footprint map (`footprint-map.tsx`) |

Dev: openapi-typescript ^7.13.0, typescript ^5, eslint ^9 with eslint-config-next 16.4.3, prettier ^3.9.10, tailwindcss ^4 with @tailwindcss/turbopack, vitest ^5.0.5, @testing-library/react ^16.3.3, jsdom ^30.1.3, @playwright/test ^1.64.1, @axe-core/playwright ^4.14.0.

### Security and privacy

#### Masking layers

Personal data here is a driver's identifiers only: `pii_attributes = {license_number, date_of_birth, home_address}`. Business data (company addresses, phones, emails, FEIN) is not masked.

| Layer | Code | What it does |
|---|---|---|
| 1 Field and fact values | `api/privacy.py` `Masker.value` | License numbers keep the first and last 3 characters; dates of birth hide the year; home addresses hidden |
| 2 Free text | `privacy.pii_texts`, `Masker.text` | Every identifier the database holds, in every printed form, replaced in quotes, row snippets, sensitivity rows, page words and cells |
| 3 Last pass | `main.scrub_personal_data` | Over every JSON response on submission paths; SSNs masked for every role always; withheld with 503 if identifiers cannot be read |
| Photos | `api/pages.py` `blurred_png` | License photos blurred (Gaussian radius max side / 30) unless an underwriter reveals; audit `pii.photo_revealed` |
| Reveal | `POST /fields/{id}/reveal` | One field at a time, reveal_pii only, audited |
| Audit text | `audit/describe.py` | Descriptions pass values through `mask()` |
| Analyst | `analyze/context.py` | Withheld, tokenized, ages instead of dates of birth |
| Agent email | `actions/email.py` | Details never go to the model |
| Logs | `log.py` `mask_pii` | Any log key equal to or ending in `_<pii attribute>` becomes `[masked]` |
| DOB conflicts | `domain/ages.py` | Only the differing parts are shown, year hidden |

#### Audit hash chain (`audit/chain.py`)

Each event's `hash` is sha256 of canonical JSON `{prev, at (UTC microseconds), actor, action, subject, payload}`; `prev_hash` is the previous event's hash (genesis `0` x 64). Both columns are unique. On Postgres, `record()` first takes `pg_advisory_xact_lock(0x4C414E45)` (held to the end of the transaction) so concurrent writers (API and workers) append in turn. `verify()` walks the chain in `seq` order and reports the first broken link or hash; `GET /submissions/{id}/audit` returns the result. Deleting a submission keeps its audit events and adds `submission.deleted`.

#### Other controls

- Document text is data: wrapped with a content nonce before any model sees it; injection attempts and hidden text are recorded and shown; rules never read model output directly; findings that tell the underwriter to approve or decline are rejected.
- Model failures are visible states: classify falls back to keywords, extract marks documents failed, analyze keeps the last good result marked degraded, draft falls back to a template.
- Nothing is sent: drafts only; mark sent records a person's action.
- Inbound mail on AWS is held unless authenticated and allowlisted.

### End-to-end flows

#### (a) A new email submission to a finished Brief

1. The agent's email reaches Mailpit (SMTP 1025) or SES, which stores it in S3 under `ses_inbox_prefix`; or a person uploads the `.eml` on the Inbox (`POST /api/v1/intake/eml`).
2. The watcher (`mailpit.watch` or `ses` over the same loop) calls `poll_once()`; SES messages first pass `held_reason()`.
3. `ingest_email()` checks size and sniff, parses, de-duplicates by Message-ID, stores blobs, writes `email_messages`, `submissions`, `attachments`, audit `email.received` and `intake.completed`.
4. `link.place()` finds no related open submission, so `process_id` is the new submission, trigger `intake`.
5. `background.start()` runs `process()` on a thread (inline) or `queue.enqueue()` writes a `jobs` row (queue); a worker claims it with `SKIP LOCKED` and starts renewing its lease.
6. `process_submission()` writes `runs`, sets status processing, audit `run.started`, emits `run.started`.
7. Triage renders pages, prepares scans, runs OCR (with caches), records hidden text, scans for injection; emits `page.ready`.
8. Classify types pages (tier 1, known layouts, then `classify_page` on Haiku), splits documents, marks supersession; emits `document.found`.
9. Extract reads each non-superseded document (v2: Haiku or Sonnet first, code's second read, validators, recall, arithmetic, auto escalation); writes `document_extractions` and `facts`; emits `document.extracted`.
10. Resolve builds the board from facts that pass the gate; writes entities, fields, conflicts, missing; emits `conflict.found`, `missing.found`.
11. Rules evaluates 29 rules with the safeguards and decides the route; writes `rule_results`, `decisions`; emits `rule.evaluated`, `decision.made`.
12. Analyze builds the minimized brief; with the auto budget at 0 and nothing cached, it records `awaiting_approval` and the step is `skipped`; emits `analysis.done`.
13. Actions builds request items and drafts the email (Sonnet or template); emits `actions.ready`.
14. The run is fingerprinted (`input_hash`, `versions`, `output_hash`), audit `run.finished`, status ready; the job is closed `done`.
15. In queue mode every event went worker BUS -> `pg_notify` -> API listener -> API BUS. The browser's `EventSource` on `/submissions/{id}/events` showed each stage in `LiveIntake`; on `end` the Brief (`GET /submissions/{id}`) renders the route, blockers, key numbers and counts.
16. An underwriter approves the analysis spend (`POST /analysis/approve`); work kind `analysis` runs Opus, validates citations, stores the summary with footnotes, then rebuilds request items; the Brief polls and shows the cited summary.

#### (b) A value's journey from a page to a rule outcome

1. Triage stores the page render and its words (text layer or OCR) as a layout blob, with quality band.
2. Classify assigns the page to a document of a type; the field catalog lists the fields of that type with tier and labels.
3. Extract asks the model (purpose `extract_<type>`); it returns the value as printed, a verbatim quote and the page (or a cell reference for a sheet).
4. `engine_v2.tidy()` corrects code-level issues; `normalize_value()` parses the value by type.
5. `verify.locate()` reads the value again at its anchor (label, row, column, or cell); on a scan `ocr_check.gate()` applies OCR confidence and `reread()` tries an enlarged crop if needed.
6. `compare()` matches by type; the fact gets `verified`, `verification`, `second_read`, `grounding`, `bbox` or `cell_ref`, `quote`.
7. Validators, recall and arithmetic checks run; failures escalate or cap confidence; `_confidence()` sets the band and reasons; the fact is saved.
8. `resolve/service.decides()` admits the fact only if it passes the gate; `Builder` adds it as a candidate (with document and fact ids) to an entity field.
9. `resolve_field()` merges agreeing candidates or marks a conflict; `field_resolutions` apply any person's choice; the field is saved with a stable id.
10. A predicate reads the field through `SubmissionView`; the read is recorded in the rule's `inputs`, and detail facts in `evidence`.
11. Safeguards adjust the outcome (unverified, sensitivity over conflict candidates, decline certainty, readings, anchor note); the result is saved in `rule_results`.
12. The UI's lens opens the field (`GET /fields/{id}`), its candidates and facts, and draws the box on the page image.

#### (c) An agent reply linking and re-running

1. A person marks the request sent (`POST /actions/mark-sent`): `request_snapshots` stores every item; the draft subject carries `[Ref LN-xxxxxxxx]`.
2. The agent replies; the reply arrives by any way in and is ingested as a new submission.
3. `link.place()`: if every file is already held, `same_files` and stop. Else the reference token, then the thread headers, then identity numbers from the same domain match: `_attach()` moves the new email's attachments into the target (duplicates marked), sets relation `follow_up`, audit `submission.amended`, and returns `process_id = target`, trigger `follow_up`. A weaker match sets `possible_follow_up` and waits for a person (`/attach` or `/keep-separate`).
4. The target runs the full pipeline again with trigger `follow_up`; the run's `email_id` is the newest attached reply. Pages and model answers already read are reused.
5. The rules step compares outcomes with the run before; any rule that moved away from decline or potential decline creates a pending `rule_confirmations` row.
6. The actions step takes the baseline (the request marked sent), rebuilds items, and `points.diff()` records answered, still open, and new points in the step summary.
7. The Brief shows the changes and pending confirmations; a senior underwriter confirms each cleared decline (`POST /rule-confirmations/{id}/confirm`).

#### (d) A person resolving a conflict and the rerun

1. On the Conflicts tab, a reviewer picks a candidate and gives a reason; the UI posts `POST /conflicts/{id}/resolve`.
2. `review/service.resolve_conflict()` writes `field_resolutions` (stable field id) and the audit event.
3. `rerun()` snapshots rule outcomes, then calls `process_submission()` synchronously with only the `resolve` and `rules` steps (trigger `resolution`): no model calls.
4. Resolution applies the choice; the conflict carries the resolution and rules read the chosen value; sensitivity no longer treats it as conflicted.
5. `build_actions(..., redraft=False)` rebuilds request items; the draft is not regenerated, so it may be marked out of date.
6. The response (`RerunOut`) lists rule changes and the headline before and after; the UI invalidates the submission queries.
7. Reopening (`DELETE /conflicts/{id}/resolution`, trigger `reopen`), overriding a value (`POST /fields/{id}/override`) and clearing it follow the same path.

#### (e) Sign-in

Demo:
1. The `SignInGate` covers the app; `GET /auth/config` says demo is on; `GET /auth/people` lists the people.
2. Choosing a person posts `/auth/demo-login`; the API issues an HMAC token with role, name and expiry.
3. The browser stores it in `localStorage` (`lane.session`), clears the query cache and reloads; `setToken()` adds it to every request.
4. `GET /auth/me` returns role, permissions and decision authority; screens show or hide actions with `can()` and `mayDecide()`.

Cognito:
1. With the Cognito domain and client id set, "Sign in" calls `startSignIn()`: PKCE verifier and state into `sessionStorage`, redirect to Cognito's `/oauth2/authorize`.
2. Cognito redirects to `/auth/callback?code&state`; `SignInReturn` calls `finishSignIn()`, which checks state and exchanges the code and verifier at `/oauth2/token` for an ID token.
3. The browser posts the ID token to `POST /auth/cognito`; the API verifies it against the pool's JWKS, maps `cognito:groups` to a role by `ROLE_ORDER`, and issues Lane's own HMAC token.
4. From here the flow is the demo flow's steps 3 and 4. With `LANE_REQUIRE_SIGN_IN` on, the API refuses every other `/api/v1/` path without a valid token; sign-out also ends the Cognito session through `/logout`.

## Local deployment: runtime and tooling

This section describes Lane as it runs on one Mac: every process, port, store, tool and flow, read from the repository at `lane-aws/`. The application's internal packages and pipeline are covered elsewhere; components are named here only where a process runs them.

### At a glance

Lane runs locally as six processes on one Mac: the Next.js web app, the FastAPI API, the queue worker, PostgreSQL, MinIO and Mailpit; the Anthropic API is the one outside service. Each process, its port and what it talks to are listed below, and the twelve connections are numbered under Numbered connections.

In inline mode (`LANE_RUN_MODE` unset) the worker exits at once and the API process runs the pipeline and the Mailpit watcher itself; flows 4, 5 and 7 then start from the API instead of the worker.

### Processes and services

| Process | Command | Port, bind | Started by | Talks to |
|---|---|---|---|---|
| Web (dev) | `next dev` (`npm run dev` in `frontend/`) | 3000 (Next.js default) | `make dev`, `make web` | Nothing server side for data; serves pages and JS to the browser |
| Web (production build) | `next build` then `next start` | 3000 | `make demo` (`make build` builds only) | Same as above |
| API | `uv run uvicorn lane.api.main:app --reload --port 8000` | 8000, 127.0.0.1 (uvicorn default host) | `make dev`, `make api` (no `--reload` in `make demo`) | Postgres or SQLite; blob store (local folder or MinIO); Anthropic (inline mode); Mailpit REST (inline mode) |
| Worker | `uv run python -m lane.worker` | none | `make dev`, `make demo`, `make worker` | Postgres (job queue, heartbeats, NOTIFY); MinIO; Mailpit REST; Anthropic |
| Postgres | external server, not started by the Makefile | 5433 (from every default URL) | the developer (README: "Needs ... Postgres") | API, worker, CLIs, tests |
| MinIO | `minio server var/minio --address 127.0.0.1:9000 --console-address 127.0.0.1:9001` | 9000 S3 API, 9001 console | `make minio` | API and worker over S3 HTTP |
| Mailpit | `mailpit --smtp 127.0.0.1:1025 --listen 127.0.0.1:8025` | 1025 SMTP, 8025 web UI and REST API | `make mailpit` | SMTP senders in; watchers poll its REST API |
| Standalone Mailpit watcher | `uv run python -m lane.intake.mailpit` (optional `--once`) | none | `make mailpit-watch` | Mailpit REST, Postgres, blob store, Anthropic (runs the pipeline in its own process) |
| One-shot CLIs | `python -m lane.intake.cli`, `lane.pipeline.cli`, `lane.intake.reset_cli`, `lane.evals.*`, `lane.documents.template_tool` | none | the Makefile targets below | Postgres, blob store, Anthropic (cache first) |

#### Web app (`frontend/`)

- Next.js `16.4.3`, React `19.3.0`. `frontend/next.config.ts` sets `devIndicators: false`, `cacheComponents: true`, `partialPrefetching: true` and a Turbopack rule that loads `*.css` through `@tailwindcss/turbopack`. It defines no rewrites, proxies or headers, and there is no `middleware.ts`, `proxy.ts` or `route.ts` in `frontend/src`.
- The browser calls the API directly. `frontend/src/lib/api/index.ts` builds an `openapi-fetch` client with `baseUrl: API_BASE`, where `API_BASE = process.env.NEXT_PUBLIC_LANE_API_URL ?? "http://localhost:8000"`, `cache: "no-store"`. Requests carry `Authorization: Bearer <token>` once someone signs in; without a token, only `/api/v1/auth/*` and `/api/v1/health` leave the browser and everything else is answered locally with a 401.
- Live progress uses `EventSource` on `/api/v1/submissions/{id}/events` (`frontend/src/components/submission/live-intake/use-intake-stream.ts`), with the token in the URL (`authedUrl`).
- Other public env vars read by the web app: `NEXT_PUBLIC_LANE_COGNITO_DOMAIN`, `NEXT_PUBLIC_LANE_COGNITO_CLIENT_ID` (`frontend/src/lib/cognito.ts`; empty means the demo people sign-in), `NEXT_PUBLIC_LANE_TURNAROUND_HOURS` (`frontend/src/lib/queue.ts`, default 4).

#### API (`backend/src/lane/api/main.py`)

- FastAPI app built by `create_app()`. Routers under `/api/v1` (health, intake at `/api/v1/intake/eml`, auth at `/api/v1/auth/*`, submissions, pages, documents, events and the rest).
- Middleware, outermost first: GZip (minimum 1024 bytes; event streams are not compressed), `require_sign_in` (only when `LANE_REQUIRE_SIGN_IN` is true; off locally), `scrub_personal_data` (masks driver identifiers in JSON responses for submission data paths), CORS (`LANE_CORS_ORIGINS`, default `["http://localhost:3000"]`).
- Startup (`Lifespan`): `check_secrets()` refuses the default session secret when `LANE_ENV` is not `dev`. Then:
  - queue mode: starts a thread that `LISTEN`s on the Postgres channel `lane_events` (if the database is Postgres) and republishes events into the in-process bus. It does not watch Mailpit and does not run work.
  - inline mode: recovers runs a restart interrupted, warms the OCR models on a thread, and, if `LANE_MAILPIT_URL` is set, starts the Mailpit watcher on a daemon thread that processes new submissions on threads of the API process.
- `GET /api/v1/health` reports `database` (`SELECT 1`) and `llm` (`configured` or `missing_key`).

#### Worker (`backend/src/lane/worker.py`)

Two modes, chosen by `LANE_RUN_MODE`:

| Mode | What the worker does |
|---|---|
| `inline` (default in `config.py`) | Logs `worker.idle` and exits with status 0. The API runs work on its own threads (FastAPI `BackgroundTasks`, `lane/pipeline/background.py`). |
| `queue` | Runs until SIGTERM or SIGINT: |

In queue mode:
1. If the database is Postgres, installs a `Publisher` so every bus event is also sent with `SELECT pg_notify('lane_events', ...)` (payload capped near 7,900 bytes; larger events lose their details) (`lane/events/notify.py`).
2. Warms the OCR models on a thread (`LANE_TRIAGE_OCR`, default true).
3. If `LANE_MAILPIT_URL` is set, starts the Mailpit watcher thread; new submissions are enqueued as `process` jobs. If `LANE_SES_INBOX_PREFIX` is set, starts the SES S3 inbox watcher (an AWS feature; not used locally).
4. Writes a heartbeat row (`WorkerHeartbeat`, every 5 s) with its owner `<hostname>:<pid>` and current job.
5. Loops: `queue.claim()` with `SELECT ... FOR UPDATE SKIP LOCKED` (`lane/pipeline/queue.py`), lease `LANE_WORKER_LEASE_S` (30 s) renewed every `LANE_WORKER_RENEW_S` (10 s), poll every `LANE_WORKER_POLL_S` (1 s), at most `LANE_WORKER_MAX_ATTEMPTS` (3). Job kinds: `process`, `replay`, `analysis`. A reclaimed job's cut-off run is marked interrupted first.
6. On a clean stop deletes its heartbeat row. Several workers can run at once (`make worker` in more terminals).

#### Postgres

- Not started by any Makefile target. All defaults point at `localhost:5433`: `.env.example` (`postgresql+psycopg://localhost:5433/lane`), `make check-pg` and `make test-pg` (`.../lane_test`), `make eval-db` (`.../lane_eval`). The checked-out `.env` in this repository names `.../lane_aws`.
- Migrations: Alembic, `backend/alembic.ini`, `backend/migrations/env.py` (URL from `get_settings().resolved_database_url`), 28 revisions in `backend/migrations/versions/` (`0001_baseline.py` to `0028_rule_evidence.py`). Every target that touches the database depends on `make migrate`.
- Server engine: `pool_pre_ping=True`, `pool_size=20` (`lane/db/base.py`).
- SQLite fallback: with `LANE_DATABASE_URL` empty, the URL is `sqlite:///<LANE_DATA_DIR>/lane.db` (default `var/lane.db`). On SQLite, `notify.available()` is false, so worker events do not reach the API's live view; queue mode is meant for Postgres.
- What lives in it (relevant to runtime): the `jobs` table (queue), worker heartbeats, submissions and evidence, the model cache (`llm_cache`, `llm_calls`), the OCR cache (`ocr_cache`), layouts.

#### File storage (blobs)

`lane/storage/blobs.py`, chosen by `LANE_BLOB_BACKEND`:

| Backend | Where | Keys |
|---|---|---|
| `local` (default) | `<LANE_DATA_DIR>/blobs` (default `var/blobs`) | content sha256 |
| `s3` | bucket `LANE_S3_BUCKET` under prefix `LANE_S3_PREFIX` (default `blobs/`), at `LANE_S3_ENDPOINT_URL` (MinIO: `http://127.0.0.1:9000`), region `LANE_S3_REGION` (default `us-east-1`) | the same sha256 keys |

Emails, attachments and page images go here. `make minio` keeps MinIO's data in `var/minio` and takes its root user and password from the `LANE_S3_ACCESS_KEY_ID` and `LANE_S3_SECRET_ACCESS_KEY` lines of `.env`. The checked-out `.env` names bucket `lane-aws`. `make blobs-to-s3` copies a local blob folder into the bucket and creates the bucket if missing.

#### Mailpit and the watchers

- Mailpit receives SMTP on `127.0.0.1:1025` and serves its UI and REST API on `127.0.0.1:8025`.
- The watcher (`lane/intake/mailpit.py`) polls every `LANE_MAILPIT_POLL_S` (5 s): `GET /api/v1/messages?limit=100`, keeps unread ones, `GET /api/v1/message/{id}/raw`, ingests each (`source="smtp"`, idempotent by Message-ID), starts processing the new ones, then `PUT /api/v1/messages` to mark them read. Rejected emails are marked read too. Mailpit being down is logged and retried.
- Where it runs depends on settings:
  - queue mode with `LANE_MAILPIT_URL`: inside the worker (enqueues jobs).
  - inline mode with `LANE_MAILPIT_URL`: inside the API (runs on API threads).
  - `make mailpit-watch`: a separate process that runs the full pipeline itself (`process_submission`), with `LANE_MAILPIT_URL` defaulting to `http://127.0.0.1:8025`.
- `deploy/docker-compose.yml` warns that two watchers race for every email. Running `make mailpit-watch` alongside a queue-mode worker that has `LANE_MAILPIT_URL` set gives two watchers.

#### Anthropic API

- The only model provider. `AnthropicTransport` in `lane/llm/gateway.py` uses the `anthropic` SDK (`anthropic.Anthropic`, timeout `LANE_LLM_TIMEOUT_S` 300 s, `LANE_LLM_MAX_RETRIES` 3) over HTTPS.
- Called from whichever process runs the pipeline: the worker (queue), the API (inline), a CLI, or the standalone watcher.
- Answers are cached in the database by request and content hash; `LANE_LLM_CACHE_ONLY=true` never calls the API (a miss is recorded). The expert review (analyze step) is skipped as `awaiting_approval` unless cached or within `LANE_ANALYZE_AUTO_BUDGET_USD` (default 0); approval enqueues an `analysis` job (`lane/api/analysis.py`).
- Default models in `lane/config.py`: classify and extract `claude-haiku-5-5`, escalation and draft `claude-sonnet-5-5`, analyze `claude-opus-5-5`.

#### OCR

RapidOCR with ONNX Runtime (`triage_ocr_models = "PP-OCRv6/small"`), loaded in-process by the worker or API. `backend/Dockerfile` notes that RapidOCR fetches its models on first use; in containers they are baked in at build time. On the Mac the first OCR use downloads them (the location is chosen by RapidOCR, not by Lane).

### Numbered connections

| # | From | To | Protocol, port | What |
|---|---|---|---|---|
| 1 | Browser | Next.js | HTTP 3000 | Pages, JS, CSS |
| 2 | Browser | API | HTTP 8000, cross-origin with CORS | JSON via `openapi-fetch`; SSE at `/api/v1/submissions/{id}/events` |
| 3 | API | Postgres | psycopg 5433 | Reads, writes, enqueues jobs (queue mode) |
| 4 | Worker | Postgres | psycopg 5433 | Claims jobs (`SKIP LOCKED`), renews leases, heartbeats, writes results, `pg_notify` |
| 5 | Postgres | API | `LISTEN lane_events` on its own connection | Worker progress events into the API's bus, then out over SSE (flow 2) |
| 6 | API | MinIO | S3 over HTTP 9000 (boto3) | Blobs for uploads, page images, evidence views |
| 7 | Worker | MinIO | S3 over HTTP 9000 | Blobs written and read by the pipeline |
| 8 | Watcher (in worker, API or standalone) | Mailpit | HTTP 8025 REST | List unread, fetch raw, mark read |
| 9 | `scripts/send_eml.py`, `scripts/send_reply.py --send` | Mailpit | SMTP 1025 | Agent emails in |
| 10 | Process running the pipeline | Anthropic API | HTTPS 443 | Classification, extraction, escalation, analysis, drafts |
| 11 | `scripts/send_reply.py` | API | HTTP 8000 (`LANE_LOCAL_API`, default `http://127.0.0.1:8000`) | `GET /api/v1/submissions` to find the sample's submission |
| 12 | `make migrate` (Alembic) | Postgres | psycopg 5433 | Schema upgrade to head |

### Environment variables

Settings come from `lane/config.py` (`pydantic-settings`, prefix `LANE_`, read from the repository `.env` and `backend/.env`; environment wins). The web app reads `NEXT_PUBLIC_*` at build or dev time.

#### Variables in `.env.example`

| Variable | Set by default in the example | Does |
|---|---|---|
| `ANTHROPIC_API_KEY` | empty | Key for every model call; without it `/health` reports `missing_key` and model steps degrade |
| `LANE_DATA_DIR` | commented, `./var` | Root for SQLite and local blobs |
| `LANE_DATABASE_URL` | commented, `postgresql+psycopg://localhost:5433/lane` | Empty means SQLite in `LANE_DATA_DIR` |
| `NEXT_PUBLIC_LANE_API_URL` | `http://localhost:8000` | Where the browser finds the API |
| `LANE_SESSION_SECRET` | commented | Signs demo sign-in tokens; the default is allowed only with `LANE_ENV=dev` |
| `LANE_LLM_ESCALATION_MODEL`, `LANE_LLM_ANALYZE_MODEL`, `LANE_LLM_DRAFT_MODEL` | commented, `claude-haiku-5-5` | Testing profile: every role on Haiku to keep spend low |
| `LANE_LLM_ANALYZE_MAX_TOKENS` | commented, `32000` | Room for Haiku's longer analysis answers |
| `LANE_LLM_CACHE_ONLY` | commented, `true` | Answer only from the model cache |
| `LANE_BLOB_BACKEND` | commented, `s3` | `local` or `s3` |
| `LANE_S3_BUCKET` | commented, `lane` | Bucket |
| `LANE_S3_ENDPOINT_URL` | commented, `http://127.0.0.1:9000` | MinIO endpoint; empty for AWS |
| `LANE_S3_ACCESS_KEY_ID`, `LANE_S3_SECRET_ACCESS_KEY` | commented, empty | MinIO keys you choose; also MinIO's root user and password via `make minio` |
| `LANE_S3_SSE` | commented, `aws:kms` | Server-side encryption on each write (AWS only) |

The example does not list `LANE_RUN_MODE` or `LANE_MAILPIT_URL`. The checked-out `.env` in this repository sets these keys (secret values not shown): `ANTHROPIC_API_KEY`, `NEXT_PUBLIC_LANE_API_URL=http://localhost:8000`, `LANE_DATABASE_URL=postgresql+psycopg://localhost:5433/lane_aws`, `LANE_RUN_MODE=queue`, `LANE_BLOB_BACKEND=s3`, `LANE_S3_BUCKET=lane-aws`, `LANE_S3_ENDPOINT_URL=http://127.0.0.1:9000`, `LANE_S3_ACCESS_KEY_ID`, `LANE_S3_SECRET_ACCESS_KEY`, `LANE_MAILPIT_URL=http://127.0.0.1:8025`. That is the full local stack: Postgres, queue worker, MinIO and Mailpit.

#### Other runtime settings worth knowing locally (`lane/config.py`)

| Variable | Default | Does |
|---|---|---|
| `LANE_ENV` | `dev` | Gates the secret check |
| `LANE_RUN_MODE` | `inline` | `inline` or `queue` (see worker) |
| `LANE_MAILPIT_URL`, `LANE_MAILPIT_POLL_S` | empty, 5.0 | Mailpit watcher on or off, poll interval |
| `LANE_EVENTS_CHANNEL` | `lane_events` | Postgres NOTIFY channel |
| `LANE_EVENTS_POLL_S`, `LANE_EVENTS_HEARTBEAT_S`, `LANE_EVENTS_IDLE_CLOSE_S` | 0.2, 15, 5 | SSE stream timing |
| `LANE_WORKER_LEASE_S`, `LANE_WORKER_RENEW_S`, `LANE_WORKER_POLL_S`, `LANE_WORKER_MAX_ATTEMPTS` | 30, 10, 1, 3 | Queue behavior |
| `LANE_CORS_ORIGINS` | `["http://localhost:3000"]` | Origins allowed to call the API |
| `LANE_LOG_JSON` | false | JSON logs (production) |
| `LANE_REQUIRE_SIGN_IN`, `LANE_DEMO_LOGIN`, `LANE_DEMO_DEFAULT_ROLE` | false, true, `viewer` | Sign-in policy; locally the demo people sign in |
| `LANE_COGNITO_*`, `LANE_SECRETS_ID`, `LANE_DB_SECRET_ID`, `LANE_SES_INBOX_PREFIX`, `LANE_INTAKE_ALLOWED_SENDERS` | empty | AWS only |
| `LANE_TRIAGE_OCR`, `LANE_TRIAGE_OCR_MODELS` | true, `PP-OCRv6/small` | OCR on or off, model set |
| `LANE_ANALYZE_AUTO_BUDGET_USD` | 0.0 | Expert review always asks before a paid run |
| `LANE_EXTRACT_V2` (or `EXTRACT_V2`) | true | Extraction v2; `0` runs v1 |

Test and tool variables: `LANE_TEST_PG_URL`, `LANE_TEST_ALL_ON_PG` (backend tests), `LANE_WEB_URL` (Playwright base URL, default `http://localhost:3000`), `LANE_LOCAL_API`, `LANE_LOCAL_TOKEN`, `LANE_LOCAL_SMTP` (`scripts/send_reply.py`), `EVAL_DB`, `GOLD`, `SUB`, `EML`, `APPROVE`, `DOC`, `NAME`, `PAGE`, `SOURCE` (Makefile arguments).

### Makefile targets

`Makefile` uses `SHELL := /bin/bash`, `BACKEND := backend`, `FRONTEND := frontend`; the default goal is `help`. Backend commands run from `backend/` through `uv run`.

#### Run the stack

| Target | Runs |
|---|---|
| `help` | Lists targets with their `##` comments |
| `setup` | `uv sync` in `backend/`; `npm ci` in `frontend/` |
| `migrate` | `uv run alembic upgrade head` |
| `dev` | `migrate`, then in parallel under `trap 'kill 0' EXIT`: `uvicorn lane.api.main:app --reload --port 8000`, `python -m lane.worker`, `npm run dev`; `wait` |
| `demo` | `migrate`, `npm run build`, then in parallel: uvicorn on 8000 without reload, the worker, `npm run start` |
| `worker` | `migrate`, then `python -m lane.worker` |
| `api` | `migrate`, then uvicorn with reload on 8000 |
| `web` | `npm run dev` |
| `minio` | `mkdir -p var/minio`; `minio server var/minio --address 127.0.0.1:9000 --console-address 127.0.0.1:9001` with root user and password read from `.env` |
| `mailpit` | `mailpit --smtp 127.0.0.1:1025 --listen 127.0.0.1:8025` |
| `mailpit-watch` | `LANE_MAILPIT_URL=${LANE_MAILPIT_URL:-http://127.0.0.1:8025} uv run python -m lane.intake.mailpit` |
| `send` | `uv run python ../scripts/send_eml.py "$(CURDIR)/<EML>"`; default `EML` is `var/synthetic/north_star.eml` |
| `blobs-to-s3` | `uv run python ../scripts/blobs_to_s3.py` |

#### Data and pipeline

| Target | Runs |
|---|---|
| `sample` | `python3 -I scripts/make_sample_eml.py` (writes `var/sample/cobalt_mesa.eml`) |
| `ingest` | `migrate`; builds the sample if `EML` is unset; `python -m lane.intake.cli <eml>`; `python -m lane.pipeline.cli process --latest` |
| `reset` | `migrate sample`; `python -m lane.intake.reset_cli` (deletes every submission; keeps audit log and model cache); then `make ingest` |
| `demo-reset` | `migrate sample`; `reset_cli`; `lane.intake.cli var/sample/cobalt_mesa.eml`; `pipeline.cli process --latest`; `pipeline.cli analyze --latest`, or a message that the expert review waits for `make analyze APPROVE=1` |
| `process` | `migrate`; `pipeline.cli process <SUB or --latest>` |
| `rules` | `pipeline.cli rules` |
| `analyze` | `pipeline.cli analyze`, plus `--approve` when `APPROVE` is set |
| `actions` | `pipeline.cli actions` (prints the draft email; never sent) |
| `resolution` | `pipeline.cli resolution` |
| `escalate` | `pipeline.cli escalate` |
| `documents` | `pipeline.cli documents` |
| `pages` | `pipeline.cli pages` |
| `run` | `ingest`, then `pipeline.cli export --latest > var/run/latest.json` |
| `template` | `python -m lane.documents.template_tool propose --document <DOC> --name <NAME> --page <PAGE or 1>` |
| `templates-check` | `python -m lane.documents.template_tool check` |

#### Evals

| Target | Runs |
|---|---|
| `eval` | `python -m lane.evals.score <GOLD or sample_packet> [--submission SUB]`, then `python -m lane.evals.perturb`; both under `LANE_DATABASE_URL=$(EVAL_DB)` when `EVAL_DB` is given |
| `perturb` | `scripts/make_perturbations.py`, `scripts/make_synthetic_packet.py`, then `python -m lane.evals.perturb` (optionally in `EVAL_DB`) |
| `eval-db` | `python ../scripts/eval_db.py $(EVAL_DB)` |
| `layout-speed` | `LANE_DATABASE_URL=$(EVAL_DB) python -m lane.evals.layout_speed --source $(SOURCE)` |

#### Quality

| Target | Runs |
|---|---|
| `lint` | `ruff check .`, `ruff format --check .`; `npm run lint`, `npm run format:check` |
| `format` | `ruff check --fix .`, `ruff format .`; `npm run format` |
| `typecheck` | `uv run pyright`; `npm run typecheck` (`next typegen && tsc --noEmit`) |
| `test` | `uv run pytest`; `npm test` (`vitest run`) |
| `check-pg` | `LANE_TEST_PG_URL=${LANE_TEST_PG_URL:-postgresql+psycopg://localhost:5433/lane_test} uv run pytest tests/test_postgres.py` |
| `test-pg` | `LANE_TEST_ALL_ON_PG=<same default> uv run pytest` (whole suite on Postgres, one cloned database per test) |
| `guard` | `scripts/no_hardcode_guard.sh` |
| `build` | `npm run build` |
| `check` | `lint typecheck test check-pg guard build`, in that order |
| `api-types` | `python ../scripts/export_openapi.py`; `npx openapi-typescript src/lib/api/openapi.json -o src/lib/api/schema.d.ts`; `npx prettier --write` both files |
| `screens` | `npx playwright test --project=light --project=dark` (needs `make dev` running) |
| `e2e` | `npx playwright test --project=behavior` (needs `make dev` running) |
| `clean` | `rm -rf var frontend/.next` |

### Developer tooling

#### Backend (`backend/`)

| Tool | Version or setting | Where |
|---|---|---|
| Python | `3.13` (`backend/.python-version`); `requires-python = ">=3.13,<3.14"` | `backend/pyproject.toml` |
| uv | not pinned for local use; `0.13.1` in the image (`COPY --from=ghcr.io/astral-sh/uv:0.13.1`); CI uses `astral-sh/setup-uv@v7`; lockfile `backend/uv.lock` | `backend/Dockerfile`, CI |
| Build backend | `hatchling`, package `src/lane` | `pyproject.toml` |
| ruff | `>=0.17`; line length 100, target `py313`; rules `E F W I B UP SIM RUF S PTH TID`, ignoring `S101`, `E501`; `S` off in tests | `[tool.ruff]` |
| pyright | `>=1.1.414`; `typeCheckingMode = "strict"` on `src` only, Python 3.13, venv `.venv` | `[tool.pyright]` |
| pytest | `>=9.2`; `testpaths = ["tests"]`, `-q --strict-markers`, `filterwarnings = ["error", ...]` (warnings are errors; one PyMuPDF SWIG DeprecationWarning ignored) | `[tool.pytest.ini_options]` |
| Other dev deps | `pytest-cov>=7.1`, `hypothesis>=6.170`, `httpx2>=2.16`, `types-pyyaml`, `moto[s3]>=5.1`, `boto3-stubs[s3]`, `botocore-stubs` | `[dependency-groups] dev` |

Runtime dependencies (`backend/pyproject.toml`): `fastapi>=0.162,<0.163`, `uvicorn[standard]>=0.54,<0.55`, `pydantic>=2.14,<3`, `pydantic-settings>=2.16,<3`, `sqlalchemy>=2.1,<2.2`, `alembic>=1.20,<2`, `structlog>=26.1`, `openpyxl>=3.1.5,<4`, `pillow>=13.0.0`, `numpy>=2.5.4`, `pymupdf>=1.28.2,<1.29`, `anthropic>=2.3,<3`, `rapidfuzz>=3.14,<4`, `rapidocr>=3.10,<4`, `onnxruntime>=1.31,<2`, `pyyaml>=6`, `psycopg[binary]>=3.2`, `boto3>=1.40`, `pyjwt[crypto]>=2.10,<3`.

Backend tests (`backend/tests/`, `conftest.py`): each test gets a temporary `LANE_DATA_DIR`, an empty `LANE_DATABASE_URL` (SQLite) unless `LANE_TEST_ALL_ON_PG` is set, an empty `ANTHROPIC_API_KEY`, and `LANE_TRIAGE_OCR=false`. `tests/test_postgres.py` is skipped unless `LANE_TEST_PG_URL` is set.

#### Frontend (`frontend/`)

| Tool | Version or setting | Where |
|---|---|---|
| Node | 24 (CI `node-version: 24`; image `node:24-bookworm-slim`; README "Node 24+"); no `.nvmrc` | |
| Next.js | `16.4.3`; `eslint-config-next` `16.4.3` | `package.json` |
| React | `19.3.0` | |
| Runtime deps | `@radix-ui/react-dialog ^1.3.1`, `@radix-ui/react-tooltip 1.4.1`, `@tanstack/react-query ^5.105.0`, `clsx`, `cmdk`, `d3-geo`, `lucide-react`, `motion`, `openapi-fetch ^0.17.0`, `sonner`, `topojson-client`, `us-atlas` | |
| TypeScript | `^5`, `"strict": true` | `tsconfig.json` |
| ESLint | `^9`, flat config with `next/core-web-vitals` and `next/typescript`; `--max-warnings=0`; ignores `src/lib/api/schema.d.ts`; in `src/lib/api/**` forbids hand-declared types | `eslint.config.mjs` |
| Prettier | `^3.9.10`, `printWidth: 100` | `.prettierrc` |
| Tailwind | `^4` via `@tailwindcss/turbopack` | `next.config.ts` |
| Vitest | `^5.0.5`, jsdom `^30.1.3`, `@vitejs/plugin-react`, Testing Library; tests `src/**/*.test.{ts,tsx}`; setup `vitest.setup.ts`; alias `@` to `src` | `vitest.config.mts` |
| Playwright | `@playwright/test ^1.64.1`, `@axe-core/playwright ^4.14.0` | `playwright.config.ts` |
| openapi-typescript | `^7.13.0` | `make api-types`, CI |

Playwright (`frontend/playwright.config.ts`): `testDir: "e2e"`, timeout 60 s, 2 workers, list reporter, base URL `LANE_WEB_URL` or `http://localhost:3000`, viewport 1440x900, scale 1. Projects:
- `light` and `dark`: `e2e/screens.spec.ts` with that color scheme; full-page screenshots written to `../docs/screens/<project>/<name>.png`.
- `behavior`: `e2e/behavior.spec.ts` in light (conflict resolution, roles, reveal, corrections and rerun, replay from cache, layouts, command palette, axe accessibility, and more). Fixture `e2e/fixtures/live-events.json`.
Both run against a live `make dev`; they start no server themselves.

#### API contract: OpenAPI export and generated client

1. `scripts/export_openapi.py` imports `create_app()` (no server), writes `create_app().openapi()` as sorted, indented JSON to `frontend/src/lib/api/openapi.json`. Operation ids are the handler names.
2. `npx openapi-typescript` turns it into `frontend/src/lib/api/schema.d.ts`; Prettier formats both.
3. `frontend/src/lib/api/index.ts` types every call from `paths` and `components` in that file; ESLint blocks hand-written shapes in `src/lib/api/`.
4. CI regenerates `schema.d.ts` from the committed `openapi.json` and fails on any diff.

#### Hardcode guard (`scripts/no_hardcode_guard.sh`)

Case-insensitive extended `grep -rniE` over `backend/src`, `backend/rules`, `backend/prompts`, `frontend/src` (those that exist) for sample-packet identifiers: the carrier name pattern, driver and party surnames, agency and carrier names, specific DOT, MC and policy-like numbers, VIN prefixes, and license-number patterns (`GAL[0-9]{7}`, `GMC[0-9]{7}`, `GA109[0-9]{4}`). Any hit prints the lines and exits 1; otherwise prints `guard: no packet specifics in ...`. Packet specifics are allowed only in `data/` and `evals/gold/`.

### Scripts (`scripts/`)

| Script | What it does | When |
|---|---|---|
| `make_sample_eml.py` | Builds `var/sample/cobalt_mesa.eml` from `data/packet_cobalt_mesa/`: each file attached and also zipped as `Sample Submission - Cobalt Mesa Transport.zip`; fixed Message-ID, Date, boundary and zip timestamps so the bytes are deterministic | `make sample`, `ingest`, `reset`, `demo-reset`; image build |
| `make_perturbations.py` | Writes perturbed copies of the sample to `var/perturb/<name>.eml` (blur, rotation, photo, injection text, deletions, renames and others); expectations in `evals/perturb/<name>.yaml` | `make perturb` |
| `make_synthetic_packet.py` | Builds a second, unseen packet (a two-truck Wisconsin carrier) into `var/synthetic/` including `north_star.eml` | `make perturb`; the default `make send` email |
| `send_eml.py` | Sends an `.eml` over SMTP; host must be `127.0.0.1`, `localhost`, `::1` or `mailpit`, port 1025 by default; replaces Date with now, keeps Message-ID | `make send` |
| `send_reply.py` | Builds the agent's reply to Lane's request: subject with the submission's reference token, FEIN in the body, a generated signed application and owner exclusion letter (PDFs); looks up the sample submission via `GET <LANE_LOCAL_API>/api/v1/submissions` unless `--submission`; writes `var/demo_reply.eml`; with `--send` sends to `LANE_LOCAL_SMTP` (default `127.0.0.1`) port 1025 | Demo state B, after Mark as sent |
| `export_openapi.py` | OpenAPI schema to `frontend/src/lib/api/openapi.json` | `make api-types` |
| `no_hardcode_guard.sh` | The guard above | `make guard`, `make check`, CI |
| `eval_db.py` | Drops, creates and migrates a target Postgres database, then copies every row of the main database into it (sample, runs, model cache) so evals never touch the inbox | `make eval-db` |
| `blobs_to_s3.py` | Copies `var/blobs` into the configured S3 bucket (same keys), creating the bucket if needed, skipping existing objects, checking each by hash | `make blobs-to-s3` |
| `sqlite_to_postgres.py` | One-time move of a SQLite database into a migrated, empty Postgres; `--merge-cache` adds model answers from other SQLite files | By hand |
| `demo_send.py` | Inside the deploy stack's worker container: `mailpit sample` (runs `send_eml.py --host mailpit` on `/app/var/sample/cobalt_mesa.eml`), `mailpit reply` (runs `send_reply.py --send` with `LANE_LOCAL_API=http://api:8000`, a minted viewer token and `LANE_LOCAL_SMTP=mailpit`), or the `ses` variants | `deploy/send-sample.sh` |
| `send_ses.py` | Sends an `.eml` through Amazon SES to the real inbox | AWS only |

### Sample data

- `data/packet_cobalt_mesa/`: the sample packet (15 files: three AL and three MTC loss-history PDFs, four IFTA quarter PDFs, the quote form PDF, MVR report PDF, and the application, driver list and vehicle list workbooks).
- `data/guideline/Knight_Guideline(Mockup).docx`: the underwriting guideline source.
- `var/sample/`: `cobalt_mesa.eml` (built by `make sample`), plus `cobalt_mesa_renamed.eml` and `cobalt_mesa_test.eml` present in this checkout.
- `var/reply-attachments/`: two PDFs (a signed application and a driver exclusion letter) present in this checkout; `send_reply.py` generates its own PDFs and does not read this folder.
- `var/` is git-ignored output: `var/blobs`, `var/lane.db` (SQLite), `var/minio`, `var/perturb`, `var/synthetic`, `var/run/latest.json`, `var/demo_reply.eml`.

How the sample enters:
1. Direct: `make ingest` or `make demo-reset` calls `lane.intake.cli` on the `.eml`, then `pipeline.cli process --latest`, in the CLI process (no web, no Mailpit).
2. Upload: `POST /api/v1/intake/eml` on the API.
3. As email: `make send EML=var/sample/cobalt_mesa.eml` to Mailpit, picked up by a watcher (flow b below).

### Evals (`evals/`)

| Path | Holds |
|---|---|
| `evals/gold/sample_packet.yaml`, `sample_packet_decision_facts.yaml` | Hand-built gold for the sample packet |
| `evals/perturb/*.yaml` | Ten perturbations: `blurred`, `corrupt`, `deleted_claim`, `duplicated`, `fein_in_email`, `injection`, `missing_loss_run`, `photo`, `renamed`, `rotated`; each with `name`, `what`, `expect` |
| `evals/synthetic/north_star.yaml` | Expected route, unknown rules, zero conflicts and missing items, 18 documents for `var/synthetic/north_star.eml` |
| `evals/report/` | Generated reports: `sample_packet.{json,md}`, `perturbations.{json,md}`, plus `compare_v1_v2.json`, `extraction_v1_vs_v2.md`, `follow_ups.md`, `layout_memory.md`, `v2_offline_on_v1.json` |
| `evals/baseline/README.md` | Describes `v1.db` (git-ignored frozen v1 database) |
| `evals/GOLD_CHANGES.md` | Log of gold edits |

- `make eval`: `lane.evals.score` scores the latest (or `SUB`) stored submission against the gold set from stored runs and writes `evals/report/<gold>.{json,md}`; then `lane.evals.perturb` ingests each `var/perturb/<name>.eml` (and the synthetic packet), processes only those without a decision yet (or all with `--reprocess`), checks expectations against the unperturbed baseline, and writes `evals/report/perturbations.*`.
- `make perturb`: rebuilds the perturbed and synthetic emails first, then the same perturbation check. With `EVAL_DB` both run in a separate database (built by `make eval-db`) so the inbox never gains eval submissions; processing there answers from the copied model cache.

### CI (`.github/workflows/ci.yml`)

Triggers: push to `main`, any pull request. Concurrency group `ci-${{ github.ref }}`, cancel in progress. Three jobs on `ubuntu-latest`, in parallel:

| Job | Working dir | Steps |
|---|---|---|
| `backend` | `backend` | `actions/checkout@v7`; `astral-sh/setup-uv@v7` with cache; `uv sync --locked`; `uv run ruff check .`; `uv run ruff format --check .`; `uv run pyright`; `uv run pytest --cov=lane --cov-report=term-missing` |
| `frontend` | `frontend` | `actions/checkout@v7`; `actions/setup-node@v7` (Node 24, npm cache on `frontend/package-lock.json`); `npm ci`; regenerate `schema.d.ts` from `openapi.json`, Prettier it, `git diff --exit-code`; `npm run lint`; `npm run format:check`; `npm run typecheck`; `npm test`; `npm run build` |
| `guard` | repo root | `actions/checkout@v7`; `scripts/no_hardcode_guard.sh` |

Differences from `make check`: CI runs no Postgres service, so `check-pg` and `test-pg` do not run (backend tests use SQLite); CI adds coverage and the schema drift check; Playwright does not run in CI. A second workflow, `.github/workflows/deploy.yml`, runs after a successful `ci` on `main` (or by hand) and deploys to AWS; it is outside the local picture.

### Local rehearsal of the AWS stack (Docker)

#### Compose files

`deploy/docker-compose.yml` (the AWS stack) with `deploy/docker-compose.local.yml` (the local override), run from `deploy/` with settings from `deploy/.env` (copied from `deploy/env.aws.example`):

| Service | Image | Ports | Command, notes |
|---|---|---|---|
| `caddy` | `caddy:2.10-alpine` | host `${LANE_HTTP_PORT:-80}:80`, `${LANE_HTTPS_PORT:-443}:443` (rehearsal: 8080) | `deploy/Caddyfile`: `/api/*` to `api:8000` (strips a Basic `Authorization` header, `flush_interval -1` for SSE); `/mail*` to `mailpit:8025` behind basic auth (`LANE_GATE_USER`, `LANE_GATE_HASH`); everything else to `web:3000`. Waits for healthy `web` and `api` |
| `web` | `${LANE_WEB_IMAGE:-lane-web:local}` | none published | Health: `fetch('http://127.0.0.1:3000/')` |
| `api` | `${LANE_API_IMAGE:-lane-api:local}` | none published | `uvicorn lane.api.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips *`; health: `GET /api/v1/health` |
| `worker` | same API image | none | `python -m lane.worker`; the only Mailpit watcher; after healthy `api` and started `mailpit` |
| `migrate` | same API image | none | `alembic upgrade head`; profile `tools`, run with `docker compose run --rm migrate` |
| `mailpit` | `axllent/mailpit:v1.32.0` | none published | `MP_DATABASE=/data/mailpit.db`, `MP_WEBROOT=mail`, `MP_MAX_MESSAGES=500`; volume `mailpit_data` |
| `postgres` (local override only) | `postgres:16-alpine` | `127.0.0.1:5434:5432` | user, password and database `lane`; volume `pg_data`; health `pg_isready -U lane`; `api`, `worker`, `migrate` wait for it |

All services share a json-file log driver (10 MB, 3 files) and `restart: unless-stopped` (except `migrate`). Volumes: `caddy_data`, `caddy_config`, `mailpit_data`, and `pg_data` locally. There is no MinIO service: the override's header says the rehearsal uses the Mac's own MinIO (`make minio`) reached as `host.docker.internal:9000`, bucket `lane-aws`. Postgres on 5434 lets `deploy/copy-cache.sh` read the rehearsal's model cache, OCR cache and layouts (default source `postgresql://lane:lane@127.0.0.1:5434/lane`), since the rehearsal's Linux arm64 images make page images and cache keys match the EC2.

Rehearsal steps (`deploy/README.md`): copy `env.aws.example` to `deploy/.env` and set local values; `docker build -f backend/Dockerfile -t lane-api:local .`; `docker build -f frontend/Dockerfile --build-arg NEXT_PUBLIC_LANE_API_URL=http://localhost:8080 -t lane-web:local .`; `docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm migrate`; `... up -d`; `./send-sample.sh local` (and `./send-sample.sh local reply`), which runs `docker compose ... exec -T worker python /app/scripts/demo_send.py mailpit <sample|reply>`. Open `http://localhost:8080`.

#### `deploy/env.aws.example`

Site: `LANE_SITE` (rehearsal `http://:80`, per the compose comment), `LANE_CORS_ORIGINS`, `LANE_GATE_USER`, `LANE_GATE_HASH`, `LANE_GATE_TOKEN` (the `/mail` password, made by `deploy/gate.sh`), `LANE_HTTP_PORT`, `LANE_HTTPS_PORT`, `LANE_API_IMAGE`, `LANE_WEB_IMAGE`. Backend: `ANTHROPIC_API_KEY`, `LANE_DATABASE_URL` (rehearsal `postgresql+psycopg://lane:lane@postgres:5432/lane`), `LANE_RUN_MODE=queue`, `LANE_MAILPIT_URL=http://mailpit:8025/mail`, `LANE_BLOB_BACKEND=s3`, `LANE_S3_BUCKET`, `LANE_S3_REGION=us-east-1`, `LANE_S3_ENDPOINT_URL`, `LANE_S3_ACCESS_KEY_ID`, `LANE_S3_SECRET_ACCESS_KEY` (rehearsal comment: endpoint `http://minio:9000`, bucket `lane`), `LANE_ANALYZE_AUTO_BUDGET_USD=0`.

#### Dockerfiles

`backend/Dockerfile` (one image for API, worker and migrations; built from the repo root):
1. Base `python:3.13-slim-bookworm`; apt installs `libgomp1` (ONNX Runtime), `libglib2.0-0` (OpenCV), `ca-certificates`.
2. `uv` 0.13.1 copied from `ghcr.io/astral-sh/uv:0.13.1`; `UV_COMPILE_BYTECODE=1`, `UV_LINK_MODE=copy`, `UV_PYTHON_DOWNLOADS=never`, `PYTHONUNBUFFERED=1`, venv on `PATH`.
3. Unprivileged user `lane` (uid 10001), workdir `/app/backend`.
4. `uv sync --frozen --no-dev --no-install-project` from `pyproject.toml` and `uv.lock`, then copies `backend/`, `config/`, `data/`, `evals/`, `scripts/` to `/app/...` and `uv sync --frozen --no-dev`.
5. Bakes in `var/sample/cobalt_mesa.eml` by running `make_sample_eml.py`.
6. Bakes in the OCR models by running `read_words` on a blank image.
7. `EXPOSE 8000`; default command is the API's uvicorn line.

`frontend/Dockerfile` (two stages):
1. `build` on `node:24-bookworm-slim`: `npm ci`, copy `frontend/`, build args `NEXT_PUBLIC_LANE_API_URL` (required; the build fails without it), `NEXT_PUBLIC_LANE_COGNITO_DOMAIN`, `NEXT_PUBLIC_LANE_COGNITO_CLIENT_ID` (empty: demo people), `NEXT_TELEMETRY_DISABLED=1`, `npm run build`.
2. Runtime on `node:24-bookworm-slim`: `NODE_ENV=production`, `PORT=3000`, `HOSTNAME=0.0.0.0`, copies `/app` as user `node`, `EXPOSE 3000`, `npx next start -p 3000 -H 0.0.0.0`.

The API address is fixed into the web bundle at build time.

`.dockerignore` excludes `**/.venv`, `**/node_modules`, `**/.next`, `**/__pycache__`, `**/.pytest_cache`, `**/.ruff_cache`, `**/*.tsbuildinfo`, `.git`, `.env`, `.env.*` (except `.env.example`), `deploy/.env`, `docs`, `backups`, `var/*` (except `var/sample`), `evals/baseline`, `frontend/test-results`, `frontend/playwright-report`.

### Flows

#### (a) `make dev` startup order

1. `make migrate`: `alembic upgrade head` against `LANE_DATABASE_URL` (Postgres on 5433, or SQLite in `var/`). Postgres must already be running; MinIO and Mailpit are separate terminals (`make minio`, `make mailpit`) and are not started by `make dev`.
2. Bash sets `trap 'kill 0' EXIT` so stopping `make dev` stops all three children.
3. In parallel:
   1. uvicorn with `--reload` on port 8000 imports `lane.api.main`, builds the app, runs `Lifespan`: `check_secrets()`; in queue mode starts the `LISTEN lane_events` thread; in inline mode recovers interrupted runs, warms OCR, starts the Mailpit watcher if configured.
   2. `python -m lane.worker`: in inline mode exits at once; in queue mode sets up the NOTIFY publisher, warms OCR, starts the Mailpit watcher (if `LANE_MAILPIT_URL`), starts heartbeats, logs `worker.ready`, and polls the `jobs` table every second.
   3. `npm run dev` (`next dev`) on port 3000.
4. `wait` until interrupted.

#### (b) `make send` to a finished Brief (queue mode, Postgres, MinIO, Mailpit)

1. `make send EML=var/sample/cobalt_mesa.eml` runs `scripts/send_eml.py`, which resets the Date header and sends over SMTP to `127.0.0.1:1025`.
2. Mailpit stores the message, unread.
3. Within 5 s the watcher thread in the worker lists unread messages over `http://127.0.0.1:8025/api/v1/messages`, fetches the raw message.
4. `ingest_email` (source `smtp`): idempotent by Message-ID; the email, attachments and expanded archive members are written to MinIO (S3 `PUT` on 9000, sha256 keys under `blobs/`) and recorded in Postgres.
5. For a new submission the watcher enqueues a `process` job in `jobs`, then marks the message read in Mailpit.
6. The worker's loop claims the job (`FOR UPDATE SKIP LOCKED`), starts a lease keeper thread, records the run id on the job.
7. The pipeline runs its steps in order: `triage`, `classify`, `extract`, `resolve`, `rules`, `analyze` (optional), `actions` (`lane/pipeline/registry.py`). Page images and OCR go through MinIO and the OCR cache; model calls go to the Anthropic API unless the cache answers; `analyze` is skipped as awaiting approval unless cached or within budget.
8. Each step event goes to the worker's bus and out with `pg_notify('lane_events', ...)`; the API's listener republishes it; a browser on the submission page receives it over SSE (`/api/v1/submissions/{id}/events`), which opens with a snapshot and closes a few seconds after the run finishes.
9. The worker marks the job `done` (or `failed` with the reason). The browser reads the Brief from the API (JSON over 8000; the API reads Postgres and MinIO).
10. If a person approves the expert review in the UI, the API enqueues an `analysis` job; the worker runs analysis and rebuilds actions.

In inline mode steps 3 to 9 run on threads of the API process instead, and step 8 needs no NOTIFY.

#### (c) The agent's reply locally

1. In the UI, the underwriter marks the agent request as sent (the reply is measured against the request marked sent; otherwise against the last draft).
2. `cd backend && uv run python ../scripts/send_reply.py --send`:
   1. `GET http://127.0.0.1:8000/api/v1/submissions` finds the sample's submission (or `--submission ID`).
   2. Builds the reply: subject with `[Ref <reference>]`, FEIN in the body, a generated signed application PDF and owner exclusion letter PDF; saves `var/demo_reply.eml`.
   3. Sends it over SMTP to `127.0.0.1:1025`.
3. The watcher ingests it as in flow (b); follow-up linking (`lane/intake/link.py`) matches the `[Ref ...]` token in the subject, attaches the new files to the original submission, and a `process` job runs the pipeline on it again.
4. The Brief shows what the reply answered (the script's docstring gives the expected rule and route changes).

In the Docker rehearsal the same thing runs as `deploy/send-sample.sh local reply`, inside the worker container against `api:8000` and `mailpit:1025`.

#### (d) `make check`

Runs in order, stopping at the first failure:
1. `lint`: `ruff check .`, `ruff format --check .` (backend); `eslint --max-warnings=0`, `prettier --check .` (frontend).
2. `typecheck`: `pyright` strict on `backend/src`; `next typegen && tsc --noEmit`.
3. `test`: `pytest` (SQLite per test, no model key, no OCR, warnings as errors); `vitest run` (jsdom).
4. `check-pg`: `pytest tests/test_postgres.py` against `LANE_TEST_PG_URL` (default `postgresql+psycopg://localhost:5433/lane_test`). The target always supplies a default URL, so the tests do not skip: each creates and migrates a fresh database on that server, which needs a Postgres on 5433 that allows `CREATE DATABASE`.
5. `guard`: `scripts/no_hardcode_guard.sh`.
6. `build`: `next build`.

#### (e) A CI run

1. A push to `main` or a pull request starts `ci`; a newer run on the same ref cancels the older one.
2. `backend`, `frontend` and `guard` start in parallel on fresh Ubuntu runners (steps in the CI table above). No Postgres, MinIO, Mailpit or Anthropic key is involved; backend tests run on SQLite with the model key empty.
3. The run passes when all three jobs pass. On `main`, a successful `ci` triggers `deploy.yml` (AWS; out of scope here).

## Lane on AWS: the deployment as built

Lane runs at https://laneunder.online as one CloudFormation stack named `lane` in `us-east-1`. One Graviton EC2 instance runs the whole Docker stack (Caddy, web, API, worker, Mailpit); a private RDS PostgreSQL 16 instance holds the database; one S3 bucket holds files, inbound email and deploy hand-offs; Secrets Manager holds the secrets; Cognito signs people in; SES receives real email; CloudWatch collects logs and raises alarms to an SNS email topic; GitHub Actions deploys through an OIDC role. Everything in this section is read from the code in the `lane-aws` repository. The source of truth for the infrastructure is `deploy/cloudformation.yml`; the runtime is `deploy/docker-compose.yml`, `deploy/docker-compose.aws.yml` and `deploy/Caddyfile`.

The deployed stack was checked read-only on 2026-11-03: status `UPDATE_COMPLETE`, the deployed template is identical to `deploy/cloudformation.yml` (only a trailing newline differs), and the stack holds 55 resources, the same 55 the template declares.

### As deployed (read-only check)

| Item | Value |
|---|---|
| Account, region | `367959205637`, `us-east-1` |
| Site, second name | `laneunder.online`, `54-158-224-242.sslip.io` |
| Elastic IP | `54.158.224.242` |
| Instance | `i-039449781559b8941`, `t4g.large`, `us-east-1a`, AMI `ami-0951c836c26e34fc8` (resolved from the SSM parameter), IMDSv2 required, hop limit 2 |
| VPC | default VPC `vpc-01a76d5ffe02ccd6c`; instance subnet `subnet-01ce109e726dcf57e`; database subnet group spans six default subnets |
| Database | `lane.cgz04wk66mu5.us-east-1.rds.amazonaws.com`, PostgreSQL 16.13, `db.t4g.micro`, single AZ (`us-east-1c`), encrypted, 7-day backups |
| Bucket | `lane-367959205637-us-east-1` |
| Hosted zone | `Z032145132N031562JLDA`; name servers `ns-1594.awsdns-07.co.uk`, `ns-1099.awsdns-09.org`, `ns-266.awsdns-33.com`, `ns-784.awsdns-34.net` (set at the registrar, Porkbun) |
| Inbox | `submissions@laneunder.online`; active receipt rule set `lane-inbound` |
| Cognito | pool `us-east-1_muHui8u4M`, client `493qq5i29gjfvij8a7039cp6lm`, domain `https://lane-367959205637.auth.us-east-1.amazoncognito.com` |
| Deploy role | `arn:aws:iam::367959205637:role/lane-github-deploy` |
| Alerts | SNS `lane-alerts`, one email subscription (confirmed); all seven alarms in state `OK` |

### 1. The CloudFormation template, resource by resource

File: `deploy/cloudformation.yml`. 55 resources, 8 parameters, 1 condition, 19 outputs.

#### Parameters

| Parameter | Type | Default | Purpose |
|---|---|---|---|
| `VpcId` | `AWS::EC2::VPC::Id` | none | The default VPC; `deploy/stack.sh` looks it up |
| `SubnetIds` | `List<AWS::EC2::Subnet::Id>` | none | Two or more subnets in different zones, for the DB subnet group |
| `InstanceSubnetId` | `AWS::EC2::Subnet::Id` | none | The subnet the EC2 runs in |
| `InstanceType` | `String` | `t4g.large` | EC2 size |
| `AlertEmail` | `String`, pattern `[^@ ]+@[^@ ]+\.[^@ ]+` | none | Where alarms are emailed |
| `DomainName` | `String` | `""` | The site's own domain; empty means sslip.io only |
| `GitHubSubject` | `String` | `repo:vamsijilla@118758121/lane-aws@1443616880` | GitHub's immutable OIDC subject (owner and repo with numeric ids), so a re-created repo of the same name cannot deploy |
| `AmiId` | `AWS::SSM::Parameter::Value<AWS::EC2::Image::Id>` | `/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64` | Latest Amazon Linux 2023 arm64 |

#### Condition

- `HasDomain`: `DomainName` is not empty. It gates the hosted zone, every DNS record, the SES identity, the receipt rule set and rule, the inbox bucket policy, and the outputs `InboxAddress`, `HostedZoneId`, `NameServers`. It also switches the site name used by the health check, dashboard, Cognito URLs and the SES `FromAddress` condition.

The "sslip name" used in several places is built as `<a-b-c-d>.sslip.io` from the Elastic IP (`!Join ["-", !Split [".", !Ref Address]]`).

#### Edge and DNS (Route 53)

| Logical id | Type | Key settings | Connects to |
|---|---|---|---|
| `HostedZone` | `AWS::Route53::HostedZone` | Name `DomainName`; comment "Lane; records are added by this stack" (HasDomain) | Registrar NS records point here |
| `SiteRecord` | `AWS::Route53::RecordSet` | `A`, apex, TTL 300, value `Address` (the EIP) | Elastic IP on the instance |
| `MailExchangeRecord` | `AWS::Route53::RecordSet` | `MX`, apex, TTL 300, `10 inbound-smtp.us-east-1.amazonaws.com` | SES inbound SMTP |
| `SpfRecord` | `AWS::Route53::RecordSet` | `TXT`, apex, TTL 300, `"v=spf1 include:amazonses.com -all"` | SES sending |
| `DmarcRecord` | `AWS::Route53::RecordSet` | `TXT`, `_dmarc.<domain>`, TTL 300, `"v=DMARC1; p=quarantine; adkim=r; aspf=r"` | Receivers of mail from the domain |
| `DkimRecord1`, `DkimRecord2`, `DkimRecord3` | `AWS::Route53::RecordSet` | `CNAME`, TTL 1800, name and value from `MailIdentity.DkimDNSTokenName1..3` and `DkimDNSTokenValue1..3` | SES Easy DKIM keys |
| `SiteHealth` | `AWS::Route53::HealthCheck` | `HTTPS`, FQDN = domain (or sslip name), path `/api/v1/health`, port 443, interval 30 s, failure threshold 3; tag `Name=lane-site` | Caddy, then the API health route; feeds `SiteDownAlarm` |

#### Network

The stack creates no VPC; it uses the account's default VPC and its default subnets (public, with an internet gateway). Neither a NAT gateway nor VPC endpoints exist; the instance reaches AWS APIs over its public address.

| Logical id | Type | Key settings | Connects to |
|---|---|---|---|
| `WebSecurityGroup` | `AWS::EC2::SecurityGroup` | Ingress TCP 80 from `0.0.0.0/0`, TCP 443 from `0.0.0.0/0`. No SSH. Egress: default (all). Description "Lane web, HTTP and HTTPS only (no SSH; admin is by SSM)" | Attached to `Instance` |
| `DatabaseSecurityGroup` | `AWS::EC2::SecurityGroup` | Ingress TCP 5432 from source security group `WebSecurityGroup` only. Egress: default | Attached to `Database` |
| `Address` | `AWS::EC2::EIP` | `Domain: vpc`, `InstanceId: Instance` | `SiteRecord`, Cognito callback URLs, sslip name |

#### Compute

| Logical id | Type | Key settings | Connects to |
|---|---|---|---|
| `Instance` | `AWS::EC2::Instance` | AMI `AmiId` (AL2023 arm64), type `InstanceType` (`t4g.large`), subnet `InstanceSubnetId`, profile `InstanceProfile`, SG `WebSecurityGroup`; `MetadataOptions`: `HttpTokens: required` (IMDSv2 only), `HttpPutResponseHopLimit: 2` (so containers reach the role); root `/dev/xvda` 30 GiB gp3, encrypted; tag `Name=lane` | EIP, RDS, S3, ECR, Secrets Manager, SES, CloudWatch Logs, SSM |
| `InstanceProfile` | `AWS::IAM::InstanceProfile` | Roles: `InstanceRole` | `Instance` |

User data (bash, `set -euxo pipefail`), run once at first boot:

1. `dnf install -y docker`.
2. Download Docker Compose v2.39.2 for `linux-aarch64` from GitHub releases into `/usr/local/lib/docker/cli-plugins/docker-compose`, `chmod +x`.
3. `systemctl enable --now docker`.
4. `mkdir -p /opt/lane` and `touch /opt/lane/.ready` (the marker `stack.sh up` waits for).

The AL2023 AMI already carries the SSM agent and the AWS CLI, which every deploy script relies on (`aws s3 cp`, `aws ecr get-login-password` on the instance).

#### Data: RDS

| Logical id | Type | Key settings | Connects to |
|---|---|---|---|
| `DatabaseSubnets` | `AWS::RDS::DBSubnetGroup` | Subnets `SubnetIds` (all default subnets) | `Database` |
| `DatabaseParameters` | `AWS::RDS::DBParameterGroup` | Family `postgres16`; `rds.force_ssl: "1"` (TLS required for every connection) | `Database` |
| `Database` | `AWS::RDS::DBInstance` | Identifier `lane`; engine `postgres` `16`; `db.t4g.micro`; 20 GB gp3; `StorageEncrypted: true` (default RDS KMS key); DB name `lane`; master user `lane`; master password resolved from `DatabaseSecret` (`{{resolve:secretsmanager:...:SecretString:password}}`); `PubliclyAccessible: false`; SG `DatabaseSecurityGroup`; `BackupRetentionPeriod: 7`; `AutoMinorVersionUpgrade: true`; `DeletionProtection: false`; `DeletionPolicy: Delete` (no final snapshot); `UpdateReplacePolicy: Snapshot`; single AZ (no `MultiAZ`) | API, worker, migrate over TCP 5432 TLS |

#### Data: S3

| Logical id | Type | Key settings | Connects to |
|---|---|---|---|
| `Bucket` | `AWS::S3::Bucket` | Name `lane-<AccountId>-<Region>`; public access block all four true; default encryption SSE-S3 (`AES256`); `ObjectOwnership: BucketOwnerEnforced` (ACLs off); lifecycle rules below; no versioning | SES (writes `inbound/`), instance role (read, write, delete, list), deploy role (write `deploy/`) |
| `InboxPolicy` | `AWS::S3::BucketPolicy` | One statement `SesDeliversInbound`: Allow principal `ses.amazonaws.com` `s3:PutObject` on `<bucket>/inbound/*`, conditions `AWS:SourceAccount = <account>` and `AWS:SourceArn` like `arn:aws:ses:us-east-1:<account>:receipt-rule-set/lane-inbound:receipt-rule/lane-submissions` (HasDomain) | SES receipt rule |

Lifecycle rules and prefixes:

| Prefix | Rule id | Expiry | What lives there |
|---|---|---|---|
| `deploy/` | `deploy-handoff` | 1 day | Compose files, Caddyfile, scripts, `build.env`, the `.env` (deleted right after copy), `src-<sha>.tgz` from CI, `deploy/cache/` from `copy-cache.sh` |
| `inbound/` | `inbound-mail` | 30 days | Raw emails SES stored; also `inbound/done/` and `inbound/held/` (prefix match covers them) |
| `blobs/` | none | never | Content-addressed files (emails, attachments, page images) written by the app (`LANE_S3_PREFIX` default `blobs/`) |

#### Email: SES

| Logical id | Type | Key settings | Connects to |
|---|---|---|---|
| `MailIdentity` | `AWS::SES::EmailIdentity` | Identity = `DomainName`; Easy DKIM `NextSigningKeyLength: RSA_2048_BIT` (HasDomain) | `DkimRecord1..3`; lets `agent@<domain>` send |
| `InboundRules` | `AWS::SES::ReceiptRuleSet` | Name `lane-inbound` (HasDomain). CloudFormation cannot activate it; `stack.sh up` does | `SubmissionsRule` |
| `SubmissionsRule` | `AWS::SES::ReceiptRule` | DependsOn `InboxPolicy`, `MailIdentity`. Rule `lane-submissions`, enabled; recipients `submissions@<domain>` only; `ScanEnabled: true` (spam and virus verdict headers); `TlsPolicy: Optional`; one action: `S3Action` to `Bucket`, prefix `inbound/` | S3 `inbound/` |

#### Identity: Cognito

| Logical id | Type | Key settings | Connects to |
|---|---|---|---|
| `UserPool` | `AWS::Cognito::UserPool` | Name `lane`; username is email; auto-verify email; `AllowAdminCreateUserOnly: true` (invitation only; invite subject "Your Lane account" with temporary password); password policy min 12, lower, upper, numbers, no symbols required, temporary password valid 7 days; MFA `OPTIONAL` with `SOFTWARE_TOKEN_MFA`; recovery by verified email; `DeletionProtection: INACTIVE` | Groups, client, domain |
| `SeniorUnderwriterGroup` | `AWS::Cognito::UserPoolGroup` | `senior_underwriter` | Lane role |
| `UnderwriterGroup` | `AWS::Cognito::UserPoolGroup` | `underwriter` | Lane role |
| `ViewerGroup` | `AWS::Cognito::UserPoolGroup` | `viewer` (read only) | Lane role |
| `OpsGroup` | `AWS::Cognito::UserPoolGroup` | `ops` | Lane role |
| `AdminGroup` | `AWS::Cognito::UserPoolGroup` | `admin` | Lane role |
| `UserPoolDomain` | `AWS::Cognito::UserPoolDomain` | Prefix domain `lane-<AccountId>` (managed login at `https://lane-<AccountId>.auth.us-east-1.amazoncognito.com`) | Browser |
| `UserPoolClient` | `AWS::Cognito::UserPoolClient` | Name `lane-web`; `GenerateSecret: false` (public client, PKCE); OAuth flow `code` only; scopes `openid email profile`; IdP `COGNITO`; callback URLs `https://<domain>/auth/callback`, `https://<sslip>/auth/callback`, `http://localhost:8080/auth/callback`; logout URLs the same three origins with `/`; explicit flows `ALLOW_USER_SRP_AUTH`, `ALLOW_REFRESH_TOKEN_AUTH`; `PreventUserExistenceErrors: ENABLED`; ID and access tokens 60 minutes; refresh token 1 day | Browser (`frontend/src/lib/cognito.ts`), API (audience check) |

#### Secrets

| Logical id | Type | Key settings | Read by |
|---|---|---|---|
| `DatabaseSecret` | `AWS::SecretsManager::Secret` | Name `lane/db`; generated: template `{"username": "lane"}`, key `password`, 40 characters, no punctuation. Never rotated (no rotation resource), so the app's copy never goes stale | RDS (via dynamic reference at create), API, worker, migrate |
| `AppSecret` | `AWS::SecretsManager::Secret` | Name `lane/app`; generated: template `{"ANTHROPIC_API_KEY": ""}`, key `LANE_SESSION_SECRET`, 64 characters, no punctuation. `ANTHROPIC_API_KEY` is filled later by `deploy/secrets.sh` | API, worker, migrate |

#### IAM

`InstanceRole` (`AWS::IAM::Role`), trusted by `ec2.amazonaws.com` (`sts:AssumeRole`):

| Source | Statement |
|---|---|
| Managed `AmazonSSMManagedInstanceCore` | SSM agent: Run Command and Session Manager |
| Managed `AmazonEC2ContainerRegistryReadOnly` | Pull from ECR |
| Inline `lane-files`, 1 | Allow `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` on `<bucket>/*` |
| Inline `lane-files`, 2 | Allow `s3:ListBucket` on the bucket |
| Inline `lane-files`, 3 | Allow `secretsmanager:GetSecretValue` on `AppSecret` and `DatabaseSecret` |
| Inline `lane-files`, 4 | Allow `ses:SendRawEmail`, `ses:SendEmail` on `*` with condition `StringEquals ses:FromAddress = agent@<domain>` (or `nobody@invalid` without a domain) |
| Inline `lane-files`, 5 | Allow `logs:CreateLogStream`, `logs:PutLogEvents`, `logs:DescribeLogStreams` on `arn:aws:logs:us-east-1:<account>:log-group:/lane/*` |

`GitHubOidc` (`AWS::IAM::OIDCProvider`): URL `https://token.actions.githubusercontent.com`, client id list `sts.amazonaws.com`.

`DeployRole` (`AWS::IAM::Role`, name `lane-github-deploy`):

- Trust: principal `Federated: GitHubOidc`, action `sts:AssumeRoleWithWebIdentity`, condition `StringEquals` on `token.actions.githubusercontent.com:aud = sts.amazonaws.com` and `token.actions.githubusercontent.com:sub = <GitHubSubject>:ref:refs/heads/main` (only the main branch of that one repository).
- Inline policy `lane-deploy`:

| # | Action | Resource |
|---|---|---|
| 1 | `s3:PutObject` | `<bucket>/deploy/*` |
| 2 | `ssm:SendCommand` | `arn:aws:ec2:us-east-1:<account>:instance/<Instance>` and `arn:aws:ssm:us-east-1::document/AWS-RunShellScript` |
| 3 | `ssm:GetCommandInvocation`, `ssm:ListCommandInvocations` | `*` |
| 4 | `cloudformation:DescribeStacks` | `arn:aws:cloudformation:us-east-1:<account>:stack/<StackName>/*` |

No ECR push rights: CI does not push images (it builds on the instance).

#### Container registry

| Logical id | Type | Key settings |
|---|---|---|
| `ApiRepository` | `AWS::ECR::Repository` | `lane-api`; `EmptyOnDelete: true`; scan on push; lifecycle: expire any tag beyond the newest 10 |
| `WebRepository` | `AWS::ECR::Repository` | `lane-web`; same settings |

Only the manual path (`deploy/deploy.sh`) pushes here; the instance pulls with `AmazonEC2ContainerRegistryReadOnly`.

#### Observability

Log groups (`AWS::Logs::LogGroup`, 30-day retention each): `ApiLogs` `/lane/api`, `WorkerLogs` `/lane/worker`, `WebLogs` `/lane/web`, `CaddyLogs` `/lane/caddy`, `MailpitLogs` `/lane/mailpit`.

Metric filters (`AWS::Logs::MetricFilter`, namespace `Lane`, default value 0):

| Logical id | Log group | Pattern | Metric, value |
|---|---|---|---|
| `WorkerRunFailures` | `/lane/worker` | `{ ($.event = "step.failed") \|\| ($.event = "job.failed") }` | `RunFailures`, 1 |
| `ApiRunFailures` | `/lane/api` | same | `RunFailures`, 1 |
| `WorkerModelSpend` | `/lane/worker` | `{ $.event = "llm.call" }` | `ModelSpendUSD`, `$.cost_usd` |
| `ApiModelSpend` | `/lane/api` | same | `ModelSpendUSD`, `$.cost_usd` |

`AlertTopic` (`AWS::SNS::Topic`): name `lane-alerts`, one inline subscription, protocol `email`, endpoint `AlertEmail` (confirmed once by the recipient).

Alarms (`AWS::CloudWatch::Alarm`):

| Logical id, name | Metric | Statistic, period, periods | Condition | Missing data | Actions |
|---|---|---|---|---|---|
| `SiteDownAlarm`, `lane-site-down` | `AWS/Route53` `HealthCheckStatus`, dim `HealthCheckId=SiteHealth` | Minimum, 60 s, 3 | `< 1` | breaching | Alarm and OK: SNS `lane-alerts` |
| `HostRecoverAlarm`, `lane-host-recover` | `AWS/EC2` `StatusCheckFailed_System`, dim `InstanceId` | Maximum, 60 s, 2 | `>= 1` | default | `arn:aws:automate:us-east-1:ec2:recover`, SNS |
| `HostRebootAlarm`, `lane-host-reboot` | `AWS/EC2` `StatusCheckFailed_Instance`, dim `InstanceId` | Maximum, 60 s, 3 | `>= 1` | default | `arn:aws:automate:us-east-1:ec2:reboot`, SNS |
| `RunFailedAlarm`, `lane-run-failed` | `Lane` `RunFailures` | Sum, 300 s, 1 | `>= 1` | notBreaching | SNS |
| `ModelSpendAlarm`, `lane-model-spend` | `Lane` `ModelSpendUSD` | Sum, 86400 s, 1 | `> 5` (USD per day) | notBreaching | SNS |
| `DatabaseStorageAlarm`, `lane-db-storage` | `AWS/RDS` `FreeStorageSpace`, dim `DBInstanceIdentifier` | Minimum, 300 s, 1 | `< 2000000000` (2 GB) | default | SNS |
| `DatabaseCpuAlarm`, `lane-db-cpu` | `AWS/RDS` `CPUUtilization`, dim `DBInstanceIdentifier` | Average, 300 s, 3 | `> 80` | default | SNS |

`Dashboard` (`AWS::CloudWatch::Dashboard`, name `lane`, default range last 24 h), widgets top to bottom:

| Position (x, y, w, h) | Type | Content |
|---|---|---|
| 0, 0, 24, 1 | text | "Lane on AWS", link to the site, note that alarms email `lane-alerts` |
| 0, 1, 24, 3 | alarm | All seven alarms |
| 0, 4, 8, 6 | metric | Site health (1 = up): `HealthCheckStatus` Minimum, 60 s, y axis 0 to 1 |
| 8, 4, 8, 6 | metric | Model spend (USD per hour): `Lane ModelSpendUSD` Sum, 3600 s |
| 16, 4, 8, 6 | metric | Failed runs: `Lane RunFailures` Sum, 300 s |
| 0, 10, 8, 6 | metric | Instance CPU (%): `AWS/EC2 CPUUtilization` Average, 300 s |
| 8, 10, 8, 6 | metric | Database CPU (%) and connections (right axis): `AWS/RDS CPUUtilization`, `DatabaseConnections` |
| 16, 10, 8, 6 | metric | Database free storage (GB): expression `m1 / 1000000000` over `FreeStorageSpace` Minimum |
| 0, 16, 12, 7 | log (table) | Logs Insights over `/lane/worker` and `/lane/api`: `event = 'llm.call'`, calls and `sum(cost_usd)` by `purpose`, `cache_hit` |
| 12, 16, 12, 7 | log (table) | Same groups: `level in ['error','warning']`, fields `@timestamp, event, error`, newest 20 |

#### Outputs

`PublicIp`, `Site` (domain, or sslip name), `SslipSite`, `InboxAddress` (HasDomain), `InstanceId`, `BucketName`, `DatabaseHost`, `ApiRepositoryUri`, `WebRepositoryUri`, `AppSecretArn`, `DatabaseSecretArn`, `UserPoolId`, `UserPoolClientId`, `CognitoDomain`, `DeployRoleArn`, `AlertTopicArn`, `DashboardUrl`, `HostedZoneId` (HasDomain), `NameServers` (HasDomain; pasted into the registrar). Every script reads these through `load_outputs` in `deploy/lib.sh`.

### 2. The deploy scripts

All scripts are in `deploy/`.

#### `lib.sh` (sourced by the others)

- `set -euo pipefail`; `DEPLOY_DIR`, `REPO_DIR`; `PROFILE=${AWS_PROFILE:-lane}`, `REGION=${AWS_REGION:-us-east-1}`, `STACK=${LANE_STACK:-lane}`.
- `aws()` wraps the CLI with `--profile` and `--region`.
- `load_outputs`: `cloudformation describe-stacks`, exports each output as `LANE_OUT_<Key>`; fails with "run deploy/stack.sh up" when the stack is missing.
- `on_host <script>`: `ssm send-command` with document `AWS-RunShellScript`, timeout 1800 s, the script as one command (JSON built in Python); polls `get-command-invocation` every 3 s until not Pending, InProgress or Delayed; prints stdout and stderr; fails unless `Success`. This is the only way the scripts touch the instance (no SSH).

#### `stack.sh up | outputs | down`

`up`:
1. Sources `deploy/stack.env` if present (`LANE_ALERT_EMAIL`, `LANE_DOMAIN`, `LANE_ALLOWED_SENDERS`; never committed). If no alert email, asks "Email for alarms (you confirm it once)", checks it has `@` and `.`, writes `stack.env` with `umask 077`.
2. Finds the default VPC (fails if none).
3. Finds the zones offering `${LANE_INSTANCE_TYPE:-t4g.large}`, lists default-for-AZ subnets sorted by zone, picks the first subnet in an offering zone for the instance, and passes all default subnets for the DB subnet group.
4. `cloudformation deploy` with `CAPABILITY_IAM CAPABILITY_NAMED_IAM`, `--no-fail-on-empty-changeset`, parameters `VpcId`, `SubnetIds`, `InstanceSubnetId`, `InstanceType`, `AlertEmail`, `DomainName=${LANE_DOMAIN:-}` (RDS takes about 10 minutes the first time).
5. Prints the outputs.
6. If `LANE_DOMAIN` is set: `ses set-active-receipt-rule-set --rule-set-name lane-inbound` (CloudFormation cannot activate it).
7. `ec2 wait instance-status-ok`, then polls SSM `PingStatus` up to 40 times every 10 s until `Online`.
8. `on_host`: waits up to 5 minutes for `/opt/lane/.ready` and a working `docker compose version`.

`outputs`: prints every output as `Key=Value`.

`down`:
1. Asks to type the stack name to confirm deleting the stack, its database and every file in the bucket.
2. `ses set-active-receipt-rule-set` with no name (deactivates; an active rule set cannot be deleted).
3. `s3 rm s3://<bucket> --recursive`.
4. `cloudformation delete-stack`, waits for `stack-delete-complete` (about 10 minutes).
5. Removes `stack.env`.

#### `secrets.sh`

Reads `ANTHROPIC_API_KEY` from the repo's `.env`, reads the current `lane/app` secret, merges the key in Python with both values passed on stdin (never on a command line), `put-secret-value`, prints only that it stored it. The generated `LANE_SESSION_SECRET` is kept.

#### `gate.sh`

Asks twice for a password of at least 12 characters (from `/dev/tty`), makes a bcrypt hash with `htpasswd -nbBC 14` (cost 14), rewrites the `$2y$` prefix to `$2a$` (Caddy's form), and prints `LANE_GATE_USER=reviewer`, `LANE_GATE_HASH='<hash>'`, `LANE_GATE_TOKEN=<openssl rand -hex 32>`. Used as `deploy/gate.sh > deploy/gate.env`. Only the user and hash reach the instance (the token is not written by `deploy.sh` and nothing on AWS reads it).

#### `deploy.sh` (manual path, from the Mac)

1. `load_outputs`; requires `deploy/gate.env` and a running Docker.
2. Site = `Site` output, second = `SslipSite`. Sources `stack.env`; allowlist = `LANE_ALLOWED_SENDERS` plus `agent@<inbox domain>` when an inbox exists, turned into a JSON list.
3. Tag = `git rev-parse --short HEAD`, plus `-dirty` if the tree has changes. Image names `<ApiRepositoryUri>:<tag>`, `<WebRepositoryUri>:<tag>`.
4. Builds both images for `linux/arm64`: `backend/Dockerfile`; `frontend/Dockerfile` with build args `NEXT_PUBLIC_LANE_API_URL=https://<site>`, `NEXT_PUBLIC_LANE_COGNITO_DOMAIN=<CognitoDomain>`, `NEXT_PUBLIC_LANE_COGNITO_CLIENT_ID=<UserPoolClientId>`.
5. `ecr get-login-password | docker login`, pushes both images.
6. Reads the image lines currently in `/opt/lane/.env` over SSM, so the new settings keep the running images (remote-deploy then switches and can roll back).
7. Writes `.env` in a temp dir (the full list is in section 3) and `build.env` (`LANE_SITE`, `LANE_COGNITO_DOMAIN`, `LANE_COGNITO_CLIENT_ID`, for CI builds on the instance). Copies `docker-compose.yml`, `docker-compose.aws.yml`, `Caddyfile`, `remote-deploy.sh`, `ci-build.sh` beside them.
8. `aws s3 cp --recursive` to `s3://<bucket>/deploy/`.
9. `on_host`: in `/opt/lane`, copy `deploy/` down (excluding `cache/*` and `src-*`), delete `deploy/.env` from S3, `chmod 600 .env`, make the scripts executable, `docker login` to ECR, `docker pull` both images, `./remote-deploy.sh <api> <web>`, `docker image prune -f`.
10. Checks from the Mac: polls `https://<site>/` up to 30 times every 5 s for 200, then requires: page 200, `/api/v1/health` 200, `/api/v1/submissions` without sign-in 401, `/mail/` without its password 401, `https://<sslip>/api/v1/health` 200. Otherwise fails "The site is not answering as expected".

#### `remote-deploy.sh` (on the instance)

Usage: `/opt/lane/remote-deploy.sh <api image> <web image>`.
1. `dc` = `docker compose -f docker-compose.yml -f docker-compose.aws.yml`.
2. Records the previous `LANE_API_IMAGE` and `LANE_WEB_IMAGE` from `.env`, then writes the new ones into `.env` with `sed -i`.
3. `dc run --rm migrate` (alembic upgrade head); failure: roll back.
4. `dc up -d --remove-orphans`; failure: roll back.
5. `healthy`: up to 60 checks every 5 s (5 minutes) until both `api` and `web` report `healthy`; failure: roll back.
6. Reloads Caddy (`caddy reload --config /etc/caddy/Caddyfile`), or restarts it if reload fails, since the Caddyfile is a mounted file.
7. Prints service status.
8. Keeps the three newest local images of each of `lane-api`, `lane-web` and the ECR repository names; removes older ones.

Rollback: prints "ROLLING BACK", writes the previous images back to `.env`, `dc up -d --remove-orphans`, waits for health again (warns if the old images are not healthy either), exits 1. Migrations are not reversed.

#### `ci-build.sh` (on the instance, called by CI)

Usage: `ci-build.sh <sha> <s3 url of source tarball>`.
1. `AWS_DEFAULT_REGION` defaults to `us-east-1`; work dir `/opt/lane/build/<sha>` (recreated).
2. `aws s3 cp` the tarball, untar, delete it.
3. Sources `/opt/lane/build.env` (written by the last manual `deploy.sh`).
4. Builds natively on arm64: `lane-api:<sha7>` from `backend/Dockerfile`, `lane-web:<sha7>` from `frontend/Dockerfile` with the same three build args. No push to ECR.
5. Copies that commit's `docker-compose.yml`, `docker-compose.aws.yml`, `Caddyfile` into `/opt/lane`; installs that commit's `remote-deploy.sh` and `ci-build.sh` as `*.new` then `mv` over (never overwritten in place while running).
6. Removes the work dir and runs `/opt/lane/remote-deploy.sh lane-api:<sha7> lane-web:<sha7>`.

A build failure stops before step 5, so nothing changes on the instance. The `.env` is not rewritten by CI; settings change only through `deploy.sh`.

#### `users.sh`

Invitation-only user management against the pool from `UserPoolId`:
- `add <email> <role> ["Name"]`: `admin-create-user` (email verified, name, delivery by EMAIL; Cognito emails a temporary password) and `admin-add-user-to-group`.
- `role <email> <role>`: removes from all five groups, adds to one (takes effect on next sign-in).
- `list`: users per group with status.
- `remove <email>`: `admin-delete-user`.
Roles: `underwriter`, `senior_underwriter`, `viewer`, `ops`, `admin`.

#### `send-sample.sh`

- Default: over SSM, `docker compose exec -T worker python /app/scripts/demo_send.py mailpit sample` (the sample packet into the stack's Mailpit by SMTP inside Docker).
- `reply`: the agent's reply to the marked-sent request.
- `--ses [reply]`: requires `InboxAddress`; runs `demo_send.py ses <what> --inbox submissions@<domain>`, which calls `scripts/send_ses.py` (boto3 `sesv2`, region `us-east-1`) from `Daniel Okafor <agent@<domain>>` using the instance role (allowed only for that From address).
- `local [reply]`: the same against the local rehearsal compose.

#### `copy-cache.sh`

Copies cached model answers, the `llm_calls` rows they came from (with `run_id` cleared), `ocr_cache` and `layouts` from the local rehearsal database (default `postgresql://lane:lane@127.0.0.1:5434/lane`) to RDS: `\copy` to CSV locally, writes `load.sql` (temp tables, `insert ... on conflict do nothing`, one transaction), uploads to `s3://<bucket>/deploy/cache/`, then over SSM downloads, deletes the S3 copy, reads `LANE_DATABASE_URL` from `/opt/lane/.env` and runs `psql` in a throwaway `postgres:16-alpine` container. No submissions are copied.

Note: the URL read from `.env` holds the `{password}` placeholder, not the password, since `deploy.sh` writes it that way (see differences at the end).

### 3. The runtime on the instance

Directory `/opt/lane` holds `.env` (mode 600), `build.env`, `docker-compose.yml`, `docker-compose.aws.yml`, `Caddyfile`, `remote-deploy.sh`, `ci-build.sh`. Compose project name `lane` (from the directory); one default bridge network; containers reach each other by service name.

#### Services (`deploy/docker-compose.yml` with `deploy/docker-compose.aws.yml`)

| Service | Image | Command | Env | Ports | Health check | depends_on | Restart | Logs (AWS) |
|---|---|---|---|---|---|---|---|---|
| `caddy` | `caddy:2.10-alpine` | default | `env_file: .env` | host `80:80`, `443:443` (`LANE_HTTP_PORT`, `LANE_HTTPS_PORT`) | none | `web` healthy, `api` healthy | unless-stopped | `/lane/caddy` |
| `web` | `${LANE_WEB_IMAGE}` | `npx next start -p 3000 -H 0.0.0.0` (image CMD) | none at runtime (all baked at build) | 3000 internal | `node -e fetch('http://127.0.0.1:3000/')`, every 10 s, timeout 5 s, 12 retries | none | unless-stopped | `/lane/web` |
| `api` | `${LANE_API_IMAGE}` | `uvicorn lane.api.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips *` | `env_file: .env` | 8000 internal | Python `urllib` GET `http://127.0.0.1:8000/api/v1/health` must be 200, every 10 s, timeout 5 s, 12 retries | none | unless-stopped | `/lane/api` |
| `worker` | `${LANE_API_IMAGE}` | `python -m lane.worker` | `env_file: .env` | none | none | `api` healthy, `mailpit` started | unless-stopped | `/lane/worker` |
| `migrate` | `${LANE_API_IMAGE}` | `alembic upgrade head` | `env_file: .env` | none | none | none | `no`; profile `tools` (run only by `docker compose run --rm migrate`) | `/lane/api` |
| `mailpit` | `axllent/mailpit:v1.32.0` | default | `MP_DATABASE=/data/mailpit.db`, `MP_WEBROOT=mail`, `MP_MAX_MESSAGES=500` | SMTP 1025 and UI 8025 internal only | image's own | none | unless-stopped | `/lane/mailpit` |

Volumes: `caddy_data` (`/data`, certificates), `caddy_config` (`/config`), `mailpit_data` (`/data`). The Caddyfile is mounted read-only at `/etc/caddy/Caddyfile`.

Logging: the base file uses `json-file` (10 MB, 3 files). `docker-compose.aws.yml` overrides every service with the `awslogs` driver: `awslogs-region: us-east-1`, `mode: non-blocking`, `max-buffer-size: 4m`, and `awslogs-group: /lane/<service>` (`migrate` writes to `/lane/api`). The Docker daemon on the host sends them with the instance role (`logs:CreateLogStream`, `logs:PutLogEvents` on `/lane/*`); groups are not auto-created, so they must exist (the stack creates them).

Images: `backend/Dockerfile` (`python:3.13-slim-bookworm`, uv 0.13.1, runs as uid 10001 `lane`, includes `config/`, `data/`, `evals/`, `scripts/`, builds the sample `.eml`, bakes the RapidOCR models at build time). `frontend/Dockerfile` (`node:24-bookworm-slim`, two stages, `npm run build` with the build args, runs as `node`).

#### `deploy/Caddyfile` (condensed; comments removed, blocks joined)

```
{$LANE_SITE} {
	encode zstd gzip
	route {
		@api path /api/*
		handle @api {
			@basic header Authorization "Basic *"
			request_header @basic -Authorization
			reverse_proxy api:8000 { flush_interval -1 }
		}
		handle /mail* {
			basic_auth { {$LANE_GATE_USER} {$LANE_GATE_HASH} }
			reverse_proxy mailpit:8025
		}
		handle { reverse_proxy web:3000 }
	}
}
```

- Site address `{$LANE_SITE}` = `laneunder.online, 54-158-224-242.sslip.io`, so one block serves both names. Caddy's automatic HTTPS obtains a Let's Encrypt certificate per name (ACME challenges on ports 80 and 443), stores it in `caddy_data`, and redirects HTTP to HTTPS.
- Responses compressed with zstd or gzip.
- `/api/*` goes to `api:8000`; an `Authorization: Basic ...` header (a browser resending the `/mail` password) is removed first so the API reads its own bearer or `access_token`; `flush_interval -1` streams server-sent events unbuffered.
- `/mail*` requires Basic auth (user `reviewer`, bcrypt hash from `gate.sh`) and goes to Mailpit's UI on 8025 (web root `/mail`).
- Everything else goes to `web:3000`.

#### The settings file `/opt/lane/.env` (written by `deploy.sh`)

| Variable | Value |
|---|---|
| `LANE_ENV` | `production` |
| `LANE_SITE` | `<site>, <sslip>` (one name if they are the same) |
| `LANE_CORS_ORIGINS` | `["https://<site>", "https://<sslip>"]` |
| `LANE_GATE_USER`, `LANE_GATE_HASH` | from `gate.env` (hash only) |
| `LANE_HTTP_PORT`, `LANE_HTTPS_PORT` | `80`, `443` |
| `LANE_API_IMAGE`, `LANE_WEB_IMAGE` | the images running now, or the new ECR tags on first deploy; switched by `remote-deploy.sh` |
| `LANE_SECRETS_ID` | `AppSecretArn` (`lane/app`) |
| `LANE_DB_SECRET_ID` | `DatabaseSecretArn` (`lane/db`) |
| `LANE_SECRETS_REGION` | `us-east-1` |
| `LANE_DATABASE_URL` | `postgresql+psycopg://lane:{password}@<DatabaseHost>:5432/lane?sslmode=require` |
| `LANE_REQUIRE_SIGN_IN` | `true` |
| `LANE_DEMO_LOGIN` | `false` |
| `LANE_COGNITO_REGION`, `LANE_COGNITO_USER_POOL_ID`, `LANE_COGNITO_CLIENT_ID` | from outputs |
| `LANE_LOG_JSON` | `true` |
| `LANE_RUN_MODE` | `queue` |
| `LANE_MAILPIT_URL` | `http://mailpit:8025/mail` |
| `LANE_SES_INBOX_PREFIX` | `inbound/` (empty without a domain) |
| `LANE_INTAKE_ALLOWED_SENDERS` | JSON list: `LANE_ALLOWED_SENDERS` from `stack.env` plus `agent@<domain>` |
| `LANE_BLOB_BACKEND`, `LANE_S3_BUCKET`, `LANE_S3_REGION` | `s3`, `<bucket>`, `us-east-1` |
| `LANE_S3_ENDPOINT_URL`, `LANE_S3_ACCESS_KEY_ID`, `LANE_S3_SECRET_ACCESS_KEY` | empty (real S3, default credential chain: the instance role) |
| `LANE_ANALYZE_AUTO_BUDGET_USD` | `0` (the expert review always asks before spending) |

No secret is in this file. `LANE_S3_PREFIX` and `LANE_S3_SSE` are not set, so blobs go under `blobs/` and rely on the bucket's default SSE-S3.

#### Secrets read at start (`backend/src/lane/config.py`)

`get_settings()` (cached) returns `with_secrets(Settings())`. When `secrets_id` or `db_secret_id` is set, `with_secrets` calls Secrets Manager (`boto3` `secretsmanager` in `secrets_region`, credentials from the instance role through IMDSv2):
1. From `lane/app`: `ANTHROPIC_API_KEY` into `anthropic_api_key`, `LANE_SESSION_SECRET` into `session_secret` (each only if present).
2. From `lane/db`: `password`, URL-quoted, substituted for `{password}` in `database_url`; a URL without `{password}` is an error.

This runs once per process: the API at import (`create_app`), the worker in `main()`, and `migrate` through `backend/migrations/env.py`. At API start `Lifespan` calls `settings.check_secrets()`, which raises "Set LANE_SESSION_SECRET" if `env != "dev"` and the session secret is still the development default `dev-only-secret-change-me`.

### 4. App code that exists for AWS

#### SES inbox (`backend/src/lane/intake/ses.py`)

- `S3Mailbox` implements the same `Mailbox` protocol as Mailpit (`unread`, `raw`, `mark_read`) over `s3://<bucket>/<ses_inbox_prefix>`. The boto3 S3 client uses the instance role (empty keys and endpoint).
- `unread()`: `list_objects_v2` with prefix `inbound/`, max 100 keys. Skips anything with a `/` after the prefix (so `done/` and `held/` are never offered) and SES's `AMAZON_SES_SETUP_NOTIFICATION` object. For each message: `get_object`, then `held_reason`. A held message is logged `intake.held` (warning, with key and reason) and moved to `inbound/held/<key>`. Others are cached in memory and offered with their subject.
- `held_reason(raw, allowlist)`, checks in order:
  1. `X-SES-Virus-Verdict` is not `PASS`: "the virus scan did not pass".
  2. `X-SES-Spam-Verdict` is not `PASS`: "the spam scan did not pass".
  3. No From address: "no sender address".
  4. From address not on the allowlist (exact address, or an `@domain` entry): "... is not on the allowlist".
  5. Not authenticated: only `Authentication-Results` headers that start with `amazonses.com` count; passes on `dmarc=pass`, or `dkim=pass` whose `header.d` or `header.i` domain equals the From domain or is its parent.
- `mark_read(ids)`: moves each key to `inbound/done/<key>` (copy then delete).
- A move is `copy_object` then `delete_object`. Any botocore error is raised as `OSError`, which the watcher logs as `mailbox.unreachable` and retries.
- `start_in_background` runs `mailpit.watch(settings, process, mailbox=S3Mailbox)` on a daemon thread. Polling interval is `mailpit_poll_s` (5 s default). Each poll: `poll_once` ingests every offered message with `ingest_email` (source `smtp`, intake limits), stores the raw bytes and attachments in the blob store, creates the submission, enqueues a `process` job, then marks the messages read (rejected ones too, so they are not retried forever).
- Wiring (`backend/src/lane/worker.py`): only the worker watches. With `LANE_RUN_MODE=queue` it starts the Mailpit watcher (`LANE_MAILPIT_URL` set) and the SES watcher (`LANE_SES_INBOX_PREFIX` set); the API in queue mode watches neither. The worker then claims jobs from Postgres under a lease (30 s, renewed every 10 s, up to 3 attempts), writes a heartbeat every 5 s, and publishes progress over Postgres `NOTIFY` (channel `lane_events`), which the API listens to for its live view.

#### Cognito exchange (`backend/src/lane/api/auth.py`)

- `GET /api/v1/auth/config` returns `{demo, cognito}`: on AWS `demo=false`, `cognito=true`.
- `POST /api/v1/auth/cognito` with `{id_token}`:
  1. 404 if no pool is configured.
  2. `cognito_claims`: issuer `https://cognito-idp.<region>.amazonaws.com/<pool id>`; signing key from `<issuer>/.well-known/jwks.json` through a cached `PyJWKClient` (keys cached, lifespan 3600 s, refetched for an unknown key id); `jwt.decode` with `RS256`, audience = client id, issuer checked, `exp iat iss aud sub` required; `token_use` must be `id`. Any failure: 401.
  3. Role from `cognito:groups` by order `senior_underwriter`, `underwriter`, `admin`, `ops`, `viewer` (first match wins); no group: 403 "This account has no role in Lane yet".
  4. Name = `name`, else `email`, else `sub`.
  5. Returns Lane's own session: an HMAC-SHA256 signed token (`base64(claims).hexsig`, signed with `LANE_SESSION_SECRET`, expiry `session_ttl_hours` = 12 h) with role, permissions and decision authority.
- Demo sign-in (`/auth/people`, `/auth/demo-login`) returns 404 when `LANE_DEMO_LOGIN=false`.
- `get_viewer` reads `Authorization: Bearer <token>` or the `access_token` query parameter (for `img` and `EventSource`).

#### Require sign-in middleware (`backend/src/lane/api/main.py`)

`require_sign_in`: when `LANE_REQUIRE_SIGN_IN=true`, every request under `/api/v1/` except `OPTIONS`, `/api/v1/health` and `/api/v1/auth/*` needs a valid Lane token (bearer or `access_token`); otherwise 401 "Sign in to use Lane". Middleware order, outermost first: GZip (min 1024 bytes), `require_sign_in`, `scrub_personal_data` (masks SSNs and stored driver identifiers in JSON responses of submission paths; withholds with 503 if the identifiers cannot be read), CORS (origins from `LANE_CORS_ORIGINS`). `/api/v1/health` runs `SELECT 1` and returns 200 with `status: ok` or `degraded`, `database`, and `llm: configured | missing_key`.

#### Frontend sign-in (`frontend/src/lib/cognito.ts`, `frontend/src/app/auth/callback/page.tsx`)

- `cognitoEnabled()` is true when `NEXT_PUBLIC_LANE_COGNITO_DOMAIN` and `NEXT_PUBLIC_LANE_COGNITO_CLIENT_ID` were set at build time (`frontend/Dockerfile` build args, default empty; `NEXT_PUBLIC_LANE_API_URL` is required).
- `startSignIn()`: random verifier (48 bytes) and state (16 bytes), stored with the return path in `sessionStorage` key `lane.signin`; S256 challenge via `crypto.subtle`; redirects to `<CognitoDomain>/oauth2/authorize` with `response_type=code`, `client_id`, `redirect_uri=<origin>/auth/callback`, `scope=openid email profile`, `state`, `code_challenge`, `code_challenge_method=S256`.
- `/auth/callback` renders `SignInReturn` (`frontend/src/components/shell/sign-in-return.tsx`), which calls `finishSignIn(code, state)`: checks state, POSTs `<CognitoDomain>/oauth2/token` (`grant_type=authorization_code`, `client_id`, `code`, `redirect_uri`, `code_verifier`), takes `id_token`, then `session.signInWithCognito` POSTs it to `/api/v1/auth/cognito` and keeps Lane's token in `localStorage`.
- `signOutUrl()`: `<CognitoDomain>/logout?client_id=...&logout_uri=<origin>/`.

#### S3 blob storage (`backend/src/lane/storage/blobs.py`, `records.py`)

`blob_store(settings)` returns `S3BlobStore` when `LANE_BLOB_BACKEND=s3`: boto3 S3 client with the default credential chain. Keys are content addresses `<prefix><sha[0:2]>/<sha[2:4]>/<sha256>` with prefix `blobs/`. `put` checks `head_object` first (identical bytes are never rewritten), then `put_object` with `ChecksumSHA256` (and `ServerSideEncryption` only when `LANE_S3_SSE` is set, which it is not on AWS). `ensure_blob` writes bytes and the `Blob` row (sha256, size, kind, mime, storage key) once.

#### Logs, PII masking, cost lines (`backend/src/lane/log.py`, `backend/src/lane/llm/gateway.py`)

- `configure_logging(json=LANE_LOG_JSON, pii=pii_attributes)`, used by API and worker: structlog with context vars, `level`, the PII mask, ISO UTC timestamp, then a JSON renderer on AWS.
- `mask_pii`: any log key equal to, or ending in `_` plus, `license_number`, `date_of_birth`, `home_address` is replaced with `[masked]`.
- The model gateway logs one `llm.call` line per call with `purpose`, `model`, `cache_hit`, `status`, `cost_usd`, `latency_ms`. The `ModelSpendUSD` metric filters sum `$.cost_usd`; the dashboard's table groups by `purpose` and `cache_hit`. `step.failed` and `job.failed` (worker `run_job`) feed `RunFailures`.

### 5. CI/CD

#### `.github/workflows/ci.yml`

Triggers on push to `main` and on pull requests (concurrency per ref, cancel in progress). Three jobs on `ubuntu-latest`:
- `backend`: uv sync, ruff check, ruff format check, pyright, pytest with coverage.
- `frontend`: Node 24, `npm ci`, regenerate API types from `openapi.json` and fail on diff, lint, format check, typecheck, test, build.
- `guard`: `scripts/no_hardcode_guard.sh`.

#### `.github/workflows/deploy.yml`

- Trigger: `workflow_run` of `ci`, type `completed`, branch `main`; and `workflow_dispatch`. Job runs only if dispatched or `ci` concluded `success`.
- Concurrency group `deploy`, no cancel (deploys queue).
- Permissions: `id-token: write`, `contents: read`.
- Env: `AWS_REGION` and `AWS_DEFAULT_REGION` `us-east-1`, `ROLE_ARN` `arn:aws:iam::367959205637:role/lane-github-deploy`, `STACK` `lane`.
- Job `deploy`, `ubuntu-latest`, timeout 40 minutes:
  1. Checkout at `workflow_run.head_sha` (or `github.sha`).
  2. Sign in: fetch GitHub's OIDC token with audience `sts.amazonaws.com` from `ACTIONS_ID_TOKEN_REQUEST_URL`; `aws sts assume-role-with-web-identity` (session `deploy-<run id>`, 3600 s); mask the secret key and session token; export the three credentials to `GITHUB_ENV`. No AWS keys are stored in GitHub.
  3. Upload: read `BucketName`, `InstanceId`, `Site` from `describe-stacks`; `git archive --format=tar.gz HEAD` to `src.tgz`; `aws s3 cp` to `s3://<bucket>/deploy/src-<sha>.tgz`.
  4. Build and start: `ssm send-command` `AWS-RunShellScript` on the instance, timeout 2400 s, comment `deploy <sha>`, command `/opt/lane/ci-build.sh <sha> s3://<bucket>/deploy/src-<sha>.tgz`; polls `get-command-invocation` every 10 s; prints stdout and stderr; step fails unless `Success`.
  5. Check: polls `https://<site>/api/v1/health` up to 20 times every 5 s; requires health 200 and `/api/v1/submissions` 401.
- Failure handling: a failed build leaves the running stack untouched; a failed migration, start or health check is rolled back on the instance by `remote-deploy.sh` and the SSM status becomes Failed, failing the job; a site check failure fails the job. The tarball expires with the `deploy/` lifecycle rule after one day.

### 6. Security posture as built

- Open ports: TCP 80 and 443 from anywhere on the instance; nothing else. No SSH rule, no key pair on the instance. Administration is SSM only (Run Command from scripts and CI, Session Manager for a shell).
- Database: not publicly accessible; its security group admits 5432 only from the web security group; `rds.force_ssl=1`; the app connects with `sslmode=require` (encrypted; the server certificate is not verified at that mode).
- IMDSv2 required; hop limit 2 so containers (one network hop away) can get role credentials.
- Encryption at rest: EBS root volume encrypted; RDS storage encrypted; S3 default SSE-S3; Secrets Manager (default key). In transit: HTTPS with Let's Encrypt certificates at Caddy for both names; TLS to RDS; HTTPS to AWS APIs, Cognito and Anthropic. Inside the instance, Caddy to containers is plain HTTP on the Docker network.
- S3: all public access blocked, ACLs disabled; the only bucket policy lets SES (this account, this receipt rule) put to `inbound/*`. No deny-non-TLS statement.
- Secrets: generated by Secrets Manager; read at process start with the instance role; never in `.env` or GitHub. `secrets.sh` keeps values off command lines.
- Email allowlist: `LANE_INTAKE_ALLOWED_SENDERS` (built from `LANE_ALLOWED_SENDERS` in `deploy/stack.env`, which holds two personal addresses, plus `agent@laneunder.online`); plus virus, spam and DMARC or DKIM checks before any model spend. Anything else is held in `inbound/held/`.
- Sign-in: Cognito invitation only (no self sign-up), optional TOTP MFA; API refuses anonymous requests (`LANE_REQUIRE_SIGN_IN=true`) except health and auth; demo login off (`LANE_DEMO_LOGIN=false`); roles from Cognito groups.
- Production refuses the dev secret (`check_secrets` with `LANE_ENV=production`).
- `/mail` (Mailpit UI) behind Caddy Basic auth with a bcrypt (cost 14) hash; Mailpit SMTP is not published outside Docker.
- IAM: SES sending limited to From `agent@<domain>`; log writes limited to `/lane/*`; the deploy role is limited to main of one repo by immutable subject, and to S3 `deploy/*`, SendCommand on this instance with `AWS-RunShellScript`, and DescribeStacks on this stack.
- Containers run as non-root users (`lane` uid 10001, `node`).
- PII masked in logs (`license_number`, `date_of_birth`, `home_address`) and in API responses.
- ECR scan on push.

### 7. Flows

#### Flow 1: real email in

1. The agent's mail server looks up the MX of `laneunder.online` in Route 53: `10 inbound-smtp.us-east-1.amazonaws.com`.
2. It delivers by SMTP (TLS optional, `TlsPolicy: Optional`) to SES inbound in `us-east-1`.
3. SES matches active rule set `lane-inbound`, rule `lane-submissions` (recipient `submissions@laneunder.online`), runs the spam and virus scan, adds `X-SES-Spam-Verdict`, `X-SES-Virus-Verdict` and an `Authentication-Results: amazonses.com; ... dkim=... dmarc=...` header.
4. SES writes the raw message to `s3://lane-<acct>-us-east-1/inbound/<message id>` (allowed by `InboxPolicy` for that rule ARN; encrypted SSE-S3).
5. Within 5 s, the worker container's SES watcher thread (`S3Mailbox`) lists `inbound/` over HTTPS with the instance role (IMDSv2, hop 2) and downloads the new object.
6. `held_reason` checks virus, spam, sender, allowlist, authentication. If held: log `intake.held` to `/lane/worker`, move to `inbound/held/`, stop.
7. Otherwise `ingest_email` stores the raw email and each attachment as blobs under `s3://.../blobs/` (`put_object`), writes the submission and blob rows to RDS (psycopg, TCP 5432, TLS), enqueues a `process` job; the message moves to `inbound/done/`.
8. The worker's main loop claims the job from RDS under a lease and runs the pipeline: reads blobs from S3, runs OCR locally, calls the Anthropic API over HTTPS (each call logs `llm.call` with `cost_usd`, through `awslogs` to `/lane/worker`, summed by `ModelSpendUSD`), writes extractions, rules results and the Brief to RDS.
9. Progress events go from the worker to the API over Postgres `NOTIFY` on `lane_events`; the API streams them to an open browser as server-sent events through Caddy (`flush_interval -1`).
10. The underwriter opens the submission and reads the Brief (Flow 2).

For the demo the same path is triggered by `deploy/send-sample.sh --ses`: SSM, `docker compose exec worker`, `sesv2` SendEmail from `agent@laneunder.online` (signed with the domain's Easy DKIM, so DMARC passes) to the inbox, then steps 1 to 10.

#### Flow 2: an underwriter loads the site

1. The browser resolves `laneunder.online` (registrar Porkbun delegates to the four Route 53 name servers); the `A` record returns the Elastic IP (TTL 300).
2. TCP 443 to the instance, allowed by `WebSecurityGroup` (port 80 is redirected to HTTPS by Caddy).
3. Caddy terminates TLS with its Let's Encrypt certificate for that name (the sslip name has its own).
4. Caddy routes on the path: `/api/*` to `api:8000` (Basic header stripped, streaming unbuffered); `/mail*` to `mailpit:8025` after Basic auth; anything else to `web:3000`. Responses are compressed with zstd or gzip.
5. The Next.js page loads; its JavaScript calls `https://laneunder.online/api/v1/...` (same origin, baked in as `NEXT_PUBLIC_LANE_API_URL`) with `Authorization: Bearer <Lane token>`; images and the event stream pass `access_token` in the URL.
6. The API's `require_sign_in` refuses calls without a valid token (401); with one, it reads RDS (TLS) and S3 blobs and returns JSON with personal data masked.

#### Flow 3: sign-in with Cognito (PKCE) to Lane's session

1. The browser has no Lane token; the sign-in page asks `GET /api/v1/auth/config` (open route): `cognito=true`, `demo=false`.
2. `startSignIn()` makes the PKCE verifier, S256 challenge and state, saves them in `sessionStorage`, and redirects to `https://lane-<acct>.auth.us-east-1.amazoncognito.com/oauth2/authorize?response_type=code&...`.
3. The person signs in on Cognito's managed login (email and password; first time, the temporary password from the invite, then a new one; TOTP if they enrolled).
4. Cognito redirects to `https://laneunder.online/auth/callback?code=...&state=...` (a registered callback URL).
5. `SignInReturn` checks state and POSTs to Cognito `/oauth2/token` with the code and `code_verifier` (no client secret); Cognito returns the ID token (60 minutes).
6. The browser POSTs `{id_token}` to `https://laneunder.online/api/v1/auth/cognito` (through Caddy to the API).
7. The API fetches and caches the pool's JWKS from `cognito-idp.us-east-1.amazonaws.com`, verifies RS256 signature, issuer, audience (client id), expiry and `token_use=id`, maps `cognito:groups` to a role (403 if none).
8. The API returns Lane's HMAC-signed session token (12 hours) with role, permissions and decision authority; the browser keeps it in `localStorage` and returns to the page it started from.
9. Sign out clears it and sends the browser to Cognito `/logout`, which returns to `https://laneunder.online/`.

#### Flow 4: deploy by push

1. A commit lands on `main` on GitHub; `ci` runs backend, frontend and guard jobs.
2. On `ci` success, `deploy` starts (one at a time, group `deploy`).
3. The runner requests a GitHub OIDC token (audience `sts.amazonaws.com`) and calls STS `AssumeRoleWithWebIdentity` for `lane-github-deploy`; IAM checks `aud` and `sub = repo:vamsijilla@118758121/lane-aws@1443616880:ref:refs/heads/main`; STS returns 1-hour credentials.
4. The runner reads stack outputs (`cloudformation:DescribeStacks`), makes `git archive` of the commit and puts it at `s3://<bucket>/deploy/src-<sha>.tgz`.
5. The runner calls `ssm:SendCommand` (`AWS-RunShellScript`, instance `i-039449781559b8941`, 2400 s): `/opt/lane/ci-build.sh <sha> <s3 url>`.
6. The SSM agent on the instance runs it as root: downloads the tarball from S3 with the instance role, builds `lane-api:<sha7>` and `lane-web:<sha7>` natively on arm64 (web build args from `/opt/lane/build.env`), copies the commit's compose files, Caddyfile and scripts into `/opt/lane`.
7. `remote-deploy.sh` writes the new tags into `.env`, runs `migrate` against RDS, `docker compose up -d`, waits up to 5 minutes for `api` and `web` health, reloads Caddy, prunes to three images per repository.
8. On a migration, start or health failure it restores the previous tags, starts them, waits for health and exits 1.
9. The runner polls `ssm:GetCommandInvocation` every 10 s, prints the output, and fails the job on any status other than `Success`.
10. The runner checks `https://<site>/api/v1/health` is 200 (up to 100 s) and `/api/v1/submissions` is 401.

#### Flow 5: the manual deploy

1. On the Mac: `deploy/deploy.sh` reads outputs with the `lane` AWS profile, `gate.env` and `stack.env`.
2. Builds both images for `linux/arm64` with the site and Cognito build args; tags with the short commit (plus `-dirty`).
3. Logs in to ECR and pushes `lane-api:<tag>` and `lane-web:<tag>` (scan on push runs).
4. Reads the running image tags from `/opt/lane/.env` over SSM.
5. Writes `.env` and `build.env`, uploads them with the compose files, Caddyfile and scripts to `s3://<bucket>/deploy/`.
6. Over SSM on the instance: download to `/opt/lane`, delete `deploy/.env` from S3, `chmod 600 .env`, ECR login, pull both images, `remote-deploy.sh <api> <web>` (migrate, start, health, rollback as in Flow 4), `docker image prune -f`.
7. From the Mac over HTTPS: page 200, health 200, API without sign-in 401, `/mail/` without password 401, sslip health 200; else the script fails.

#### Flow 6: an alarm fires to email

1. A signal crosses a threshold:
   - Route 53 health checkers call `https://laneunder.online/api/v1/health` every 30 s; 3 failures mark it unhealthy; `HealthCheckStatus` Minimum below 1 for 3 one-minute periods trips `lane-site-down`.
   - EC2 status checks: `StatusCheckFailed_System` for 2 minutes trips `lane-host-recover`, which also calls the EC2 recover action (the instance moves to new hardware, keeping its id, EIP and volume); `StatusCheckFailed_Instance` for 3 minutes trips `lane-host-reboot`, which reboots it (containers restart, `unless-stopped`).
   - Containers log through `awslogs` to `/lane/<service>`; metric filters turn `step.failed` or `job.failed` into `Lane/RunFailures` and `llm.call` into `Lane/ModelSpendUSD`; one failure in 5 minutes trips `lane-run-failed`; over $5 in a day trips `lane-model-spend`.
   - RDS metrics: free storage below 2 GB (`lane-db-storage`), CPU above 80% for 15 minutes (`lane-db-cpu`).
2. CloudWatch publishes the alarm state change to SNS topic `lane-alerts` (`lane-site-down` also on return to OK).
3. SNS emails the confirmed subscriber.
4. The operator opens the `lane` dashboard (`DashboardUrl`) and the log groups, and gets a shell with `aws ssm start-session --target <InstanceId>` if needed.

#### Flow 7: stack up and stack down

Up (first deploy, from `deploy/README.md`):
1. `deploy/stack.sh up`: asks for the alert email once, finds the default VPC and subnets, `cloudformation deploy` creates the 55 resources (about 15 minutes, RDS the longest), activates `lane-inbound`, waits for instance status OK, SSM Online, and `/opt/lane/.ready` plus Docker Compose.
2. Confirm the SNS subscription email.
3. Paste `NameServers` into the registrar (once, for the domain).
4. `deploy/secrets.sh`: Anthropic key into `lane/app`.
5. `deploy/gate.sh > deploy/gate.env`: the `/mail` password hash.
6. `deploy/deploy.sh`: build, push, settings, migrate, start, check (Caddy gets certificates on first request).
7. `deploy/copy-cache.sh`: the rehearsal's model and OCR cache into RDS so the sample replays for $0.
8. `deploy/users.sh add <email> <role> "<name>"`: invite people.
9. `deploy/send-sample.sh` (or `--ses`): the agent's email arrives and the Brief fills in.

Down:
1. `deploy/stack.sh down`: type the stack name.
2. Deactivate the active SES receipt rule set.
3. Empty the bucket (`s3 rm --recursive`).
4. `cloudformation delete-stack` and wait: instance, EIP, RDS (no final snapshot, `DeletionPolicy: Delete`), ECR repos (`EmptyOnDelete`), secrets, user pool, roles, OIDC provider, zone and records, SES identity and rules, log groups, alarms, topic, dashboard.
5. Remove `deploy/stack.env`.

### 8. Cost per month

From `deploy/README.md`: about $68 a month. EC2 about $49; RDS about $12; the Elastic IP and storage; two secrets ($0.80); the health check and alarms (about $1.30); logs (cents). Cognito is free at this size. Model calls are extra and only for new input; a replay of the sample is $0.

### Notes, uncertainties and differences between code and deployment

- The deployed template matches `deploy/cloudformation.yml` exactly, and resource count (55) matches. Parameters as deployed: `DomainName=laneunder.online`, `InstanceType=t4g.large`, six subnets in the DB subnet group.
- ECR `lane-api` holds 14 images though its lifecycle rule keeps 10; lifecycle expiry runs asynchronously, so this may be a lag rather than a fault. CI deploys do not push to ECR, so ECR only holds manual-deploy images; the instance runs locally built `lane-api:<sha7>` tags after a CI deploy.
- `deploy/copy-cache.sh` reads `LANE_DATABASE_URL` from `/opt/lane/.env`, which holds the `{password}` placeholder, not the password (secrets are fetched at process start). As written, its `psql` call would not authenticate unless the URL is filled some other way. Not tested here.
- `LANE_GATE_TOKEN` from `gate.sh` is not written to the instance and nothing on AWS uses it; `deploy/env.aws.example` still lists it.
- `ci-build.sh` copies the new commit's compose files and Caddyfile before `remote-deploy.sh` runs; a rollback restarts the previous images with the new commit's compose files.
- `/api/v1/health` returns 200 even when the database is unavailable (body `status: degraded`), so `lane-site-down` does not fire on a database outage alone.
- `sslmode=require` encrypts the RDS connection but does not verify the server certificate.
- The bucket has no lifecycle rule for `blobs/` and no versioning; the `inbound/` 30-day rule also expires `inbound/done/` and `inbound/held/`.
- `deploy/README.md` diagrams Mailpit as `/mail/*`; the Caddyfile matcher is `/mail*`.
- The deployed `/opt/lane/.env` was not read (that would need an SSM command); its contents above are from `deploy.sh`.

## Production (design)

This part is a design, not running code. It describes how Lane would run for Knight, carrying over
everything that is built (local and AWS today) and naming each thing that would be new. Every element
below is marked **built** (exists in this repository today), **config** (the same code, configured
differently), or **new** (would have to be built or provisioned). The diagram is
`docs/architecture/png/3-production.png`; its source is `production()` in
`docs/architecture/final.py`.

### What does not change

The application is the same images. The production design changes where they run and what
surrounds them, not what they do:

- The seven pipeline steps, the rules engine, the verification gate, the evidence model, the audit
  chain and the masking are unchanged (**built**).
- The web, API and worker containers are the same Dockerfiles (**built**), built for arm64.
- Configuration stays in environment variables and Secrets Manager (**built**); the policy stays
  as data in `config/`, `backend/rules/` and `backend/prompts/` (**built**).
- The job queue stays in Postgres (`jobs` table, leases, `SKIP LOCKED`) (**built**); SQS is an
  option only when stages are split into separate services (**new**, optional).

### Layers, outside in

| Layer | Components | Status |
|---|---|---|
| People and outside systems | Agents (any mailbox), underwriters (browser), Knight's identity provider (Okta or Entra ID), GitHub Actions, FMCSA and NHTSA public APIs | identity provider **new**; others as today |
| Organization | AWS Organizations: accounts for dev, staging, prod, log archive, security; IAM Identity Center for staff access | **new** |
| Edge | Route 53 (DNS, health checks, failover records), CloudFront, AWS WAF (managed rules, rate limits), Shield Standard, ACM certificates, SES inbound | Route 53 and SES **built**; the rest **new** |
| Identity | Cognito user pool, federated to Okta or Entra ID over SAML or OIDC; groups become Lane roles | Cognito and the role mapping **built**; federation **new** (**config** in Cognito) |
| Network | One VPC (example `10.0.0.0/16`) across two Availability Zones; per AZ a public subnet, a private app subnet and a private data subnet; NAT gateway per AZ with an egress allowlist; VPC endpoints | **new** (today: the default VPC, one public subnet) |
| Load balancing | Application Load Balancer in the public subnets, TLS from ACM, path routing: `/api` to the API service, everything else to web | **new** (today: Caddy does this routing) |
| Compute | ECS cluster on Fargate (arm64): `web` service (2 to 6 tasks), `api` service (2 to 10 tasks), `worker` service (0 to 20 tasks, scaled on queue depth), `migrate` one-off task per release | **new** (today: one EC2 with Docker Compose) |
| Model | Claude (Haiku, Sonnet, Opus) on Amazon Bedrock through a PrivateLink endpoint | **new** (today: the Anthropic API over the internet); the gateway in `lane/llm/` is the one place to change |
| Data | RDS PostgreSQL 16 Multi-AZ (primary and synchronous standby), RDS Proxy (pooling, IAM auth), a read replica for evals and reports; S3 for documents by sha256 with KMS, versioning and replication to a second region | RDS and S3 **built**; Multi-AZ, proxy, replica, versioning, replication **new** |
| Keys and secrets | KMS (a key per data class), Secrets Manager with rotation | Secrets Manager **built**; customer keys and rotation **new** |
| Observability | CloudWatch logs, metrics, alarms, SLOs and dashboard; X-Ray traces from OpenTelemetry; SNS to on-call (PagerDuty) | logs, alarms, SNS, dashboard **built**; tracing, SLOs, paging **new** |
| Security and recovery | CloudTrail (all accounts), GuardDuty, Security Hub, Config, Inspector (image CVEs), Macie (personal data in S3), AWS Backup (vault lock, cross-region copies), run from the security account | **new** |
| Delivery | GitHub Actions with a role per account (OIDC), ECR with immutable tags, blue/green deploys with CodeDeploy, rollback on alarms, migrations first | OIDC, migrate-first, health check and rollback **built**; per-account roles, CodeDeploy blue/green **new** |

### Network, subnet by subnet

Boundaries, outermost first:

- VPC `10.0.0.0/16` (example), spanning two Availability Zones, a and b.
- In each Availability Zone, three subnets:
  - Public subnet: one node of the Application Load Balancer, one NAT gateway.
  - Private app subnet: the ECS Fargate tasks for web, api and worker.
  - Private data subnet: in zone a, the RDS primary and RDS Proxy; in zone b, the RDS standby and
    the read replica.
- VPC endpoints, shared by both zones. Interface endpoints: ECR (api and dkr), Secrets Manager,
  KMS, CloudWatch Logs, X-Ray, Systems Manager, Bedrock runtime. Gateway endpoint: S3.

Security groups (design):

| Group | Inbound | Outbound |
|---|---|---|
| ALB | 443 from CloudFront's managed prefix list only | web and api task ports |
| web tasks | 3000 from the ALB group | api through the ALB, VPC endpoints |
| api tasks | 8000 from the ALB group | RDS Proxy 5432, VPC endpoints |
| worker tasks | none | RDS Proxy 5432, VPC endpoints, NAT for the FMCSA and NHTSA hosts only |
| RDS Proxy | 5432 from api and worker groups | RDS 5432 |
| RDS | 5432 from the proxy group | none |

Egress: only the worker reaches the internet, through NAT, to an allowlist of the FMCSA and NHTSA
hosts (enforced by AWS Network Firewall or a proxy with a domain allowlist, **new**). Document data
never leaves through NAT: S3, Bedrock, KMS and the logs are reached through endpoints.

### Compute

| Service | Image | Size and scaling | Notes |
|---|---|---|---|
| `web` | `frontend/Dockerfile` (**built**) | 2 to 6 tasks, CPU target | Next.js `next start`; the API address is baked in at build, so one image per environment (as today) |
| `api` | `backend/Dockerfile` (**built**) | 2 to 10 tasks, CPU and request count | FastAPI; require sign-in, privacy scrub and SSE as today; connects through RDS Proxy |
| `worker` | `backend/Dockerfile` (**built**) | 0 to 20 tasks on queue depth (a CloudWatch metric of queued jobs) | runs the pipeline; RapidOCR on Graviton; the S3 mail watcher as today |
| `migrate` | `backend/Dockerfile` | one-off task before each release | `alembic upgrade head`, as the `migrate` service does today |

The task role replaces the instance role: the same S3, Secrets Manager and logs permissions as the
instance role in `deploy/cloudformation.yml`, scoped per service, plus `bedrock:InvokeModel` for
the worker and api.

### Verification beyond the documents

| Check | Status | Where | What it does |
|---|---|---|---|
| VIN | **built** | `backend/src/lane/domain/vin.py` (`check`, `model_year`), used from `lane/extract/validators.py` | 17 characters, allowed characters only, the check digit (position 9, weights and transliteration), model year from position 10, maker from the first three characters |
| IFTA arithmetic | **built** | `backend/src/lane/extract/checks.py` (`ifta_checks`) | each column of the rows adds up to its printed total; tax equals gallons times rate on every row; subtotal plus penalty plus interest equals total due; plus the recall check that every jurisdiction line on the page has a row (`lane/extract/validators.py`) |
| FMCSA by USDOT | **new** | would be a worker step calling the FMCSA QCMobile API through NAT | carrier census record (name, address, power units, drivers) compared against the application; authority and insurance on file. Today rule `ELIG-06` (SAFER record) is marked `external: true` and waits on this lookup at quote (`backend/rules/knight_trucking.v1.yaml`) |
| NHTSA vPIC by VIN | **new** | the same pattern, after the offline VIN check | maker, model and year from NHTSA's decoder, compared with the vehicle schedule |

Each lookup would land on the evidence board as one more source with its own evidence record
(source, request, response hash, time), so conflicts with the documents surface the same way
document conflicts do. No lookup decides on its own; the rules read the resolved facts as today.

### Data protection and recovery

- **Encryption.** TLS everywhere (CloudFront, ALB, RDS SSL forced as today). At rest: KMS keys per
  data class (documents, database, logs, backups).
- **Database.** Multi-AZ failover (synchronous standby), point-in-time recovery, automated backups
  copied to a second region by AWS Backup.
- **Documents.** S3 versioning, replication to a second region, Object Lock in the backup vault,
  Macie scanning for personal data.
- **Personal data.** Masking in the UI, the API and the logs stays as built (`lane/api/`, log
  masking in the worker and API).
- **Recovery drill.** A scheduled restore of the database and a sample of documents into the
  staging account.

### Delivery

The pipeline, in order:

1. A push to `main` starts `ci`: lint, types, tests, the hardcode guard and the evals.
2. Arm64 images are built and pushed to ECR with immutable tags; Inspector scans them.
3. Dev deploys automatically.
4. Staging deploys, then runs smoke tests and the eval gate.
5. A person approves the release.
6. Prod: the `migrate` task runs first, then CodeDeploy shifts web and api blue/green, watches the
   alarms, and rolls back if one fires.

Built today and carried over: OIDC with no stored keys (`.github/workflows/deploy.yml`), migrations
first, health checks and automatic rollback (`deploy/remote-deploy.sh`).

### Production flows (numbered as on the diagram)

1. **SMTP.** The agent emails the submissions address; the MX points at SES.
2. **To S3.** SES runs its spam and virus scans and writes the message to S3, encrypted by KMS.
3. **Ingest.** A worker task reads it through the S3 gateway endpoint and applies the same checks
   as today (verdicts, DMARC or DKIM, allowlist; `lane/intake/ses.py`), then ingests.
4. **SQL.** Jobs, facts and rule outcomes go to RDS through RDS Proxy.
5. **Claude.** Reading and review call Claude on Bedrock through the PrivateLink endpoint; results
   are cached by model, prompt hash and input hash as today.
6. **HTTPS.** Underwriters reach CloudFront; WAF filters.
7. **Routing.** The ALB sends `/api` to the API service and everything else to web.
8. **Sign in.** The browser signs in through Cognito (PKCE, as built).
9. **Federation.** Cognito federates to Knight's Okta or Entra ID over SAML or OIDC; the group
   claim becomes the Lane role.
10. **Verification.** The worker calls FMCSA and NHTSA through NAT, allowlisted hosts only.

### From today to production, step by step

| Today (Part 3) | Production | Change |
|---|---|---|
| One EC2 with Docker Compose | ECS Fargate services in two AZs | task definitions, services, autoscaling |
| Caddy on the instance, Let's Encrypt | CloudFront, WAF, ALB, ACM | routing rules move from `deploy/Caddyfile` to ALB listener rules |
| Default VPC, public subnet | Dedicated VPC, private subnets, endpoints, NAT allowlist | network stack |
| RDS single AZ, `db.t4g.micro` | Multi-AZ, RDS Proxy, read replica, larger class | database settings |
| Anthropic API over the internet | Bedrock over PrivateLink | the gateway's client in `lane/llm/` |
| Cognito, invitation only | Cognito federated to Okta or Entra ID | identity provider configuration |
| SSM Run Command deploys | CodeDeploy blue/green from per-account roles | pipeline |
| 7 alarms to email | SLOs, tracing, paging | observability |
| One account | Organizations with five accounts and central security services | landing zone |
