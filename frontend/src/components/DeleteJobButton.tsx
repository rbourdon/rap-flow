'use client'

import { useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { deleteJob } from '@/app/actions'
import { focusRing } from '@/lib/ui'

interface DeleteJobButtonProps {
  jobId: string
  // 'icon' renders a compact trash button (used inside the job list); 'full'
  // renders a labelled button (used on the job detail page).
  variant?: 'icon' | 'full'
  // Where to send the user after a successful delete. Defaults to a refresh.
  redirectTo?: string
}

export function DeleteJobButton({ jobId, variant = 'icon', redirectTo }: DeleteJobButtonProps) {
  const router = useRouter()
  const [isPending, setIsPending] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState(false)
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
    }
  }, [])

  // First click arms the confirm; a second click within the window deletes.
  // Replaces the old window.confirm() dialog with a two-step inline button.
  const handleClick = () => {
    if (isPending) return
    if (!confirming) {
      setError(false)
      setConfirming(true)
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
      timeoutRef.current = setTimeout(() => setConfirming(false), 4000)
      return
    }

    if (timeoutRef.current) clearTimeout(timeoutRef.current)
    setConfirming(false)
    setError(false)
    setIsPending(true)
    ;(async () => {
      try {
        await deleteJob(jobId)
        if (redirectTo) {
          router.push(redirectTo)
        } else {
          router.refresh()
        }
      } catch (err) {
        console.error('Failed to delete job:', err)
        setError(true)
        setIsPending(false)
      }
    })()
  }

  if (variant === 'full') {
    return (
      <div className="flex flex-col items-start gap-1">
        <button
          onClick={handleClick}
          disabled={isPending}
          className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg font-medium transition disabled:opacity-50 ${focusRing} ${
            confirming ? 'bg-red-600 text-white' : 'bg-red-600/90 hover:bg-red-600 text-white'
          }`}
        >
          <TrashIcon />
          {isPending ? 'Deleting…' : confirming ? 'Confirm delete?' : 'Delete Job'}
        </button>
        {error && <p className="text-sm text-red-400" role="alert">Delete failed. Try again.</p>}
      </div>
    )
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={isPending}
      aria-label={confirming ? 'Confirm delete track' : 'Delete track'}
      title={error ? 'Delete failed - try again' : confirming ? 'Click again to confirm' : 'Delete track'}
      className={`flex-shrink-0 p-2 rounded-lg border transition disabled:opacity-50 ${focusRing} ${
        error || confirming
          ? 'border-red-500/40 text-red-400 bg-red-500/10'
          : 'border-white/10 text-neutral-400 hover:text-red-400 hover:border-red-500/30 hover:bg-red-500/10'
      }`}
    >
      {isPending ? <SpinnerIcon /> : confirming ? <CheckIcon /> : <TrashIcon />}
    </button>
  )
}

function TrashIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 6h18" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <line x1="10" x2="10" y1="11" y2="17" />
      <line x1="14" x2="14" y1="11" y2="17" />
    </svg>
  )
}

function CheckIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 13l4 4L19 7" />
    </svg>
  )
}

function SpinnerIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="animate-spin">
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
    </svg>
  )
}
