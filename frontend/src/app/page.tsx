import { prisma } from '@/lib/db'
import { auth } from '@/lib/auth'
import { headers } from 'next/headers'
import { UploadWidget } from '@/components/Upload'
import { JobList } from '@/components/JobList'
import { AuthForm } from '@/components/AuthForm'
import { SignOutButton } from '@/components/SignOutButton'

export default async function Home() {
  const session = await auth.api.getSession({
    headers: await headers()
  });

  if (!session?.user) {
    return (
      <main className="p-8 max-w-4xl mx-auto flex flex-col items-center justify-center min-h-[60vh]">
        <h1 className="text-2xl font-bold mb-8">Rap Flow → Percussion Track Generator</h1>
        <AuthForm />
      </main>
    )
  }

  const jobs = await prisma.job.findMany({
    where: { userId: session.user.id },
    orderBy: { createdAt: 'desc' }
  });

  return (
    <main className="p-8 max-w-4xl mx-auto">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold">Rap Flow → Percussion Track Generator</h1>
        <SignOutButton />
      </div>

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
