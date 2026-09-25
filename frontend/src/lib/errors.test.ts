import { describe, expect, it } from 'vitest'
import { humanizeJobError } from './errors'

describe('humanizeJobError', () => {
  it('maps known backend prefixes to friendly copy', () => {
    expect(humanizeJobError('VIDEO_UNAVAILABLE: Private video').message).toMatch(/private/i)
    expect(humanizeJobError('UPLOAD_FAILED: status 500').action).toBe('Retry the job.')
  })

  it('never leaks the raw prefix', () => {
    for (const raw of ['AUTH_REQUIRED: x', 'INGEST_FAILED: 403', 'MISSING_ARTIFACT: stems']) {
      expect(humanizeJobError(raw).message).not.toMatch(/^[A-Z_]+:/)
    }
  })

  it('surfaces the plain-language detail of UNSUPPORTED_SOURCE', () => {
    expect(humanizeJobError('UNSUPPORTED_SOURCE: Livestreams are not supported').message).toBe(
      'Livestreams are not supported',
    )
  })

  it('falls back to generic copy for empty or unknown errors', () => {
    expect(humanizeJobError(null).message).toBe('Processing failed.')
    expect(humanizeJobError('').message).toBe('Processing failed.')
    expect(humanizeJobError('SOMETHING_NEW: boom').message).toBe('Processing failed.')
    expect(humanizeJobError('lowercase text').message).toBe('Processing failed.')
  })
})
