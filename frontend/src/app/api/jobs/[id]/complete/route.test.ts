import crypto from 'node:crypto'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

// In-memory stand-in for the one table this route touches.
const jobs = new Map<string, Record<string, unknown>>()
vi.mock('@/lib/db', () => ({
  prisma: {
    job: {
      findUnique: async ({ where }: { where: { id: string } }) => jobs.get(where.id) ?? null,
      update: async ({ where, data }: { where: { id: string }; data: Record<string, unknown> }) => {
        const next = { ...jobs.get(where.id), ...data }
        jobs.set(where.id, next)
        return next
      },
    },
  },
}))

const SECRET = 'test-secret'
let POST: typeof import('./route').POST

beforeAll(async () => {
  // The route reads HMAC_SECRET at module load.
  vi.stubEnv('HMAC_SECRET', SECRET)
  ;({ POST } = await import('./route'))
})

beforeEach(() => {
  jobs.clear()
  jobs.set('job_1', { id: 'job_1', status: 'PENDING', stageStates: null })
})

const sign = (body: string) => crypto.createHmac('sha256', SECRET).update(body).digest('hex')

function callback(body: string, signature: string | null = sign(body)) {
  const headers: Record<string, string> = { 'content-type': 'application/json' }
  if (signature !== null) headers['x-signature'] = signature
  return POST(new Request('http://test/api/jobs/job_1/complete', { method: 'POST', headers, body }), {
    params: Promise.resolve({ id: 'job_1' }),
  })
}

describe('POST /api/jobs/[id]/complete', () => {
  it('accepts a body signed exactly as backend/worker.py _post_callback signs it', async () => {
    // Golden vector from Python: body = json.dumps(payload) (note the ", " and
    // ": " separators), signature = hmac.new(b"test-secret", body.encode(),
    // hashlib.sha256).hexdigest(). If this breaks, the worker's callbacks are
    // being rejected in production.
    const body =
      '{"jobId": "job_1", "status": "PROCESSING", "stage": "Downloading Audio", "stageKey": "ingest", "stageState": "RUNNING"}'
    const pythonSignature = '2f6c232a2c9eebf756d777a5dce186cb5f2e542f166f9472b2dce4c8b9c60ffa'

    const res = await callback(body, pythonSignature)

    expect(res.status).toBe(200)
    expect(jobs.get('job_1')).toMatchObject({ status: 'PROCESSING', stage: 'ingest' })
  })

  it('rejects a missing signature', async () => {
    const res = await callback('{"status": "COMPLETED"}', null)
    expect(res.status).toBe(401)
    expect(jobs.get('job_1')?.status).toBe('PENDING')
  })

  it('rejects a signature over different bytes', async () => {
    const res = await callback('{"status": "COMPLETED"}', sign('{"status":"COMPLETED"}'))
    expect(res.status).toBe(401)
    expect(jobs.get('job_1')?.status).toBe('PENDING')
  })

  it('merges per-stage state rather than replacing it', async () => {
    await callback(JSON.stringify({ status: 'PROCESSING', stageKey: 'ingest', stageState: 'REUSED', reused: true }))
    await callback(
      JSON.stringify({ status: 'PROCESSING', stageKey: 'detect', stageState: 'COMPLETED', warning: 'flux fallback' }),
    )

    const states = jobs.get('job_1')?.stageStates as Record<string, Record<string, unknown>>
    expect(states.ingest).toMatchObject({ state: 'REUSED', reused: true })
    expect(states.detect).toMatchObject({ state: 'COMPLETED', warning: 'flux fallback' })
  })

  it('does not clobber metadata with a callback that omits it', async () => {
    await callback(JSON.stringify({ status: 'PROCESSING', title: 'Song', durationSec: 180 }))
    await callback(JSON.stringify({ status: 'PROCESSING', stageKey: 'separate', stageState: 'RUNNING' }))
    expect(jobs.get('job_1')).toMatchObject({ title: 'Song', durationSec: 180, stage: 'separate' })
  })

  it('stores result URLs on completion', async () => {
    await callback(
      JSON.stringify({
        status: 'COMPLETED',
        stageKey: 'finalize',
        stageState: 'COMPLETED',
        resultUrl: 'https://blob/mix.wav',
        percUrl: 'https://blob/perc.wav',
        mixOpusUrl: 'https://blob/mix.opus.ogg',
        midUrl: null,
      }),
    )
    expect(jobs.get('job_1')).toMatchObject({
      status: 'COMPLETED',
      resultBlobUrl: 'https://blob/mix.wav',
      percBlobUrl: 'https://blob/perc.wav',
      mixOpusBlobUrl: 'https://blob/mix.opus.ogg',
    })
    expect(jobs.get('job_1')?.midBlobUrl).toBeUndefined()
  })

  it('turns a COMPLETED callback without a result into a failure', async () => {
    await callback(JSON.stringify({ status: 'COMPLETED' }))
    expect(jobs.get('job_1')).toMatchObject({ status: 'FAILED', error: expect.stringMatching(/^UPLOAD_FAILED/) })
  })

  it('records failures with their error', async () => {
    await callback(JSON.stringify({ status: 'FAILED', stageKey: 'separate', error: 'INGEST_FAILED: boom' }))
    expect(jobs.get('job_1')).toMatchObject({ status: 'FAILED', error: 'INGEST_FAILED: boom' })
  })
})
