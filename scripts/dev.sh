#!/usr/bin/env bash
# Run the whole app locally with no cloud services: local Postgres, the mock
# Modal worker, and `next dev`. Ctrl-C stops the worker and the app (Postgres
# keeps running; `make db-down` stops it).
#
# Sign in without Google: `make session` prints a cookie for this server.
#
# Env overrides: APP_PORT (3000), MOCK_WORKER_PORT (8765), RAPFLOW_PG_PORT.
# Set MODAL_WORKER_URL to a real deployed worker to use it instead of the mock
# (it must be able to reach NEXT_PUBLIC_APP_URL for callbacks).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_PORT="${APP_PORT:-3000}"
MOCK_WORKER_PORT="${MOCK_WORKER_PORT:-8765}"

"$ROOT/scripts/dev-db.sh" start

export DATABASE_URL="${DATABASE_URL:-$("$ROOT/scripts/dev-db.sh" url)}"
export NEXT_PUBLIC_APP_URL="${NEXT_PUBLIC_APP_URL:-http://localhost:$APP_PORT}"
export BETTER_AUTH_URL="${BETTER_AUTH_URL:-$NEXT_PUBLIC_APP_URL}"
export HMAC_SECRET="${HMAC_SECRET:-local-dev-hmac-secret}"

if [ -z "${MODAL_WORKER_URL:-}" ]; then
  export MODAL_WORKER_URL="http://127.0.0.1:$MOCK_WORKER_PORT/"
  python3 "$ROOT/scripts/mock_worker.py" --port "$MOCK_WORKER_PORT" &
  WORKER_PID=$!
  trap 'kill $WORKER_PID 2>/dev/null || true' EXIT
fi

cd "$ROOT/frontend"
npx next dev -p "$APP_PORT"
