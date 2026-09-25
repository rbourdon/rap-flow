#!/usr/bin/env bash
# Throwaway local Postgres for development, tests and e2e runs.
#
#   scripts/dev-db.sh start    # init (first time) + start + create db + push schema
#   scripts/dev-db.sh stop
#   scripts/dev-db.sh reset    # wipe all data and start fresh
#   scripts/dev-db.sh status
#   scripts/dev-db.sh url      # print the DATABASE_URL
#
# Data lives in .dev/postgres (gitignored). Idempotent: `start` on a running
# server just re-syncs the schema. Uses the system PostgreSQL binaries
# (apt `postgresql`), which the Claude Code cloud image already has. CI uses a
# postgres service container instead and never calls this.
#
# Env: RAPFLOW_PG_PORT (default 54329 - off the standard port so it never
# collides with a system Postgres), RAPFLOW_PG_DB (default rapflow).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PGDATA="$ROOT/.dev/postgres"
PORT="${RAPFLOW_PG_PORT:-54329}"
DB="${RAPFLOW_PG_DB:-rapflow}"
URL="postgresql://postgres@127.0.0.1:$PORT/$DB"

bindir() {
  if command -v pg_config >/dev/null 2>&1 && [ -x "$(pg_config --bindir)/pg_ctl" ]; then
    pg_config --bindir
    return
  fi
  local d
  d="$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1 || true)"
  if [ -z "$d" ]; then
    echo "dev-db: no PostgreSQL server binaries found (apt-get install postgresql)" >&2
    exit 1
  fi
  echo "$d"
}
BIN="$(bindir)"

# Postgres refuses to run as root; run the server as the `postgres` user.
as_pg() {
  if [ "$(id -u)" = "0" ]; then
    runuser -u postgres -- "$@"
  else
    "$@"
  fi
}

running() {
  [ -f "$PGDATA/postmaster.pid" ] && as_pg "$BIN/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1
}

init() {
  [ -f "$PGDATA/PG_VERSION" ] && return
  mkdir -p "$PGDATA"
  if [ "$(id -u)" = "0" ]; then
    chown postgres: "$PGDATA"
  fi
  chmod 700 "$PGDATA"
  as_pg "$BIN/initdb" -D "$PGDATA" -U postgres --auth=trust -E UTF8 --no-locale >/dev/null
}

start() {
  init
  if ! running; then
    as_pg "$BIN/pg_ctl" -D "$PGDATA" -l "$PGDATA/server.log" -w -t 30 \
      -o "-p $PORT -k $PGDATA -c listen_addresses=127.0.0.1 -c fsync=off -c full_page_writes=off" \
      start >/dev/null
  fi
  "$BIN/psql" -h 127.0.0.1 -p "$PORT" -U postgres -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = '$DB'" | grep -q 1 \
    || "$BIN/createdb" -h 127.0.0.1 -p "$PORT" -U postgres "$DB"
  local out
  if ! out="$(cd "$ROOT/frontend" && DATABASE_URL="$URL" npx --no-install prisma db push 2>&1)"; then
    echo "$out" >&2
    echo "dev-db: prisma db push failed (schema change needing data loss? try: $0 reset)" >&2
    exit 1
  fi
  echo "Postgres ready: $URL"
}

stop() {
  if running; then
    as_pg "$BIN/pg_ctl" -D "$PGDATA" -m fast -w stop >/dev/null
  fi
  echo "Postgres stopped"
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  reset) stop; rm -rf "$PGDATA"; start ;;
  status) running && echo "running: $URL" || { echo "stopped"; exit 1; } ;;
  url) echo "$URL" ;;
  *) echo "usage: $0 {start|stop|reset|status|url}" >&2; exit 2 ;;
esac
