import { defineConfig, devices } from '@playwright/test'

// End-to-end tests against the real Next app, a real Postgres and the mock
// Modal worker (scripts/mock_worker.py). No Google, Vercel Blob or Modal
// access needed. Locally: `make e2e` (starts Postgres first). CI: the `e2e`
// job in .github/workflows/ci.yml.
//
// Ports and the database are separate from `make dev`'s (3000 / 8765 /
// rapflow), and the app builds into .next-e2e, so this suite can run while a
// dev server is up.
const APP_PORT = Number(process.env.E2E_APP_PORT ?? 3100)
const WORKER_PORT = Number(process.env.E2E_WORKER_PORT ?? 8766)
const baseURL = `http://localhost:${APP_PORT}`

const appEnv = {
  DATABASE_URL:
    process.env.E2E_DATABASE_URL ??
    `postgresql://postgres@127.0.0.1:${process.env.RAPFLOW_PG_PORT ?? 54329}/rapflow_e2e`,
  NEXT_PUBLIC_APP_URL: baseURL,
  BETTER_AUTH_URL: baseURL,
  BETTER_AUTH_SECRET: 'e2e-only-better-auth-secret-0123456789abcdef',
  HMAC_SECRET: 'e2e-hmac-secret',
  MODAL_WORKER_URL: `http://127.0.0.1:${WORKER_PORT}/`,
  NEXT_DIST_DIR: '.next-e2e',
  NEXT_TELEMETRY_DISABLED: '1',
}
// globalSetup and the specs (which mint sessions) read the same values.
Object.assign(process.env, appEnv)

export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.ts',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: process.env.CI ? 2 : undefined,
  timeout: 60_000,
  expect: { timeout: 20_000 },
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `python3 ../scripts/mock_worker.py --port ${WORKER_PORT}`,
      url: `http://127.0.0.1:${WORKER_PORT}/healthz`,
      env: { MOCK_STAGE_DELAY: '0.2' },
      reuseExistingServer: !process.env.CI,
    },
    {
      // CI tests the production build; locally `next dev` starts in seconds.
      command: process.env.CI
        ? `npx next build && npx next start -p ${APP_PORT}`
        : `npx next dev -p ${APP_PORT}`,
      url: baseURL,
      env: appEnv,
      timeout: 240_000,
      stdout: 'pipe',
      reuseExistingServer: !process.env.CI,
    },
  ],
})
