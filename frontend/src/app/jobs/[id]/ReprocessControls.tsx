'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { reprocessJob } from '@/app/actions'
import { type WorkflowStage } from '@/app/workflow-stages'
import { btnPrimary, focusRing } from '@/lib/ui'

// Stages a user can meaningfully re-run for an existing job. `finalize` (blob
// upload) is excluded - it always runs as part of any reprocess. Each entry
// reuses every upstream artifact from the durable cache, so e.g. "Synthesize
// Beats" re-renders using the already-separated stems without re-downloading.
const REPROCESSABLE: { stage: WorkflowStage; label: string; hint: string }[] = [
  { stage: 'ingest', label: 'Re-download source', hint: 're-fetches the audio' },
  { stage: 'separate', label: 'Re-separate stems', hint: 'reuses the download' },
  { stage: 'detect', label: 'Re-analyze syllables', hint: 'reuses the stems' },
  { stage: 'groove', label: 'Re-imagine drums', hint: 'reuses stems & syllables' },
  { stage: 'render', label: 'Re-render beats', hint: 'reuses the drum score' },
]

export function ReprocessControls({ jobId }: { jobId: string }) {
  const [stage, setStage] = useState<WorkflowStage>('render')
  const [isBusy, setIsBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const router = useRouter()

  const handleReprocess = async () => {
    setIsBusy(true)
    setError(null)
    try {
      await reprocessJob(jobId, stage)
      router.refresh()
    } catch (err) {
      // Inline error instead of a browser alert().
      setError(err instanceof Error ? err.message : 'Failed to reprocess stage.')
      setIsBusy(false)
    }
  }

  const active = REPROCESSABLE.find((r) => r.stage === stage)

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-col sm:flex-row sm:items-center gap-3">
        <label htmlFor="reprocess-stage" className="text-sm text-neutral-400">
          Reprocess a stage:
        </label>
        <select
          id="reprocess-stage"
          value={stage}
          onChange={(e) => setStage(e.target.value as WorkflowStage)}
          disabled={isBusy}
          className={`bg-neutral-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white disabled:opacity-50 ${focusRing}`}
        >
          {REPROCESSABLE.map((r) => (
            <option key={r.stage} value={r.stage}>
              {r.label}
            </option>
          ))}
        </select>
        <button
          onClick={handleReprocess}
          disabled={isBusy}
          className={`${btnPrimary} px-4 py-2 text-sm`}
        >
          {isBusy ? 'Starting…' : 'Reprocess'}
        </button>
        {active && (
          <span className="text-xs text-neutral-400">{active.hint}</span>
        )}
      </div>
      {error && (
        <p className="text-sm text-red-400" role="alert">
          {error}{' '}
          <button onClick={handleReprocess} className="underline hover:text-red-300">Try again</button>
        </p>
      )}
    </div>
  )
}
