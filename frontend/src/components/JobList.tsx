'use client'

import { useEffect } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { ClientDate } from '@/components/ClientDate'
import { DeleteJobButton } from '@/components/DeleteJobButton'
import { formatDuration } from '@/lib/format'
import { STAGE_ACTIVE_LABELS, normalizeStage } from '@/app/workflow-stages'
import { humanizeJobError } from '@/lib/errors'

interface Job {
  id: string
  createdAt: Date
  sourceType: string
  sourceUrl: string | null
  status: string
  stage: string | null
  title: string | null
  thumbnailUrl: string | null
  durationSec: number | null
  error: string | null
}

function jobTitle(job: Job): string {
  if (job.title) return job.title
  if (job.sourceType === 'URL' && job.sourceUrl) return job.sourceUrl
  return 'File upload'
}

function MusicNotePlaceholder() {
  return (
    <div className="w-12 h-12 flex-shrink-0 rounded-lg bg-white/5 border border-white/10 flex items-center justify-center text-neutral-500">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M9 18V5l12-2v13" />
        <circle cx="6" cy="18" r="3" />
        <circle cx="18" cy="16" r="3" />
      </svg>
    </div>
  )
}

function StatusChip({ job }: { job: Job }) {
  if (job.status === 'COMPLETED') {
    return (
      <span className="px-2.5 py-1 text-xs font-bold rounded-md uppercase tracking-wider bg-green-500/20 text-green-400 border border-green-500/20">
        Completed
      </span>
    )
  }
  if (job.status === 'FAILED') {
    const friendly = humanizeJobError(job.error)
    return (
      <span
        title={friendly.message + ' ' + friendly.action}
        className="px-2.5 py-1 text-xs font-bold rounded-md uppercase tracking-wider bg-red-500/20 text-red-400 border border-red-500/20"
      >
        Failed
      </span>
    )
  }
  // PENDING / PROCESSING: show the current stage label + a subtle spinner.
  const stageId = normalizeStage(job.stage)
  const label = stageId ? STAGE_ACTIVE_LABELS[stageId] : 'Queued…'
  return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-md bg-indigo-500/15 text-indigo-300 border border-indigo-500/20 whitespace-nowrap">
      <span className="h-3 w-3 rounded-full border-2 border-indigo-400/30 border-t-indigo-300 motion-safe:animate-spin" />
      {label}
    </span>
  )
}

export function JobList({ initialJobs }: { initialJobs: Job[] }) {
  const jobs = initialJobs
  const router = useRouter()

  useEffect(() => {
    // Poll while any job is still in flight — PENDING *or* PROCESSING. (The old
    // code only polled on PENDING, so a PROCESSING job never refreshed.)
    const hasActive = jobs.some(j => j.status === 'PENDING' || j.status === 'PROCESSING')
    if (!hasActive) return

    const intervalId = setInterval(() => {
      router.refresh()
    }, 5000)

    return () => clearInterval(intervalId)
  }, [jobs, router])

  if (jobs.length === 0) {
    return <p className="text-neutral-400">No jobs found.</p>
  }

  return (
    <ul className="flex flex-col gap-3">
      {jobs.map((job) => {
        const title = jobTitle(job)
        const duration = formatDuration(job.durationSec)
        return (
          <li key={job.id} className="border border-white/5 rounded-xl bg-white/[0.02] shadow-sm hover:bg-white/[0.04] transition group">
            <div className="p-3 sm:p-4 flex items-center gap-3">
              <Link href={`/jobs/${job.id}`} className="flex-1 min-w-0 flex items-center gap-3">
                {job.thumbnailUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={job.thumbnailUrl}
                    alt=""
                    className="w-12 h-12 flex-shrink-0 rounded-lg object-cover border border-white/10 bg-white/5"
                  />
                ) : (
                  <MusicNotePlaceholder />
                )}
                <div className="min-w-0">
                  <div className="text-base sm:text-lg text-white/90 group-hover:text-white transition-colors truncate" title={title}>
                    {title}
                  </div>
                  <div className="flex items-center gap-2 text-xs text-neutral-400">
                    {duration && <span className="tabular-nums">{duration}</span>}
                    {duration && <span aria-hidden>·</span>}
                    <ClientDate date={job.createdAt} relative />
                  </div>
                </div>
              </Link>
              <div className="flex items-center gap-2 sm:gap-3 flex-shrink-0">
                <StatusChip job={job} />
                <DeleteJobButton jobId={job.id} variant="icon" />
              </div>
            </div>
          </li>
        )
      })}
    </ul>
  )
}
