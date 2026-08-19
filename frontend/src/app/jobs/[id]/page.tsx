import { prisma } from '@/lib/db'
import { auth } from '@/lib/auth'
import { headers } from 'next/headers'
import { notFound } from 'next/navigation'
import Link from 'next/link'
import { WaveSurferPlayer } from './WaveSurferPlayer'

export default async function JobDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await auth.api.getSession({
    headers: await headers()
  });

  if (!session?.user) {
    return <div>Unauthorized</div>
  }

  const { id } = await params;

  const job = await prisma.job.findUnique({
    where: { id }
  });

  if (!job || job.userId !== session.user.id) {
    notFound();
  }

  // Fetch events JSON if completed
  let events = [];
  if (job.status === 'COMPLETED' && job.eventsBlobUrl) {
    try {
      // Use standard fetch
      const res = await fetch(job.eventsBlobUrl);
      if (res.ok) {
        events = await res.json();
      }
    } catch (e) {
      console.error('Failed to fetch events', e);
    }
  }

  return (
    <div className="min-h-screen bg-black text-white selection:bg-white/20 selection:text-white font-sans flex flex-col">
      <div className="fixed inset-0 bg-grid-white/[0.02] bg-[size:50px_50px]" />
      <main className="flex-grow pt-32 pb-20 relative z-10 w-full max-w-7xl mx-auto px-6">
      <Link href="/" className="text-indigo-400 hover:underline mb-4 inline-block">&larr; Back to Jobs</Link>

      <h1 className="text-4xl md:text-5xl font-extrabold tracking-tight mb-4">Job Details</h1>

      <div className="bg-white/[0.02] border border-white/5 rounded-3xl p-8 shadow-2xl backdrop-blur-sm mb-8 text-neutral-400">
        <p><strong className="text-white/80">Status:</strong> {job.status}</p>
        <p><strong className="text-white/80">Source:</strong> {job.sourceType === 'URL' ? job.sourceUrl : 'Upload'}</p>
        <p><strong className="text-white/80">Created:</strong> {new Date(job.createdAt).toLocaleString()}</p>
        {job.error && <p className="text-red-500"><strong className="text-white/80">Error:</strong> {job.error}</p>}
      </div>

      {job.status === 'COMPLETED' && job.resultBlobUrl && (
        <div className="mt-8 bg-white/[0.02] border border-white/5 rounded-3xl p-8 shadow-2xl backdrop-blur-sm">
          <h2 className="text-xl font-semibold mb-4">Result Mix</h2>

          <WaveSurferPlayer audioUrl={job.resultBlobUrl} events={events} />

          <div className="mt-6">
            <a href={job.resultBlobUrl} download className="bg-green-500 text-white px-4 py-2 rounded font-medium hover:bg-green-600 transition">
              Download Audio
            </a>
          </div>
        </div>
      )}
    </main>
    </div>
  )
}
