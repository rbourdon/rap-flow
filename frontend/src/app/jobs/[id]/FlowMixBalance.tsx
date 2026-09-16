'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { rerenderJob } from '@/app/actions'
import { focusRing } from '@/lib/ui'

const DEFAULT_BALANCE = 0.5

interface Props {
  jobId: string
  // Persisted balance from the last render; null for jobs rendered before this
  // control existed, which fall back to the backend default.
  initialBalance: number | null
  // True while the job is in flight, so the control is read-only and the
  // "Rendering percussion…" chip stands in for the button.
  isRunning?: boolean
}

/**
 * The one knob that matters: how much of the mix is the rapper's syllables and
 * how much is the record's own beat.
 *
 * The balance is an equal-power gain between the two percussion sub-buses at
 * *render* time, not part of the drum score, so re-rendering reuses the
 * download, the stems, the syllables and the drum score from the durable cache.
 * That is why this is a slider on the result card rather than a full reprocess.
 */
export function FlowMixBalance({ jobId, initialBalance, isRunning }: Props) {
  const [balance, setBalance] = useState(initialBalance ?? DEFAULT_BALANCE)
  const [isBusy, setIsBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const router = useRouter()

  const disabled = isBusy || Boolean(isRunning)
  const pct = Math.round(balance * 100)

  const handleRerender = async () => {
    setIsBusy(true)
    setError(null)
    try {
      await rerenderJob(jobId, balance)
      router.refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start the re-render.')
      setIsBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex justify-between text-xs text-neutral-400">
        <label htmlFor="layer-balance">Syllable flow</label>
        <span>Beat backbone</span>
      </div>

      <div className="relative h-4 flex items-center">
        <div className="absolute inset-x-0 h-1 rounded-full bg-white/15" />
        <div
          className="absolute left-0 h-1 rounded-full bg-gradient-to-r from-indigo-400 to-indigo-300"
          style={{ width: `${pct}%` }}
        />
        <div
          className="absolute h-3.5 w-3.5 -translate-x-1/2 rounded-full bg-white ring-4 ring-indigo-400/35"
          style={{ left: `${pct}%` }}
        />
        {/* The real control sits on top, invisible, so keyboard and pointer
            behaviour stay native while the visuals above stay custom. */}
        <input
          id="layer-balance"
          type="range"
          min="0"
          max="1"
          step="0.05"
          value={balance}
          onChange={(e) => setBalance(parseFloat(e.target.value))}
          disabled={disabled}
          aria-label="Balance between the syllable flow layer and the beat backbone"
          aria-valuetext={`${pct}% beat backbone`}
          className={`absolute inset-0 w-full cursor-pointer opacity-0 disabled:cursor-not-allowed ${focusRing}`}
        />
      </div>

      <div className="flex flex-wrap items-center gap-3 mt-3">
        {disabled ? (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-md bg-indigo-500/15 text-indigo-300 border border-indigo-500/20 whitespace-nowrap">
            <span className="h-3 w-3 rounded-full border-2 border-indigo-400/30 border-t-indigo-300 motion-safe:animate-spin" />
            Rendering percussion…
          </span>
        ) : (
          <button
            onClick={handleRerender}
            className={`bg-white text-black text-xs font-semibold px-4 py-1.5 rounded-full hover:bg-white/90 transition-colors ${focusRing}`}
          >
            Re-render
          </button>
        )}
        <span className="text-xs text-neutral-500">
          ~8s · reuses stems, syllables &amp; drum score
        </span>
      </div>

      {error && (
        <p className="text-sm text-red-400" role="alert">
          {error}{' '}
          <button onClick={handleRerender} className="underline hover:text-red-300">Try again</button>
        </p>
      )}
    </div>
  )
}
