import { prisma } from '@/lib/db'
import { auth } from '@/lib/auth'
import { headers } from 'next/headers'
import { notFound, redirect } from 'next/navigation'
import Link from 'next/link'
import { WaveSurferPlayer } from './WaveSurferPlayer'
import { RetryButton } from './RetryButton'
import { ReprocessControls } from './ReprocessControls'
import { ClientDate } from '@/components/ClientDate'
import { JobStatusTracker } from './JobStatusTracker'
import { DeleteJobButton } from '@/components/DeleteJobButton'
import { humanizeJobError } from '@/lib/errors'
import { formatDuration } from '@/lib/format'
import { card, btnSecondary } from '@/lib/ui'

export default async function JobDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await auth.api.getSession({
    headers: await headers()
  });

  if (!session?.user) {
    redirect('/')
  }

  const { id } = await params;

  const job = await prisma.job.findUnique({
    where: { id }
  });

  if (!job || job.userId !== session.user.id) {
    notFound();
  }

  // Fetch events JSON if completed. The blob is stored with private access,
  // so it must be fetched with the read/write token as a bearer token
  // rather than requested directly by the browser.
  let events = [];
  if (job.status === 'COMPLETED' && job.eventsBlobUrl) {
    try {
      const token = process.env.BLOB_READ_WRITE_TOKEN;
      const res = await fetch(job.eventsBlobUrl, {
        headers: token ? { authorization: 'Bearer ' + token } : {},
      });
      if (res.ok) {
        events = await res.json();
      }
    } catch (e) {
      console.error('Failed to fetch events', e);
    }
  }

  const assetUrl = (type: string) => `/api/jobs/${job.id}/asset?type=${type}`

  // Prefer compressed Opus copies for playback; fall back to WAV for old jobs.
  const waveformUrl = job.percOpusBlobUrl
    ? assetUrl('percOpus')
    : job.mixOpusBlobUrl
    ? assetUrl('mixOpus')
    : assetUrl('mix')
  const percStreamUrl = job.percOpusBlobUrl
    ? assetUrl('percOpus')
    : job.percBlobUrl
    ? assetUrl('perc')
    : undefined
  const instStreamUrl = job.instOpusBlobUrl
    ? assetUrl('instOpus')
    : job.instBlobUrl
    ? assetUrl('inst')
    : undefined

  const downloads = [
    { label: 'Mix (WAV)', type: 'mix', present: !!job.resultBlobUrl },
    { label: 'Percussion (WAV)', type: 'perc', present: !!job.percBlobUrl },
    { label: 'Instrumental (WAV)', type: 'inst', present: !!job.instBlobUrl },
    { label: 'MIDI', type: 'midi', present: !!job.midBlobUrl },
  ].filter((d) => d.present)

  const title = job.title || (job.sourceType === 'URL' ? job.sourceUrl : null) || 'File upload'
  const duration = formatDuration(job.durationSec)
  const friendlyError = job.error ? humanizeJobError(job.error) : null

  return (
    <div className="min-h-screen bg-black text-white selection:bg-white/20 selection:text-white font-sans flex flex-col overflow-x-hidden">
      <div className="fixed inset-0 bg-grid-white/[0.02] bg-[size:50px_50px]" />
      <main className="flex-grow pt-24 sm:pt-32 pb-20 relative z-10 w-full max-w-7xl mx-auto px-4 sm:px-6">
        <Link href="/" className="text-indigo-400 hover:underline mb-6 inline-flex items-center gap-1 rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 focus-visible:ring-offset-black">
          <span aria-hidden>&larr;</span> Back to Jobs
        </Link>

        {/* Track identity header */}
        <div className="flex items-start gap-4 mb-8">
          {job.thumbnailUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={job.thumbnailUrl} alt="" className="w-20 h-20 sm:w-24 sm:h-24 rounded-xl object-cover border border-white/10 bg-white/5 flex-shrink-0" />
          ) : (
            <div className="w-20 h-20 sm:w-24 sm:h-24 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center text-neutral-500 flex-shrink-0">
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 18V5l12-2v13" /><circle cx="6" cy="18" r="3" /><circle cx="18" cy="16" r="3" />
              </svg>
            </div>
          )}
          <div className="min-w-0">
            <h1 className="text-2xl sm:text-3xl md:text-4xl font-extrabold tracking-tight break-words">{title}</h1>
            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-neutral-400">
              <span><span className="text-white/70">Status:</span> {job.status}</span>
              {job.uploader && <span><span className="text-white/70">By:</span> {job.uploader}</span>}
              {duration && <span className="tabular-nums">{duration}</span>}
              <span><span className="text-white/70">Created:</span> <ClientDate date={job.createdAt} /></span>
              {job.sourceType === 'URL' && job.sourceUrl && (
                <a href={job.sourceUrl} target="_blank" rel="noopener noreferrer" className="text-indigo-400 hover:underline">Source ↗</a>
              )}
            </div>
          </div>
        </div>

        <div className={`${card} p-5 sm:p-8 mb-8 text-neutral-400`}>
          <div className="flex flex-col sm:flex-row sm:justify-between sm:items-start gap-4">
            <div className="min-w-0 space-y-2">
              {friendlyError && (
                <div className="text-red-300">
                  <p className="font-medium text-red-400">{friendlyError.message}</p>
                  <p className="text-sm text-red-300/80">{friendlyError.action}</p>
                  <details className="mt-2 text-xs text-neutral-500">
                    <summary className="cursor-pointer hover:text-neutral-300">Technical details</summary>
                    <p className="mt-1 break-words font-mono">{job.error}</p>
                  </details>
                </div>
              )}
              {!friendlyError && (
                <p className="text-sm text-neutral-400">Manage this track below.</p>
              )}
            </div>
            {job.status === 'FAILED' && (
              <div className="flex-shrink-0">
                <RetryButton jobId={job.id} />
              </div>
            )}
          </div>

          <div className="mt-6 pt-6 border-t border-white/5">
            <DeleteJobButton jobId={job.id} variant="full" redirectTo="/" />
          </div>
        </div>

        {(job.status === 'PENDING' || job.status === 'PROCESSING') && (
          <JobStatusTracker
            jobId={job.id}
            initialStatus={job.status}
            initialStage={job.stage}
            createdAt={job.createdAt}
            initialStageStates={job.stageStates as Record<string, { state?: string; reused?: boolean; updatedAt?: string }> | null}
          />
        )}

        {job.status === 'COMPLETED' && job.resultBlobUrl && (
          <div className={`mt-8 ${card} p-5 sm:p-8`}>
            <h2 className="text-xl font-semibold mb-4">Result Mix</h2>

            <WaveSurferPlayer
              waveformUrl={waveformUrl}
              percUrl={percStreamUrl}
              instUrl={instStreamUrl}
              events={events}
            />

            {downloads.length > 0 && (
              <div className="mt-6">
                <h3 className="text-sm font-medium text-neutral-400 mb-2">Download</h3>
                <div className="flex flex-wrap gap-2">
                  {downloads.map((d) => (
                    <a
                      key={d.type}
                      href={assetUrl(d.type)}
                      download
                      className={`${btnSecondary} px-4 py-2 text-sm`}
                    >
                      {d.label}
                    </a>
                  ))}
                </div>
              </div>
            )}

            <div className="mt-6 pt-6 border-t border-white/5">
              <ReprocessControls jobId={job.id} />
            </div>
          </div>
        )}

        {job.status === 'COMPLETED' && !job.resultBlobUrl && (
          <div className={`mt-8 ${card} p-5 sm:p-8 text-neutral-400`}>
            <p className="font-medium text-red-400">
              {humanizeJobError('UPLOAD_FAILED').message}
            </p>
            <p className="text-sm text-red-300/80">This job finished but produced no audio. {humanizeJobError('UPLOAD_FAILED').action}</p>
            <div className="mt-4">
              <RetryButton jobId={job.id} />
            </div>
          </div>
        )}
      </main>
    </div>
  )
}
