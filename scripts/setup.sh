#!/usr/bin/env bash
# Install everything needed to lint, typecheck, test and run the app locally.
# Idempotent and fast when nothing changed: each step is skipped while the
# lockfile / requirements it depends on hash the same as last time. Used by
# `make setup` and by the Claude Code SessionStart hook.
#
# Deliberately NOT installed: the backend's production ML stack (torch,
# demucs, torchcrepe, yt-dlp - see backend/requirements.txt). Tests stub it,
# and the mock worker stands in for it end to end.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMPS="$ROOT/.dev/stamps"
mkdir -p "$STAMPS"

hash_of() { cat "$@" | sha256sum | cut -d' ' -f1; }

fresh() { # fresh <name> <hash>: true if the step already ran for this hash
  [ -f "$STAMPS/$1" ] && [ "$(cat "$STAMPS/$1")" = "$2" ]
}

log() { printf '[setup] %s\n' "$*"; }

# --- node ---------------------------------------------------------------------
# .nvmrc pins the Node major for everything: CI, Vercel (engines in
# frontend/package.json), cloud sessions (the SessionStart hook installs it).
# Here we only warn: a local machine's Node is its owner's to manage.
want="$(tr -dc '0-9' < "$ROOT/.nvmrc")"
have="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo none)"
if [ "$have" != "$want" ]; then
  log "WARNING: Node $have is active but .nvmrc pins Node $want (try: nvm use)"
fi

# --- frontend -----------------------------------------------------------------
# `npm ci` never rewrites package-lock.json, so parallel sessions don't pick
# up spurious lockfile diffs. Its postinstall runs `prisma generate`. The
# Node major is part of the stamp so switching majors reinstalls.
h="$(echo "node$have" | cat - "$ROOT/frontend/package-lock.json" | sha256sum | cut -d' ' -f1)"
if [ -d "$ROOT/frontend/node_modules" ] && fresh npm "$h"; then
  log "frontend deps up to date"
else
  log "installing frontend deps (npm ci)"
  (cd "$ROOT/frontend" && npm ci --no-audit --no-fund --loglevel=error)
  echo "$h" > "$STAMPS/npm"
  # npm ci's postinstall just generated the client for the current schema.
  hash_of "$ROOT/frontend/prisma/schema.prisma" > "$STAMPS/prisma"
fi

h="$(hash_of "$ROOT/frontend/prisma/schema.prisma")"
if ! fresh prisma "$h"; then
  log "regenerating Prisma client"
  (cd "$ROOT/frontend" && npx --no-install prisma generate >/dev/null)
  echo "$h" > "$STAMPS/prisma"
fi

# --- backend ------------------------------------------------------------------
VENV="$ROOT/backend/.venv"
h="$(hash_of "$ROOT/backend/requirements-dev.txt")"
if [ -x "$VENV/bin/python" ] && fresh venv "$h"; then
  log "backend venv up to date"
else
  log "installing backend dev deps into backend/.venv"
  if command -v uv >/dev/null 2>&1; then
    [ -x "$VENV/bin/python" ] || uv venv --quiet "$VENV"
    uv pip install --quiet --python "$VENV/bin/python" -r "$ROOT/backend/requirements-dev.txt"
  else
    [ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --disable-pip-version-check -r "$ROOT/backend/requirements-dev.txt"
  fi
  echo "$h" > "$STAMPS/venv"
fi

log "done"
