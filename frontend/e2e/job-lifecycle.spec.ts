import type { Page } from '@playwright/test'
import { expect, signInAsNewUser, test } from './fixtures'

// The mock worker walks every stage with signed callbacks; see
// scripts/mock_worker.py for the `mock-fail` / `mock-error` query knobs.

async function createJob(page: Page, sourceUrl: string) {
  await page.goto('/')
  const submit = page.getByRole('button', { name: 'Create beat' })
  // A fill that lands before React hydrates is dropped from the form's state,
  // leaving the button disabled, so fill again until the form has the URL.
  await expect(async () => {
    await page.getByLabel(/url/i).fill(sourceUrl)
    await expect(submit).toBeEnabled({ timeout: 1000 })
  }).toPass()
  await submit.click()
  await page.waitForURL(/\/jobs\/[^/]+$/)
  return new URL(page.url()).pathname.split('/').pop()!
}

test('signed-out visitors get the landing page', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('main').getByRole('button', { name: 'Continue with Google' })).toBeVisible()
})

test('a URL job runs every stage and ends with a playable, downloadable result', async ({ page, signedIn }) => {
  void signedIn
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible()
  const jobId = await createJob(page, 'https://www.youtube.com/watch?v=e2e-happy-path')

  // Progress is live, then polling sees COMPLETED and refreshes into the result.
  await expect(page.getByRole('heading', { name: 'Result Mix' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Mock worker track' })).toBeVisible()
  // The player decoded the proxied WAV (the duration comes from the audio itself).
  await expect(page.getByRole('group', { name: /Audio waveform/ })).toBeVisible()
  await expect(page.getByText('0:00 / 0:06')).toBeVisible()
  for (const label of ['Mix (WAV)', 'Percussion (WAV)', 'Instrumental (WAV)', 'MIDI']) {
    await expect(page.getByRole('link', { name: label })).toBeVisible()
  }

  // The asset proxy streams the worker's file through with auth.
  const mix = await page.request.get(`/api/jobs/${jobId}/asset?type=mix`)
  expect(mix.status()).toBe(200)
  expect(mix.headers()['content-type']).toBe('audio/wav')
  expect(mix.headers()['content-disposition']).toContain(`${jobId}-mix.wav`)

  await page.goto('/')
  await expect(page.getByText('Mock worker track')).toBeVisible()
})

test('reprocessing from a stage reuses everything before it', async ({ page, signedIn }) => {
  void signedIn
  const jobId = await createJob(page, 'https://www.youtube.com/watch?v=e2e-reprocess')
  await expect(page.getByRole('heading', { name: 'Result Mix' })).toBeVisible()

  await page.getByLabel('Reprocess a stage:').selectOption('groove')
  await page.getByRole('button', { name: 'Reprocess' }).click()

  // Don't wait on the transient "Processing" view: the mock can finish before
  // the page refreshes. Only a reprocess marks early stages REUSED.
  const status = async () => (await page.request.get(`/api/jobs/${jobId}/status`)).json()
  await expect
    .poll(async () => {
      const s = await status()
      return s.status === 'COMPLETED' && s.stageStates?.ingest?.state === 'REUSED'
    })
    .toBe(true)
  await expect(page.getByRole('heading', { name: 'Result Mix' })).toBeVisible()

  const { stageStates } = await status()
  for (const stage of ['ingest', 'separate', 'detect']) {
    expect(stageStates[stage]).toMatchObject({ state: 'REUSED', reused: true })
  }
  for (const stage of ['groove', 'render', 'finalize']) {
    expect(stageStates[stage]).toMatchObject({ state: 'COMPLETED' })
  }
})

test('a failed stage shows friendly copy and a retry button', async ({ page, signedIn }) => {
  void signedIn
  await createJob(page, 'https://youtu.be/e2e-failure?mock-fail=detect&mock-error=VIDEO_UNAVAILABLE')

  await expect(page.getByText('This video is private, deleted, or region-blocked.')).toBeVisible()
  await expect(page.getByRole('button', { name: /retry/i })).toBeVisible()
  // The raw prefix is tucked behind "Technical details", never the headline.
  await expect(page.getByText(/^VIDEO_UNAVAILABLE:/)).toBeHidden()
})

test('jobs are private to their owner', async ({ page, browser, baseURL, signedIn }) => {
  void signedIn
  const jobId = await createJob(page, 'https://www.youtube.com/watch?v=e2e-private')
  await expect(page.getByRole('heading', { name: 'Result Mix' })).toBeVisible()

  const anonymous = await browser.newContext({ baseURL })
  expect((await anonymous.request.get(`/api/jobs/${jobId}/status`)).status()).toBe(401)
  await anonymous.close()

  const otherUser = await browser.newContext({ baseURL })
  await signInAsNewUser(otherUser, baseURL!)
  expect((await otherUser.request.get(`/api/jobs/${jobId}/status`)).status()).toBe(403)
  expect((await otherUser.request.get(`/api/jobs/${jobId}/asset?type=mix`)).status()).toBe(404)
  const other = await otherUser.newPage()
  await other.goto(`/jobs/${jobId}`)
  await expect(other.getByRole('heading', { name: 'Mock worker track' })).toBeHidden()
  await otherUser.close()
})
