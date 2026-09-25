# rap-flow

Turns a rap vocal into percussion: a **Next.js** app (`frontend/`, deployed on
Vercel, Postgres on Neon via Prisma, auth via Better Auth + Google) triggers a
**Python pipeline on Modal** (`backend/`: yt-dlp → Demucs → syllable detection
→ drum score → sampler render), which reports progress back through
HMAC-signed callbacks. Deeper docs: `backend/README.md`, `frontend/README.md`.
`frontend/CLAUDE.md` adds Next.js-version rules for frontend work.

## Commands

Everything runs from the repo root and needs no cloud credentials.

| Command | What it does |
| --- | --- |
| `make check` | Lint + typecheck + unit/contract tests for both halves. **Run before every push**: it is CI's backend and frontend jobs. |
| `make e2e` | Playwright against the real app + local Postgres + mock Modal worker (CI's `e2e` job). Run for UI, route, auth or schema changes. |
| `make dev` | The whole app on http://localhost:3000 with no cloud services. |
| `make session` | Print a signed-in session cookie for `make dev` (no Google needed). |
| `make test-backend ARGS="-n 0 -k name"` | Focus one backend test (`-n 0` turns off parallelism). |
| `make test-frontend ARGS="src/lib"` | Focus frontend unit tests. |
| `make db-up` / `db-down` / `db-reset` | Local Postgres in `.dev/postgres` on port 54329. |
| `make setup` | Install deps. Every target already runs it, and it's a no-op when nothing changed. |

Cloud sessions get dependencies from the SessionStart hook
(`.claude/hooks/session-start.sh` → `scripts/setup.sh`), with
`backend/.venv/bin` on `PATH`. The backend dev venv deliberately leaves out
torch/demucs/yt-dlp. Tests stub them, and `scripts/mock_worker.py` stands in
for the whole Modal worker (steer it with `?mock-fail=<stage>&mock-error=<PREFIX>`
on the source URL). Seeing a UI change in a browser: `.claude/skills/run-app`.

## Working in parallel

Many agents work this repo at once, each on its own branch and PR. To keep
them from colliding:

- **One task, one branch, one PR.** Keep the diff to the task: no drive-by
  reformatting or renames, which cause merge conflicts in other PRs.
- **Bring main in by merging, not rebasing**, once a PR is open, so reviewers'
  links and other checkouts stay valid.
- **Lockfile.** Change dependencies only with `npm install <pkg>` in
  `frontend/`, never by hand. On a `package-lock.json` conflict, take main's
  copy and re-run your `npm install <pkg>`. If npm 10 crashes with
  `Cannot read properties of null (reading 'edgesOut')` (an arborist bug hit
  by vitest's peer set), use `npx npm@11 install ...` instead.
  `@playwright/test` is pinned exactly to match the Chromium preinstalled in
  cloud sessions.
- **Files that tools rewrite.** `next dev` rewrites `frontend/AGENTS.md` and
  `frontend/tsconfig.json` when they drift. If either shows up modified, commit
  it rather than reverting.
- **Don't commit** `.dev/`, `output/`, artifact caches, logs, or one-off patch
  scripts.

## Contracts the tests enforce

Frontend and backend deploy separately. Their seams are checked statically in
`backend/tests/test_contracts.py`. When one of these fails, make the matching
change on the other side **in the same PR**:

- `WORKFLOW_STAGES` (`frontend/src/app/workflow-stages.ts`) must equal
  `STAGES` (`backend/workflow.py`).
- Every field `worker.py` sends through `_post_callback` must be read by
  `frontend/src/app/api/jobs/[id]/complete/route.ts`, and vice versa.
- Every error prefix in `worker.py` `_classify_error` needs copy in
  `frontend/src/lib/errors.ts`.
- The trigger body in `actions.ts` `triggerWorker` must match what
  `web_trigger` reads.
- Every local module `worker.py` imports (transitively) must be listed in the
  Modal image's `add_local_python_source(...)`. Otherwise it passes locally
  and in CI, then fails inside Modal after deploy.
- The callback HMAC is `hex(HMAC-SHA256(secret, json.dumps(payload)))` over the
  raw body. `route.test.ts` pins a Python-generated golden vector.

Also: bump `PIPELINE_VERSION` in `workflow.py` when a cached artifact's
*format* changes. Never move a flow-layer hit off its syllable's time
(`backend/tests/test_flow.py` asserts float equality).

## Shared production state: handle with care

There is one of each, and every agent's work eventually lands on it.

- **Database.** Neon branch `production` is the only branch. Vercel gives
  production, preview *and* development the same `DATABASE_URL`, so never
  point a local command at it. Schema reaches it through `prisma db push` in
  the **production** build (`frontend/scripts/db-deploy.mjs`; preview builds
  skip it). So schema changes must be **additive and nullable** (or defaulted):
  a rename or drop is data loss and fails the deploy. For realistic data,
  create a throwaway Neon branch from `production` with the Neon connector,
  and delete it when done.
- **Modal** (app `rap-flow-worker`, volumes `rap-flow-artifacts` and
  `demucs-models`, secret `rap-flow-secrets`). Deploys happen only from `main`
  through `.github/workflows/deploy-modal.yml` (lint + tests, then deploy, one at
  a time). Never `modal deploy` from a branch: there's no staging app, so it
  replaces production for everyone.
- **Vercel** project `rap-flow` (team "Pause 9"). Every PR gets a preview
  deployment. Use the Vercel connector for build and runtime logs. The Blob
  store is private: the app proxies reads through
  `/api/jobs/[id]/asset`.
