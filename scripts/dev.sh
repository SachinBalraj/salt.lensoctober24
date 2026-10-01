#!/usr/bin/env bash
# Run backend + frontend with hot reload.
# Ports come from env with conventional defaults; override if they are busy:
#   BACKEND_PORT=8001 FRONTEND_PORT=3001 ./scripts/dev.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"

cleanup() { kill "${BACKEND_PID:-}" "${FRONTEND_PID:-}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

( cd "$ROOT/backend" && .venv/bin/uvicorn app.main:app \
    --host "$BACKEND_HOST" --port "$BACKEND_PORT" --reload ) & BACKEND_PID=$!

( cd "$ROOT/frontend" && BACKEND_PORT="$BACKEND_PORT" FRONTEND_PORT="$FRONTEND_PORT" \
    npm run dev -- --port "$FRONTEND_PORT" ) & FRONTEND_PID=$!

echo "backend  -> http://localhost:$BACKEND_PORT  (docs /docs)"
echo "frontend -> http://localhost:$FRONTEND_PORT"
echo "press Ctrl-C to stop both"
wait