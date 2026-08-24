// Shared upload constraints, used by both the client Upload widget and the
// server upload route so client-side validation matches what the server allows.

// Max upload size. Kept in sync with the upload route's token limit.
export const MAX_UPLOAD_BYTES = 100 * 1024 * 1024 // 100 MB

export const ALLOWED_AUDIO_CONTENT_TYPES = [
  'audio/mpeg',
  'audio/wav',
  'audio/flac',
  'audio/x-wav',
  'audio/mp3',
  'audio/ogg',
]

// A source URL must be a YouTube or SoundCloud link (incl. `watch?v=` and
// `youtu.be` short links).
export function isValidSourceUrl(raw: string): boolean {
  const value = raw.trim()
  if (!value) return false
  let url: URL
  try {
    url = new URL(value)
  } catch {
    return false
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return false
  const host = url.hostname.toLowerCase().replace(/^www\./, '')
  const isYouTube =
    host === 'youtube.com' ||
    host.endsWith('.youtube.com') ||
    host === 'youtu.be'
  const isSoundCloud =
    host === 'soundcloud.com' || host.endsWith('.soundcloud.com')
  return isYouTube || isSoundCloud
}

export function isAudioFile(file: File): boolean {
  if (file.type) return file.type.startsWith('audio/')
  // Fall back to extension when the browser doesn't supply a MIME type.
  return /\.(mp3|wav|flac|ogg|m4a|aac)$/i.test(file.name)
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const kb = bytes / 1024
  if (kb < 1024) return `${kb.toFixed(0)} KB`
  const mb = kb / 1024
  return `${mb.toFixed(1)} MB`
}
