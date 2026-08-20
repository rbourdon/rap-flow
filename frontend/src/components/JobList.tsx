'use client'

import { useEffect } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { ClientDate } from '@/components/ClientDate'

interface Job {
  id: string
  createdAt: Date
  sourceType: string
  sourceUrl: string | null
  status: string
}

export function JobList({ initialJobs }: { initialJobs: Job[] }) {
  const jobs = initialJobs
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


  if (jobs.length === 0) {
    return <p>No jobs found.</p>
  }

  return (
    <ul className="flex flex-col gap-2">
      {jobs.map((job) => (
        <li key={job.id} className="border rounded-lg bg-white shadow-sm hover:bg-gray-50 transition">
          <Link href={`/jobs/${job.id}`} className="p-4 flex justify-between items-center w-full">
            <div>
              <span className="font-medium text-sm text-gray-500">
                <ClientDate date={job.createdAt} />
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
            </div>
          </Link>
        </li>
      ))}
    </ul>
  )
}
