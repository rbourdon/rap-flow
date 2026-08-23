'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { reprocessJob } from '@/app/actions'
import { type WorkflowStage } from '@/app/workflow-stages'

// Stages a user can meaningfully re-run for an existing job. `finalize` (blob
// upload) is excluded - it always runs as part of any reprocess. Each entry
// reuses every upstream artifact from the durable cache, so e.g. "Synthesize
// Beats" re-renders using the already-separated stems without re-downloading.
const REPROCESSABLE: { stage: WorkflowStage; label: string; hint: string }[] = [
  { stage: 'ingest', label: 'Re-download source', hint: 're-fetches the audio' },
  { stage: 'separate', label: 'Re-separate stems', hint: 'reuses the download' },
  { stage: 'detect', label: 'Re-analyze syllables', hint: 'reuses the stems' },
  { stage: 'render', label: 'Re-synthesize beats', hint: 'reuses stems & syllables' },
]

export function ReprocessControls({ jobId }: { jobId: string }) {
  const [stage, setStage] = useState<WorkflowStage>('render')
  const [isBusy, setIsBusy] = useState(false)
  const router = useRouter()

  const handleReprocess = async () => {
    setIsBusy(true)
    try {
      await reprocessJob(jobId, stage)
      router.refresh()
    } catch (error) {
      console.error('Failed to reprocess stage:', error)
      alert('Failed to reprocess stage')
      setIsBusy(false)
    }
  }

  const active = REPROCESSABLE.find((r) => r.stage === stage)

  return (
    <div className="flex flex-col sm:flex-row sm:items-center gap-3">
      <label className="text-sm text-neutral-400">
        Reprocess a stage:
      </label>
      <select
        value={stage}
        onChange={(e) => setStage(e.target.value as WorkflowStage)}
        disabled={isBusy}
        className="bg-neutral-900 border border-white/10 rounded-lg px-3 py-2 text-sm text-white disabled:opacity-50"
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
        className="bg-indigo-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-indigo-700 transition disabled:opacity-50"
      >
        {isBusy ? 'Starting...' : 'Reprocess'}
      </button>
      {active && (
        <span className="text-xs text-neutral-500">{active.hint}</span>
      )}
    </div>
  )
}
