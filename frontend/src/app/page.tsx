import { prisma } from '@/lib/db'
import { auth } from '@/lib/auth'
import { headers } from 'next/headers'
import { UploadWidget } from '@/components/Upload'
import { JobList } from '@/components/JobList'

export default async function Home() {
  const session = await auth.api.getSession({
    headers: await headers()
  });

  if (!session?.user) {
    return (
      <main className="p-8">
        <h1 className="text-2xl font-bold mb-4">Rap Flow → Percussion Track Generator</h1>
        <p className="mb-4">Please sign in to continue.</p>
      </main>
    )
  }

  const jobs = await prisma.job.findMany({
    where: { userId: session.user.id },
    orderBy: { createdAt: 'desc' }
  });

  return (
    <main className="p-8 max-w-4xl mx-auto">
      <h1 className="text-2xl font-bold mb-6">Rap Flow → Percussion Track Generator</h1>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
        <div>
          <h2 className="text-xl font-semibold mb-4">Create New Job</h2>
          <UploadWidget />
        </div>

        <div>
          <h2 className="text-xl font-semibold mb-4">Your Jobs</h2>
          <JobList initialJobs={jobs} />
        </div>
      </div>
    </main>
  )
}
