'use client'

import { useState } from 'react'
import { retryJob } from '@/app/actions'

export function RetryButton({ jobId }: { jobId: string }) {
  const [isRetrying, setIsRetrying] = useState(false)

  const handleRetry = async () => {
    setIsRetrying(true)
    try {
      await retryJob(jobId)
    } catch (error) {
      console.error('Failed to retry job:', error)
      alert('Failed to retry job')
      setIsRetrying(false)
    }
  }

  return (
    <button
      onClick={handleRetry}
      disabled={isRetrying}
      className="bg-indigo-600 text-white px-4 py-2 rounded-lg font-medium hover:bg-indigo-700 transition disabled:opacity-50"
    >
      {isRetrying ? 'Retrying...' : 'Retry Job'}
    </button>
  )
}
