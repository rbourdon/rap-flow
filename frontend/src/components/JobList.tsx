'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'

interface Job {
  id: string
  createdAt: Date
  sourceType: string
  sourceUrl: string | null
  status: string
}

export function JobList({ initialJobs }: { initialJobs: Job[] }) {
  const [jobs, setJobs] = useState<Job[]>(initialJobs)
  const router = useRouter()

  useEffect(() => {
    // If any jobs are pending, poll every 5 seconds
    const hasPending = jobs.some(j => j.status === 'PENDING')
    if (!hasPending) return

    const intervalId = setInterval(() => {
      // In a real app we'd fetch an API route to get updated jobs, but for simplicity we just trigger a router refresh
      router.refresh()
    }, 5000)

    return () => clearInterval(intervalId)
  }, [jobs, router])

  // update local state when props change
  useEffect(() => {
    setJobs(initialJobs)
  }, [initialJobs])

  if (jobs.length === 0) {
    return <p>No jobs found.</p>
  }

  return (
    <ul className="flex flex-col gap-2">
      {jobs.map((job) => (
        <li key={job.id} className="border p-4 rounded-lg flex justify-between items-center bg-white shadow-sm">
          <div>
            <span className="font-medium text-sm text-gray-500">
              {new Date(job.createdAt).toLocaleString()}
            </span>
            <div className="text-lg text-black truncate max-w-[200px]" title={job.sourceType === 'URL' ? (job.sourceUrl || '') : 'File Upload'}>
              {job.sourceType === 'URL' ? (job.sourceUrl || '') : 'File Upload'}
            </div>
          </div>
          <div className="flex items-center gap-4">
            <span className={`px-2 py-1 text-xs rounded text-white ${
              job.status === 'COMPLETED' ? 'bg-green-500' :
              job.status === 'FAILED' ? 'bg-red-500' :
              'bg-yellow-500'
            }`}>
              {job.status}
            </span>
            {job.status === 'COMPLETED' && (
              <Link href={`/jobs/${job.id}`} className="text-blue-500 hover:underline">
                View
              </Link>
            )}
          </div>
        </li>
      ))}
    </ul>
  )
}
