#!/usr/bin/env bash
# Install steps (venv + deps + env templates). Re-runnable.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> backend Python environment"
uv venv --python 3.12 backend/.venv
uv pip install --python backend/.venv/bin/python \
    -r backend/requirements.txt \
    -r backend/requirements-ml.txt \
    -r backend/requirements-dev.txt

echo "==> frontend dependencies"
npm --prefix frontend install

echo "==> environment files (skip if present)"
[ -f backend/.env ] || { cp backend/.env.example backend/.env; echo "  wrote backend/.env from example"; }
[ -f frontend/.env.local ] || { cp frontend/.env.example frontend/.env.local; echo "  wrote frontend/.env.local from example"; }

echo
echo "Done. Configure backend/.env (MONGODB_URI, JWT_SECRET_KEY) and run:"
echo "  make dev"