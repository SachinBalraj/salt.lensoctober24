# SaltLens DSS — dev shortcuts
# `make help` lists everything. Override ports with env vars, e.g.:
#   make dev FRONTEND_PORT=3001 BACKEND_PORT=8001

SHELL := /usr/bin/env bash
BACKEND_PORT ?= 8000
FRONTEND_PORT ?= 3000
export BACKEND_PORT FRONTEND_PORT

.PHONY: help dev backend frontend test test-backend test-frontend deploy-none

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

dev: ## run backend + frontend with hot reload
	./scripts/dev.sh

backend: ## run backend only
	cd backend && .venv/bin/uvicorn app.main:app --host "${BACKEND_HOST:-0.0.0.0}" --port "$(BACKEND_PORT)" --reload

frontend: ## run frontend only
	cd frontend && FRONTEND_PORT="$(FRONTEND_PORT)" npm run dev -- --port "$(FRONTEND_PORT)"

test: test-backend test-frontend ## run both test suites

test-backend: ## backend pytest (no Mongo needed)
	cd backend && .venv/bin/python -m pytest

test-frontend: ## frontend typecheck + prod build
	cd frontend && npm run build

no-ml-deploy: ## ML deployment policy (Modules 8-17) — intentionally no-op
	@echo "Deployment hooks intentionally empty: model be + reg**&&*"