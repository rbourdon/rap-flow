// Small pure formatting helpers shared across components. No dependencies.

// Format a duration in seconds as "m:ss" (or "h:mm:ss" past an hour).
export function formatDuration(totalSeconds: number | null | undefined): string {
  if (totalSeconds == null || !Number.isFinite(totalSeconds) || totalSeconds < 0) {
    return ''
  }
  const s = Math.round(totalSeconds)
  const hours = Math.floor(s / 3600)
  const minutes = Math.floor((s % 3600) / 60)
  const seconds = s % 60
  const pad = (n: number) => n.toString().padStart(2, '0')
  if (hours > 0) return `${hours}:${pad(minutes)}:${pad(seconds)}`
  return `${minutes}:${pad(seconds)}`
}

// Format a relative time like "2h ago" / "3d ago" from a past date.
export function formatRelativeTime(date: Date | string | number): string {
  const then = new Date(date).getTime()
  if (!Number.isFinite(then)) return ''
  const diffMs = Date.now() - then
  const sec = Math.round(diffMs / 1000)
  if (sec < 45) return 'just now'
  const min = Math.round(sec / 60)
  if (min < 60) return `${min}m ago`
  const hr = Math.round(min / 60)
  if (hr < 24) return `${hr}h ago`
  const day = Math.round(hr / 24)
  if (day < 30) return `${day}d ago`
  const month = Math.round(day / 30)
  if (month < 12) return `${month}mo ago`
  const year = Math.round(month / 12)
  return `${year}y ago`
}
