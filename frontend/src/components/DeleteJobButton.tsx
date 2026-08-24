'use client'

import { useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import { deleteJob } from '@/app/actions'

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
  const [isPending, startTransition] = useTransition()
  const [error, setError] = useState(false)

  const handleDelete = () => {
    if (!window.confirm('Delete this track? This permanently removes it and all of its audio files. This cannot be undone.')) {
      return
    }

    setError(false)
    startTransition(async () => {
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
      }
    })
  }

  if (variant === 'full') {
    return (
      <button
        onClick={handleDelete}
        disabled={isPending}
        className="inline-flex items-center gap-2 bg-red-600/90 text-white px-4 py-2 rounded-lg font-medium hover:bg-red-600 transition disabled:opacity-50"
      >
        <TrashIcon />
        {isPending ? 'Deleting...' : error ? 'Failed - Retry' : 'Delete Job'}
      </button>
    )
  }

  return (
    <button
      type="button"
      onClick={handleDelete}
      disabled={isPending}
      aria-label="Delete track"
      title={error ? 'Delete failed - try again' : 'Delete track'}
      className={`flex-shrink-0 p-2 rounded-lg border transition disabled:opacity-50 ${
        error
          ? 'border-red-500/40 text-red-400 bg-red-500/10'
          : 'border-white/10 text-neutral-400 hover:text-red-400 hover:border-red-500/30 hover:bg-red-500/10'
      }`}
    >
      {isPending ? <SpinnerIcon /> : <TrashIcon />}
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

function SpinnerIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="animate-spin">
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
    </svg>
  )
}
