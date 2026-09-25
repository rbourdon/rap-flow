#!/bin/bash
# SessionStart hook for Claude Code on the web: make `make check` / `make e2e`
# work the moment a cloud session starts. All the logic lives in
# scripts/setup.sh (shared with `make setup`), which skips steps whose inputs
# haven't changed, so resumes and compactions cost well under a second.
set -euo pipefail

# Local sessions manage their own environment.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}"
./scripts/setup.sh >&2

# Put the backend venv first on PATH so `python -m pytest` / `ruff` just work.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export PATH=\"$PWD/backend/.venv/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
fi
