---
name: run-app
description: Launch rap-flow locally (Postgres + mock Modal worker + Next.js dev server) and drive it in a headless browser. Covers signing in without Google, creating jobs that succeed or fail on demand, and screenshotting pages. Use to see a UI change working, reproduce a frontend bug, or check a page by eye. No cloud credentials needed.
---

# Running rap-flow locally

## Start it

Run `make dev` **in the background** (it stays in the foreground otherwise),
then wait for the app:

```bash
make dev                       # Bash tool with run_in_background: true
until curl -sf -o /dev/null http://localhost:3000/; do sleep 1; done
```

This starts local Postgres (`.dev/postgres`, port 54329, schema synced), the
mock worker on :8765, and `next dev` on :3000. To use other ports, set
`APP_PORT=3001 MOCK_WORKER_PORT=8767 make dev`. A second dev server in the
same checkout also needs `NEXT_DIST_DIR=.next-alt`, because Next allows one
dev server per build dir.

## Sign in

Google OAuth is not needed. `make session` inserts a user and a session into
the local database and prints the Better Auth cookie:

```bash
make session                        # or: make session EMAIL=someone@example.com
# {"cookieName": "better-auth.session_token", "cookieValue": "..."}
```

## Drive it with Playwright

Chromium is preinstalled in cloud sessions, matching the pinned
`@playwright/test`. Put a throwaway script **inside `frontend/`** so it resolves
the packages, run it with `node`, then delete it:

```js
// frontend/tmp-shot.mjs  ->  cd frontend && node tmp-shot.mjs && rm tmp-shot.mjs
import { chromium } from '@playwright/test'
import { createDevSession } from './scripts/dev-session.cjs'

const app = 'http://localhost:3000'
const s = await createDevSession({ databaseUrl: 'postgresql://postgres@127.0.0.1:54329/rapflow' })
const browser = await chromium.launch()
const ctx = await browser.newContext()
await ctx.addCookies([{ name: s.cookieName, value: s.cookieValue, url: app }])
const page = await ctx.newPage()
await page.goto(app)
await page.getByLabel(/url/i).fill('https://youtu.be/demo')
await page.getByRole('button', { name: 'Create beat' }).click()
await page.waitForURL(/\/jobs\//)
await page.getByRole('heading', { name: 'Result Mix' }).waitFor({ timeout: 30_000 })
await page.screenshot({ path: '/tmp/job.png', fullPage: true })
await browser.close()
```

Then look at the screenshot with the Read tool.

The simplest route is often a throwaway spec in `frontend/e2e/` that uses the
`signedIn` fixture from `e2e/fixtures.ts`, run with `make e2e
ARGS=e2e/that.spec.ts`. That suite runs on its own ports and database, so it
doesn't disturb `make dev`. Delete the spec afterwards unless it's worth
keeping as a test.

## Scenarios

The mock worker reads knobs from the **source URL's query string**:

| URL | Result |
| --- | --- |
| `https://youtu.be/anything` | every stage completes; result is a 6 s generated mix with WAV/MIDI downloads |
| `...?mock-fail=detect` | fails at `detect` with `INGEST_FAILED` |
| `...?mock-fail=ingest&mock-error=VIDEO_UNAVAILABLE` | fails with that error prefix, which exercises `lib/errors.ts` copy |

`MOCK_STAGE_DELAY=2 make dev` slows the stages so the progress UI can be
watched. File uploads need a real Vercel Blob token and are not mocked.

## Stop it

Kill the background `make dev` task. This stops Next and the mock worker.
Postgres keeps running; `make db-down` stops it, and `make db-reset` wipes it.
