---
name: steward
description: How to drive a rap-flow pull request to green. Covers reproducing each CI job locally, the one required check, bringing in main, lockfile and generated-file conflicts, contract-test failures, Vercel preview and Modal deploy failures. Use when fixing CI, answering review comments, or resolving merge conflicts on a rap-flow PR.
---

# Driving a rap-flow PR

## CI, and how to reproduce each job

`.github/workflows/ci.yml` runs three jobs in parallel. Branch protection should
require only the **`CI OK`** aggregate, which fails if any of them fails.

| CI job | Reproduce locally |
| --- | --- |
| `Backend (ruff, pytest)` | `make lint-backend test-backend` |
| `Frontend (lint, typecheck, unit, build)` | `make lint-frontend typecheck test-frontend build` |
| `E2E (Playwright, mock worker)` | `make e2e` (CI mode: `cd frontend && CI=1 npx playwright test`, which tests the production build) |

Reproduce the failure first, then show the same command passing before you
push. `make check` covers everything except e2e and the build.

Nothing in CI talks to a real external service. The e2e job uses a Postgres
service container and `scripts/mock_worker.py`, so **a red check is never an
infra flake**: it reproduces locally. For e2e failures, download the
`playwright-report` artifact (it includes traces), or re-run locally with
`--trace on`.

## Bringing in main / conflicts

- Merge `origin/main` into the PR branch. Never rebase or force-push a branch
  someone else may have checked out.
- `frontend/package-lock.json` conflict: `git checkout origin/main --
  frontend/package-lock.json`, then re-run the PR's own `npm install <pkg>` in
  `frontend/`. Confirm with `npm ci`. Use Node 24 (`.nvmrc`), so npm 11 writes
  the lockfile. Never hand-merge the lockfile. A conflict in
  `package.json`'s `allowScripts` block: keep both sides' entries.
- `frontend/tsconfig.json` and `frontend/AGENTS.md` are rewritten by `next
  dev`. Take either side, run `make e2e` (which runs `next dev`), and commit
  whatever it leaves.

## Failures with a known meaning

- **`backend/tests/test_contracts.py`**: the frontend and backend disagree
  about stages, callback fields, error prefixes, trigger fields, or the Modal
  image's module list. The message names the mismatch. Fix the other side in
  this PR; don't loosen the test.
- **Vercel preview build** (a GitHub status from Vercel, not a CI job): get the
  build logs through the Vercel connector (project `rap-flow`). `Skipping prisma
  db push: preview deployments share the production database` is expected, not
  an error.
- **Preview pages 500 after a schema change**: expected. Previews share the
  production database, which gets the new columns only when the PR merges.
  Verify with `make e2e` instead.
- **`Deploy Modal Backend` on main** failed after merge: fix forward in a new
  PR. Never run `modal deploy` by hand.

## What not to do

- Don't skip, `.only`, or loosen a test to get green. Don't raise Playwright
  timeouts to paper over a race: wait on a state change instead (see the
  reprocess test in `frontend/e2e/job-lifecycle.spec.ts`).
- Don't push schema changes that drop or rename columns (see `CLAUDE.md`,
  "Shared production state").
