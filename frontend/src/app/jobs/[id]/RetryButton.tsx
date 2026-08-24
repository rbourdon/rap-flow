'use client'

import { useState } from 'react'
import { retryJob } from '@/app/actions'
import { btnPrimary } from '@/lib/ui'

export function RetryButton({ jobId }: { jobId: string }) {
  const [isRetrying, setIsRetrying] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleRetry = async () => {
    setIsRetrying(true)
    setError(null)
    try {
      await retryJob(jobId)
    } catch (err) {
      // Surface the failure inline instead of a browser alert().
      setError(err instanceof Error ? err.message : 'Failed to retry job.')
      setIsRetrying(false)
    }
  }

  return (
    <div className="flex flex-col items-start gap-2">
      <button
        onClick={handleRetry}
        disabled={isRetrying}
        className={`${btnPrimary} px-4 py-2`}
      >
        {isRetrying ? 'Retrying…' : 'Retry Job'}
      </button>
      {error && (
        <p className="text-sm text-red-400" role="alert">
          {error}{' '}
          <button onClick={handleRetry} className="underline hover:text-red-300">Try again</button>
        </p>
      )}
    </div>
  )
}
