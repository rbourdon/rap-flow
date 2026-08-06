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
    <main className="p-8 max-w-4xl mx-auto">
      <Link href="/" className="text-blue-500 hover:underline mb-4 inline-block">&larr; Back to Jobs</Link>

      <h1 className="text-2xl font-bold mb-4">Job Details</h1>

      <div className="bg-gray-50 border p-4 rounded-lg mb-8">
        <p><strong>Status:</strong> {job.status}</p>
        <p><strong>Source:</strong> {job.sourceType === 'URL' ? job.sourceUrl : 'Upload'}</p>
        <p><strong>Created:</strong> {new Date(job.createdAt).toLocaleString()}</p>
        {job.error && <p className="text-red-500"><strong>Error:</strong> {job.error}</p>}
      </div>

      {job.status === 'COMPLETED' && job.resultBlobUrl && (
        <div className="mt-8 border p-4 rounded-lg">
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
  )
}
