import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { formatDuration, formatRelativeTime } from './format'

describe('formatDuration', () => {
  it('formats minutes and hours', () => {
    expect(formatDuration(0)).toBe('0:00')
    expect(formatDuration(65)).toBe('1:05')
    expect(formatDuration(3725)).toBe('1:02:05')
  })

  it('returns an empty string for missing or invalid input', () => {
    expect(formatDuration(null)).toBe('')
    expect(formatDuration(undefined)).toBe('')
    expect(formatDuration(-1)).toBe('')
    expect(formatDuration(Number.NaN)).toBe('')
  })
})

describe('formatRelativeTime', () => {
  const now = new Date('2026-01-15T12:00:00Z')
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(now)
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  const ago = (ms: number) => new Date(now.getTime() - ms)

  it('buckets into human units', () => {
    expect(formatRelativeTime(ago(10_000))).toBe('just now')
    expect(formatRelativeTime(ago(5 * 60_000))).toBe('5m ago')
    expect(formatRelativeTime(ago(3 * 3_600_000))).toBe('3h ago')
    expect(formatRelativeTime(ago(2 * 86_400_000))).toBe('2d ago')
  })

  it('returns an empty string for an invalid date', () => {
    expect(formatRelativeTime('not a date')).toBe('')
  })
})
