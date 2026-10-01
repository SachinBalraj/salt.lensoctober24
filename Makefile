# SaltLens DSS — developer Makefile
# Override ports via env, e.g.  make dev BACKEND_PORT=8001 FRONTEND_PORT=3001

BACKEND_PORT ?= 8000
FRONTEND_PORT ?= 3000
export BACKEND_PORT FRONTEND_PORT

PY := backend/.venv/bin/python

.PHONY: help setup dev backend frontend test test-backend test-frontend
.PHONY: lint build clean

help: ## show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## one-time: venv, backend + ML deps, npm install, .env files
	bash scripts/setup.sh

dev: ## run backend + frontend with reload (ports from env)
	bash scripts/dev.sh

backend: ## run backend only (uvicorn --reload)
	cd backend && .venv/bin/uvicorn app.main:app --host $(BACKEND_HOST) --port $(BACKEND_PORT) --reload
BACKEND_HOST ?= 0.0.0.0

frontend: ## run frontend only (next dev)
	cd frontend && npm run dev -- --port $(FRONTEND_PORT)

test: test-backend test-frontend ## run both test suites

test-backend: ## backend pytest (no Mongo needed)
	cd backend && .venv/bin/python -m pytest

test-frontend: ## frontend vitest + typecheck + lint + prod build
	cd frontend && npm run test && npm run typecheck && npm run lint && npm run build

lint: ## backend ruff lint
	cd backend && .venv/bin/python -m ruff check app tests

clean: ## remove build artifacts and caches (keeps .venv and node_modules)
	rm -rf frontend/.next frontend/out backend/.pytest_cache backend/.mypy_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .ruff_cache -prune -exec rm -rf {} +