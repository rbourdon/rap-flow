// Maps the backend's machine-readable error prefixes (see backend/pipeline.py
// and backend/worker.py `_classify_error`) to friendly, human-readable copy
// plus a suggested action. Used everywhere a job error is surfaced so users
// never see a raw prefix like "INGEST_FAILED: ...".

export interface HumanError {
  // A short, friendly explanation of what went wrong.
  message: string
  // A suggested next step (e.g. "Try a different video.").
  action: string
}

const KNOWN: Record<string, HumanError> = {
  AUTH_REQUIRED: {
    message: 'This video requires a signed-in session (age or bot check).',
    action: 'Try a different video.',
  },
  VIDEO_UNAVAILABLE: {
    message: 'This video is private, deleted, or region-blocked.',
    action: 'Try a different video or URL.',
  },
  INGEST_FAILED: {
    message: 'The source refused the download. This is usually temporary.',
    action: 'Retry, or try another URL.',
  },
  UPLOAD_FAILED: {
    message: 'Processing failed while saving the result.',
    action: 'Retry the job.',
  },
  MISSING_ARTIFACT: {
    message: 'Processing failed — an intermediate file was missing.',
    action: 'Retry the job.',
  },
}

const GENERIC: HumanError = {
  message: 'Processing failed.',
  action: 'Retry the job.',
}

// Extract the prefix (leading UPPER_SNAKE token before an optional ": detail").
function parse(error: string): { prefix: string; detail: string } {
  const trimmed = (error || '').trim()
  const match = /^([A-Z][A-Z0-9_]+)(?::\s*([\s\S]*))?$/.exec(trimmed)
  if (match) {
    return { prefix: match[1], detail: (match[2] || '').trim() }
  }
  return { prefix: '', detail: trimmed }
}

export function humanizeJobError(error: string | null | undefined): HumanError {
  if (!error) return GENERIC
  const { prefix, detail } = parse(error)

  // UNSUPPORTED_SOURCE carries a specific, already-plain-language reason
  // (livestream, playlist, too long) — surface it directly.
  if (prefix === 'UNSUPPORTED_SOURCE') {
    return {
      message: detail || 'This source type is not supported.',
      action: 'Try a single, non-livestream track under the length limit.',
    }
  }

  return KNOWN[prefix] ?? GENERIC
}
