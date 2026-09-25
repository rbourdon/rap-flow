# One command surface for humans, agents and CI. `make check` is what CI's
# backend + frontend jobs run; `make e2e` is its e2e job.
.DEFAULT_GOAL := help
SHELL := bash

VENV := backend/.venv
PY := $(abspath $(VENV))/bin/python

.PHONY: help
help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- setup -------------------------------------------------------------------
.PHONY: setup
setup: ## Install frontend + backend dev deps (a no-op when nothing changed; every target runs it)
	@scripts/setup.sh

# --- checks ------------------------------------------------------------------
.PHONY: check lint lint-backend lint-frontend typecheck test test-backend test-frontend
check: lint typecheck test ## Lint, typecheck and unit/contract tests: run before every push

lint: lint-backend lint-frontend ## Lint both halves
lint-backend: setup
	cd backend && $(PY) -m ruff check .
lint-frontend: setup
	cd frontend && npm run -s lint

typecheck: setup ## Typecheck the frontend (generates Next route types first)
	cd frontend && npm run -s typecheck

test: test-backend test-frontend ## Unit + contract tests for both halves
test-backend: setup ## Backend pytest (parallel; add ARGS="-n 0 -k name" to focus)
	cd backend && $(PY) -m pytest $(ARGS)
test-frontend: setup ## Frontend vitest
	cd frontend && npx vitest run $(ARGS)

.PHONY: e2e
e2e: setup db-up ## Playwright against real Next + local Postgres + mock worker
	cd frontend && npx playwright test $(ARGS)

.PHONY: build
build: setup ## Production build of the frontend (no db push)
	cd frontend && npx next build

# --- local stack -------------------------------------------------------------
.PHONY: dev db-up db-down db-reset mock-worker session
dev: setup ## Run the app locally: Postgres + mock worker + next dev on :3000
	@scripts/dev.sh

db-up: setup ## Start local Postgres and sync the schema
	@scripts/dev-db.sh start
db-down: ## Stop local Postgres
	@scripts/dev-db.sh stop
db-reset: ## Wipe local Postgres data and start fresh
	@scripts/dev-db.sh reset

mock-worker: ## Run only the mock Modal worker on :8765
	python3 scripts/mock_worker.py

session: ## Print a signed-in session cookie for the local dev server
	@cd frontend && DATABASE_URL=$$(../scripts/dev-db.sh url) node scripts/dev-session.cjs $(EMAIL)
