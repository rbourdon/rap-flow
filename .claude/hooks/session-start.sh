#!/bin/bash
# SessionStart hook for Claude Code on the web: make `make check` / `make e2e`
# work the moment a cloud session starts. It puts the Node major from .nvmrc
# on PATH (installing it once if the image lacks it), then runs
# scripts/setup.sh (shared with `make setup`), which skips steps whose inputs
# haven't changed, so resumes and compactions cost well under a second.
set -euo pipefail

# Local sessions manage their own environment.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}"

# --- Node ---------------------------------------------------------------------
# The cloud image ships older Node majors only. Install the one .nvmrc pins
# from nodejs.org, checksum-verified, into /opt/node<major> beside the image's
# own /opt/node<N> dirs. The container is snapshotted after this hook, so the
# download happens once per environment, not per session.
NODE_MAJOR="$(tr -dc '0-9' < .nvmrc)"
NODE_HOME="/opt/node$NODE_MAJOR"
[ -w /opt ] || NODE_HOME="$HOME/.local/node$NODE_MAJOR"

if [ ! -x "$NODE_HOME/bin/node" ]; then
  case "$(uname -m)" in
    x86_64) arch=x64 ;;
    aarch64 | arm64) arch=arm64 ;;
    *) echo "session-start: no Node build for $(uname -m)" >&2; exit 1 ;;
  esac
  base="https://nodejs.org/dist/latest-v$NODE_MAJOR.x"
  tmp="$(mktemp -d)"
  trap 'rm -rf "${tmp:?}"' EXIT
  curl -fsSL "$base/SHASUMS256.txt" -o "$tmp/SHASUMS256.txt"
  tarball="$(grep -oE "node-v$NODE_MAJOR\.[0-9]+\.[0-9]+-linux-$arch\.tar\.xz" "$tmp/SHASUMS256.txt" | head -1)"
  echo "session-start: installing $tarball into $NODE_HOME" >&2
  curl -fsSL "$base/$tarball" -o "$tmp/$tarball"
  (cd "$tmp" && grep " $tarball\$" SHASUMS256.txt | sha256sum -c --quiet -)
  mkdir -p "$NODE_HOME"
  tar -xJf "$tmp/$tarball" -C "$NODE_HOME" --strip-components=1
fi
export PATH="$NODE_HOME/bin:$PATH"

# --- Dependencies ---------------------------------------------------------------
./scripts/setup.sh >&2

# Node and the backend venv go first on PATH for the rest of the session, so
# `node`/`npm`/`npx` are the pinned major and `python -m pytest` / `ruff` just work.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export PATH=\"$NODE_HOME/bin:$PWD/backend/.venv/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
fi
