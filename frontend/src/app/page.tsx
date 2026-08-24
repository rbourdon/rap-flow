import { prisma } from '@/lib/db'
import { auth } from '@/lib/auth'
import { headers } from 'next/headers'
import { UploadWidget } from '@/components/Upload'
import { JobList } from '@/components/JobList'
import { AuthForm } from '@/components/AuthForm'

const GITHUB_URL = 'https://github.com/rbourdon/rap-flow'

function Footer() {
  return (
    <footer className="border-t border-white/5 py-8 text-center relative z-10 bg-black">
      <p className="text-sm text-neutral-400">
        rap-flow ·{' '}
        <a
          href={GITHUB_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="text-indigo-400 hover:underline"
        >
          GitHub
        </a>
      </p>
    </footer>
  )
}

const FEATURES = [
  {
    title: 'Separate',
    body: 'Demucs splits the track into vocals and instrumental so the flow can be analyzed on its own.',
    color: 'text-indigo-400',
    ring: 'border-indigo-500/20 bg-indigo-500/10',
    icon: (
      <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3ZM19 10v2a7 7 0 0 1-14 0v-2M12 19v3" />
    ),
  },
  {
    title: 'Detect',
    body: "Onset detection finds each syllable in the vocal, marking where the percussion should hit.",
    color: 'text-cyan-400',
    ring: 'border-cyan-500/20 bg-cyan-500/10',
    icon: <path d="M3 12h4l3 8 4-16 3 8h4" />,
  },
  {
    title: 'Render',
    body: "Those onsets become a percussion track, quantized to the song's grid and mixed back with the instrumental.",
    color: 'text-purple-400',
    ring: 'border-purple-500/20 bg-purple-500/10',
    icon: (
      <path d="M9 18V5l12-2v13M6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM18 19a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" />
    ),
  },
]

export default async function Home() {
  const session = await auth.api.getSession({
    headers: await headers()
  });

  if (!session?.user) {
    return (
      <div className="min-h-screen bg-black text-white selection:bg-white/20 selection:text-white flex flex-col overflow-x-hidden font-sans">
        <div className="fixed inset-0 bg-grid-white/[0.02] bg-[size:50px_50px]" />

        <main className="flex-grow pt-24 sm:pt-32 pb-20 relative z-10 flex flex-col">
          {/* Hero Section */}
          <section className="relative px-4 sm:px-6 pt-12 sm:pt-20 pb-20 sm:pb-32 max-w-5xl mx-auto w-full text-center flex flex-col items-center">
            <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-[800px] h-[600px] bg-gradient-to-tr from-indigo-500/20 via-purple-500/10 to-cyan-500/20 blur-[120px] rounded-full pointer-events-none -z-10" />

            <h1 className="text-4xl sm:text-5xl md:text-7xl font-extrabold tracking-tight mb-6 sm:mb-8 leading-[1.1]">
              Turn vocal flow into <br className="hidden md:block" />
              <span className="text-transparent bg-clip-text bg-gradient-to-r from-indigo-400 via-purple-400 to-cyan-400">
                percussion.
              </span>
            </h1>

            <p className="text-lg sm:text-xl md:text-2xl text-neutral-300 mb-10 sm:mb-12 max-w-2xl font-light leading-relaxed">
              rap-flow separates a song&apos;s vocals with Demucs, detects the syllable onsets,
              and renders them as a percussion track mapped to the song&apos;s grid.
            </p>

            <div>
              <AuthForm className="transform scale-110" />
            </div>
          </section>

          {/* Feature Grid — the real pipeline stages */}
          <section className="px-4 sm:px-6 py-16 sm:py-24 border-t border-white/5 bg-black/40 backdrop-blur-sm">
            <div className="max-w-7xl mx-auto grid grid-cols-1 md:grid-cols-3 gap-6 sm:gap-8">
              {FEATURES.map((f) => (
                <div key={f.title} className="p-6 sm:p-8 rounded-3xl bg-white/[0.02] border border-white/5 hover:bg-white/[0.04] transition-colors group">
                  <div className={`w-14 h-14 rounded-2xl flex items-center justify-center mb-6 border ${f.ring}`}>
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={f.color}>
                      {f.icon}
                    </svg>
                  </div>
                  <h3 className="text-2xl font-bold mb-3 text-white/90">{f.title}</h3>
                  <p className="text-neutral-300 leading-relaxed font-light">{f.body}</p>
                </div>
              ))}
            </div>
          </section>
        </main>

        <Footer />
      </div>
    )
  }

  const jobs = await prisma.job.findMany({
    where: { userId: session.user.id },
    orderBy: { createdAt: 'desc' }
  });

  return (
    <div className="min-h-screen bg-black text-white selection:bg-white/20 selection:text-white font-sans flex flex-col overflow-x-hidden">
      <div className="fixed inset-0 bg-grid-white/[0.02] bg-[size:50px_50px]" />

      <main className="flex-grow pt-24 sm:pt-32 pb-20 relative z-10 w-full max-w-7xl mx-auto px-4 sm:px-6">
        <div className="mb-8 sm:mb-12">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-extrabold tracking-tight mb-3 sm:mb-4">
            Dashboard
          </h1>
          <p className="text-base sm:text-lg text-neutral-300">Create new tracks and manage your generated stems.</p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 lg:gap-12">
          {/* Upload Section */}
          <div className="lg:col-span-5 flex flex-col gap-6">
            <div className="bg-white/[0.02] border border-white/5 rounded-3xl p-5 sm:p-8 shadow-2xl backdrop-blur-sm">
              <div className="flex items-center gap-3 mb-6">
                <div className="w-10 h-10 rounded-xl bg-indigo-500/10 flex items-center justify-center border border-indigo-500/20">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-indigo-400">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" />
                  </svg>
                </div>
                <h2 className="text-2xl font-bold">New track</h2>
              </div>
              <UploadWidget />
            </div>
          </div>

          {/* Jobs List Section */}
          <div className="lg:col-span-7 flex flex-col gap-6">
            <div className="bg-white/[0.02] border border-white/5 rounded-3xl p-5 sm:p-8 shadow-2xl backdrop-blur-sm min-h-[400px] sm:min-h-[500px]">
              <div className="flex items-center gap-3 mb-6">
                <div className="w-10 h-10 rounded-xl bg-cyan-500/10 flex items-center justify-center border border-cyan-500/20">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-cyan-400">
                    <path d="M9 18V5l12-2v13" />
                    <circle cx="6" cy="18" r="3" />
                    <circle cx="18" cy="16" r="3" />
                  </svg>
                </div>
                <h2 className="text-2xl font-bold">Your tracks</h2>
              </div>

              {jobs.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-[300px] text-center border-2 border-dashed border-white/10 rounded-2xl bg-white/[0.01] px-6">
                  <p className="text-neutral-300 mb-4">No tracks yet — start one with the panel on the left.</p>
                  <ol className="text-sm text-neutral-400 flex flex-col sm:flex-row gap-2 sm:gap-4">
                    <li><span className="text-indigo-300 font-medium">1.</span> Paste a URL or drop audio</li>
                    <li><span className="text-indigo-300 font-medium">2.</span> We separate &amp; detect onsets</li>
                    <li><span className="text-indigo-300 font-medium">3.</span> Play back the percussion</li>
                  </ol>
                </div>
              ) : (
                <JobList initialJobs={jobs} />
              )}
            </div>
          </div>
        </div>
      </main>

      <Footer />
    </div>
  )
}
