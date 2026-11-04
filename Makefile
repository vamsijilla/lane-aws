.DEFAULT_GOAL := help
SHELL := /bin/bash

BACKEND := backend
FRONTEND := frontend

.PHONY: help setup migrate demo worker eval-db layout-speed minio blobs-to-s3 api-types screens e2e mailpit mailpit-watch send template templates-check dev api web sample ingest reset process pages documents resolution rules analyze actions eval perturb escalate run lint format typecheck test check check-pg test-pg guard build clean

help: ## Show targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  \033[1m%-10s\033[0m %s\n",$$1,$$2}'

setup: ## Install backend and frontend dependencies
	cd $(BACKEND) && uv sync
	cd $(FRONTEND) && npm ci

migrate: ## Apply database migrations
	cd $(BACKEND) && uv run alembic upgrade head

dev: migrate ## Run API (:8000), worker (queue mode) and web (:3000) together
	@trap 'kill 0' EXIT; \
	(cd $(BACKEND) && uv run uvicorn lane.api.main:app --reload --port 8000) & \
	(cd $(BACKEND) && uv run python -m lane.worker) & \
	(cd $(FRONTEND) && npm run dev) & \
	wait

demo: migrate ## The demo as it should run: production build of the web app, then API, worker and web (:3000)
	cd $(FRONTEND) && npm run build
	@trap 'kill 0' EXIT; \
	(cd $(BACKEND) && uv run uvicorn lane.api.main:app --port 8000) & \
	(cd $(BACKEND) && uv run python -m lane.worker) & \
	(cd $(FRONTEND) && npm run start) & \
	wait

worker: migrate ## Run a worker: claims queued jobs (LANE_RUN_MODE=queue); start several to share work
	cd $(BACKEND) && uv run python -m lane.worker

api: migrate ## Run the API only
	cd $(BACKEND) && uv run uvicorn lane.api.main:app --reload --port 8000

web: ## Run the web app only
	cd $(FRONTEND) && npm run dev

sample: ## Build the sample agent email at var/sample/cobalt_mesa.eml
	python3 -I scripts/make_sample_eml.py

ingest: migrate ## Ingest an email: make ingest EML=path.eml (default: the sample)
	@test -n "$(EML)" || $(MAKE) -s sample
	cd $(BACKEND) && uv run python -m lane.intake.cli "$(abspath $(or $(EML),var/sample/cobalt_mesa.eml))"
	cd $(BACKEND) && uv run python -m lane.pipeline.cli process --latest

reset: migrate sample ## Delete every submission, then ingest and process the sample package (all files)
	cd $(BACKEND) && uv run python -m lane.intake.reset_cli
	$(MAKE) -s ingest

demo-reset: migrate sample ## The demo's starting point: only the pristine sample, before the agent's reply, no manual resolutions, expert review from the cache
	cd $(BACKEND) && uv run python -m lane.intake.reset_cli
	cd $(BACKEND) && uv run python -m lane.intake.cli "$(CURDIR)/var/sample/cobalt_mesa.eml"
	cd $(BACKEND) && uv run python -m lane.pipeline.cli process --latest
	cd $(BACKEND) && uv run python -m lane.pipeline.cli analyze --latest || echo "The expert review is not cached for this input; it waits for approval (make analyze APPROVE=1)"

process: migrate ## Run the pipeline on the latest submission (or SUB=<id>)
	cd $(BACKEND) && uv run python -m lane.pipeline.cli process $(or $(SUB),--latest)

rules: ## Print the decision and every guideline rule outcome (or SUB=<id>)
	cd $(BACKEND) && uv run python -m lane.pipeline.cli rules $(or $(SUB),--latest)

analyze: ## Run the analyst (cached when unchanged); a paid run needs APPROVE=1 (or SUB=<id>)
	cd $(BACKEND) && uv run python -m lane.pipeline.cli analyze $(or $(SUB),--latest) $(if $(APPROVE),--approve,)

eval: ## Gold set, perturbations and the synthetic packet, from stored runs; writes evals/report/ (EVAL_DB=url: in that database)
	cd $(BACKEND) && $(if $(EVAL_DB),LANE_DATABASE_URL=$(EVAL_DB) ,)uv run python -m lane.evals.score $(or $(GOLD),sample_packet) $(if $(SUB),--submission $(SUB),)
	cd $(BACKEND) && $(if $(EVAL_DB),LANE_DATABASE_URL=$(EVAL_DB) ,)uv run python -m lane.evals.perturb

layout-speed: ## Layout memory as a speed figure in the eval database: make layout-speed EVAL_DB=url SOURCE=<confirmed submission id>
	cd $(BACKEND) && LANE_DATABASE_URL=$(EVAL_DB) uv run python -m lane.evals.layout_speed --source $(SOURCE)

eval-db: ## Rebuild the eval database from the main one (Postgres): make eval-db EVAL_DB=postgresql+psycopg://localhost:5433/lane_eval
	cd $(BACKEND) && uv run python ../scripts/eval_db.py $(EVAL_DB)

perturb: ## Build the perturbed emails and synthetic packet, process any new ones, and check them (EVAL_DB=url: in that database, never the inbox's)
	cd $(BACKEND) && uv run python ../scripts/make_perturbations.py
	cd $(BACKEND) && uv run python ../scripts/make_synthetic_packet.py
	cd $(BACKEND) && $(if $(EVAL_DB),LANE_DATABASE_URL=$(EVAL_DB) ,)uv run python -m lane.evals.perturb

actions: ## Build the agent request and print the draft email (never sent)
	cd $(BACKEND) && uv run python -m lane.pipeline.cli actions $(or $(SUB),--latest)

resolution: ## Print conflicts, missing information, drivers and vehicles (or SUB=<id>)
	cd $(BACKEND) && uv run python -m lane.pipeline.cli resolution $(or $(SUB),--latest)

escalate: ## Show documents that failed checks and ask before re-reading them (or SUB=<id>)
	cd $(BACKEND) && uv run python -m lane.pipeline.cli escalate $(or $(SUB),--latest)

run: ingest ## Ingest the sample, process it, write the full result to var/run/latest.json
	@mkdir -p var/run
	cd $(BACKEND) && uv run python -m lane.pipeline.cli export --latest > ../var/run/latest.json
	@echo "Full result: var/run/latest.json"

documents: ## Print the logical documents for the latest submission (or SUB=<id>)
	cd $(BACKEND) && uv run python -m lane.pipeline.cli documents $(or $(SUB),--latest)

pages: ## Print the page triage table for the latest submission (or SUB=<id>)
	cd $(BACKEND) && uv run python -m lane.pipeline.cli pages $(or $(SUB),--latest)

api-types: ## Export the OpenAPI schema and regenerate the frontend's API types
	cd $(BACKEND) && uv run python ../scripts/export_openapi.py
	cd $(FRONTEND) && npx openapi-typescript src/lib/api/openapi.json -o src/lib/api/schema.d.ts && npx prettier --write src/lib/api/openapi.json src/lib/api/schema.d.ts > /dev/null

screens: ## Screenshot every screen in light and dark into docs/screens (needs make dev running)
	cd $(FRONTEND) && npx playwright test --project=light --project=dark

e2e: ## Behavior tests in a real browser: lens, conflicts, roles (needs make dev running)
	cd $(FRONTEND) && npx playwright test --project=behavior

lint: ## Lint backend and frontend
	cd $(BACKEND) && uv run ruff check . && uv run ruff format --check .
	cd $(FRONTEND) && npm run lint && npm run format:check

format: ## Auto-format everything
	cd $(BACKEND) && uv run ruff check --fix . && uv run ruff format .
	cd $(FRONTEND) && npm run format

typecheck: ## Strict type checks
	cd $(BACKEND) && uv run pyright
	cd $(FRONTEND) && npm run typecheck

test: ## Run all tests
	cd $(BACKEND) && uv run pytest
	cd $(FRONTEND) && npm test

guard: ## Fail if packet specifics leak into pipeline code
	@scripts/no_hardcode_guard.sh

build: ## Production build of the web app (catches what dev mode allows)
	cd $(FRONTEND) && npm run build

check: lint typecheck test check-pg guard build ## Everything CI runs (the Postgres tests need LANE_TEST_PG_URL, default localhost:5433)

test-pg: ## The whole backend suite on Postgres, one cloned database per test (LANE_TEST_PG_URL)
	cd $(BACKEND) && LANE_TEST_ALL_ON_PG=$${LANE_TEST_PG_URL:-postgresql+psycopg://localhost:5433/lane_test} uv run pytest

check-pg: ## Postgres tests against a server we may create databases on (LANE_TEST_PG_URL)
	cd $(BACKEND) && LANE_TEST_PG_URL=$${LANE_TEST_PG_URL:-postgresql+psycopg://localhost:5433/lane_test} uv run pytest tests/test_postgres.py

clean: ## Remove local data and build output
	rm -rf var $(FRONTEND)/.next

minio: ## Local S3 (MinIO) for files: API :9000, console :9001; keys from .env (LANE_S3_ACCESS_KEY_ID/SECRET)
	@mkdir -p var/minio
	MINIO_ROOT_USER=$$(sed -n 's/^LANE_S3_ACCESS_KEY_ID=//p' .env) \
	MINIO_ROOT_PASSWORD=$$(sed -n 's/^LANE_S3_SECRET_ACCESS_KEY=//p' .env) \
	minio server var/minio --address 127.0.0.1:9000 --console-address 127.0.0.1:9001

blobs-to-s3: ## Copy every stored file into the S3 bucket the settings name (same keys)
	cd $(BACKEND) && uv run python ../scripts/blobs_to_s3.py

mailpit: ## Local SMTP server for agent emails (SMTP :1025, web and API :8025)
	mailpit --smtp 127.0.0.1:1025 --listen 127.0.0.1:8025

mailpit-watch: ## Ingest emails that arrive in Mailpit (needs make mailpit)
	cd $(BACKEND) && LANE_MAILPIT_URL=$${LANE_MAILPIT_URL:-http://127.0.0.1:8025} uv run python -m lane.intake.mailpit

send: ## Send an .eml to Mailpit over SMTP: make send EML=var/perturb/photo.eml
	cd $(BACKEND) && uv run python ../scripts/send_eml.py "$(CURDIR)/$(or $(EML),var/synthetic/north_star.eml)"

template: ## Propose a layout template from a confirmed document: make template DOC=<id> NAME="..." [PAGE=2]
	cd $(BACKEND) && uv run python -m lane.documents.template_tool propose --document $(DOC) --name "$(NAME)" --page $(or $(PAGE),1)

templates-check: ## Run every layout template against every stored page; fails if one matches another type
	cd $(BACKEND) && uv run python -m lane.documents.template_tool check
