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
      <div className="min-h-screen bg-black text-white selection:bg-white/20 selection:text-white flex flex-col overflow-x-hidden font-sans">
        <div className="fixed inset-0 bg-grid-white/[0.02] bg-[size:50px_50px]" />

        {/* Navigation */}
        <nav className="fixed top-0 w-full border-b border-white/5 bg-black/50 backdrop-blur-xl z-50 transition-all duration-300">
          <div className="max-w-7xl mx-auto px-6 h-20 flex items-center justify-between">
            <div className="flex items-center gap-3 group cursor-pointer">
              <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-indigo-500 to-cyan-400 flex items-center justify-center transform group-hover:scale-105 transition-all duration-300 shadow-[0_0_20px_rgba(99,102,241,0.3)]">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
                </svg>
              </div>
              <span className="font-bold text-xl tracking-tight text-white/90 group-hover:text-white transition-colors">FlowBeat</span>
            </div>
            <AuthForm className="scale-90 origin-right" />
          </div>
        </nav>

        <main className="flex-grow pt-32 pb-20 relative z-10 flex flex-col">
          {/* Hero Section */}
          <section className="relative px-6 pt-20 pb-32 max-w-5xl mx-auto w-full text-center flex flex-col items-center">
            <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[600px] bg-gradient-to-tr from-indigo-500/20 via-purple-500/10 to-cyan-500/20 blur-[120px] rounded-full pointer-events-none -z-10" />

            <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full bg-white/5 border border-white/10 mb-12 animate-[fade-in-up_1s_ease-out]">
              <span className="flex h-2 w-2 relative">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyan-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-cyan-500"></span>
              </span>
              <span className="text-sm font-medium text-white/80 tracking-wide uppercase">V2 Engine Now Live</span>
            </div>

            <h1 className="text-6xl md:text-8xl font-extrabold tracking-tight mb-8 leading-[1.1] animate-[fade-in-up_1s_ease-out_0.2s_both]">
              Your voice is the <br className="hidden md:block" />
              <span className="text-transparent bg-clip-text bg-gradient-to-r from-indigo-400 via-purple-400 to-cyan-400 animate-[gradient_8s_ease_infinite] bg-[length:200%_200%]">
                ultimate drum kit.
              </span>
            </h1>

            <p className="text-xl md:text-2xl text-neutral-400 mb-12 max-w-2xl font-light leading-relaxed animate-[fade-in-up_1s_ease-out_0.4s_both]">
              Transform any acapella rap into a studio-quality percussion track. Powered by next-gen AI separation and onset detection.
            </p>

            <div className="animate-[fade-in-up_1s_ease-out_0.6s_both]">
              <AuthForm className="transform scale-110" />
              <p className="mt-6 text-sm text-neutral-500 font-medium">No credit card required. Free tier forever.</p>
            </div>
          </section>

          {/* Feature Grid */}
          <section className="px-6 py-24 border-t border-white/5 bg-black/40 backdrop-blur-sm">
            <div className="max-w-7xl mx-auto grid grid-cols-1 md:grid-cols-3 gap-8">
              {/* Feature 1 */}
              <div className="p-8 rounded-3xl bg-white/[0.02] border border-white/5 hover:bg-white/[0.04] transition-all duration-500 group">
                <div className="w-14 h-14 rounded-2xl bg-indigo-500/10 flex items-center justify-center mb-6 border border-indigo-500/20 group-hover:scale-110 transition-transform duration-500">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-indigo-400">
                    <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
                  </svg>
                </div>
                <h3 className="text-2xl font-bold mb-3 text-white/90">Vocal Separation</h3>
                <p className="text-neutral-400 leading-relaxed font-light">
                  Industry-leading Demucs v4 isolates the purest transients from your flow, rejecting background noise perfectly.
                </p>
              </div>

              {/* Feature 2 */}
              <div className="p-8 rounded-3xl bg-white/[0.02] border border-white/5 hover:bg-white/[0.04] transition-all duration-500 group">
                <div className="w-14 h-14 rounded-2xl bg-cyan-500/10 flex items-center justify-center mb-6 border border-cyan-500/20 group-hover:scale-110 transition-transform duration-500">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-cyan-400">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" />
                  </svg>
                </div>
                <h3 className="text-2xl font-bold mb-3 text-white/90">Sub-millisecond Onsets</h3>
                <p className="text-neutral-400 leading-relaxed font-light">
                  Our proprietary Librosa pipeline detects syllables and plosives with hyper-accuracy, keeping your drums locked on grid.
                </p>
              </div>

              {/* Feature 3 */}
              <div className="p-8 rounded-3xl bg-white/[0.02] border border-white/5 hover:bg-white/[0.04] transition-all duration-500 group">
                <div className="w-14 h-14 rounded-2xl bg-purple-500/10 flex items-center justify-center mb-6 border border-purple-500/20 group-hover:scale-110 transition-transform duration-500">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-purple-400">
                    <path d="M9 18V5l12-2v13" />
                    <circle cx="6" cy="18" r="3" />
                    <circle cx="18" cy="16" r="3" />
                  </svg>
                </div>
                <h3 className="text-2xl font-bold mb-3 text-white/90">Instant Rendering</h3>
                <p className="text-neutral-400 leading-relaxed font-light">
                  Download crisp 808s and hi-hats mapped perfectly to your flow, ready to drop straight into Ableton or FL Studio.
                </p>
              </div>
            </div>
          </section>
        </main>

        {/* Footer */}
        <footer className="border-t border-white/5 py-12 text-center relative z-10 bg-black">
          <div className="flex items-center justify-center gap-2 mb-4 opacity-50">
            <div className="w-5 h-5 rounded-md bg-gradient-to-tr from-indigo-500 to-cyan-400 flex items-center justify-center">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
              </svg>
            </div>
            <span className="font-bold text-lg tracking-tight">FlowBeat</span>
          </div>
          <p className="text-neutral-500 text-sm">© 2026 FlowBeat Inc. All rights reserved.</p>
        </footer>
      </div>
    )
  }

  const jobs = await prisma.job.findMany({
    where: { userId: session.user.id },
    orderBy: { createdAt: 'desc' }
  });

  return (
    <div className="min-h-screen bg-black text-white selection:bg-white/20 selection:text-white font-sans flex flex-col">
      <div className="fixed inset-0 bg-grid-white/[0.02] bg-[size:50px_50px]" />

      {/* Navigation */}
      <nav className="fixed top-0 w-full border-b border-white/5 bg-black/50 backdrop-blur-xl z-50 transition-all duration-300">
        <div className="max-w-7xl mx-auto px-6 h-20 flex items-center justify-between">
          <div className="flex items-center gap-3 group cursor-pointer">
            <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-indigo-500 to-cyan-400 flex items-center justify-center transform group-hover:scale-105 transition-all duration-300 shadow-[0_0_20px_rgba(99,102,241,0.3)]">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
              </svg>
            </div>
            <span className="font-bold text-xl tracking-tight text-white/90 group-hover:text-white transition-colors">FlowBeat</span>
          </div>
          <div className="flex items-center gap-4">
            <div className="hidden sm:block text-sm text-neutral-400">
              Welcome back, <span className="text-white/80 font-medium">{session.user.name || session.user.email}</span>
            </div>
            <SignOutButton />
          </div>
        </div>
      </nav>

      <main className="flex-grow pt-32 pb-20 relative z-10 w-full max-w-7xl mx-auto px-6">
        <div className="mb-12">
          <h1 className="text-4xl md:text-5xl font-extrabold tracking-tight mb-4">
            Dashboard
          </h1>
          <p className="text-lg text-neutral-400">Create new tracks and manage your generated stems.</p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-12">
          {/* Upload Section */}
          <div className="lg:col-span-5 flex flex-col gap-6">
            <div className="bg-white/[0.02] border border-white/5 rounded-3xl p-8 shadow-2xl backdrop-blur-sm">
              <div className="flex items-center gap-3 mb-6">
                <div className="w-10 h-10 rounded-xl bg-indigo-500/10 flex items-center justify-center border border-indigo-500/20">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-indigo-400">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" />
                  </svg>
                </div>
                <h2 className="text-2xl font-bold">New Session</h2>
              </div>
              <UploadWidget />
            </div>
          </div>

          {/* Jobs List Section */}
          <div className="lg:col-span-7 flex flex-col gap-6">
            <div className="bg-white/[0.02] border border-white/5 rounded-3xl p-8 shadow-2xl backdrop-blur-sm min-h-[500px]">
              <div className="flex items-center gap-3 mb-6">
                <div className="w-10 h-10 rounded-xl bg-cyan-500/10 flex items-center justify-center border border-cyan-500/20">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-cyan-400">
                    <path d="M9 18V5l12-2v13" />
                    <circle cx="6" cy="18" r="3" />
                    <circle cx="18" cy="16" r="3" />
                  </svg>
                </div>
                <h2 className="text-2xl font-bold">Your Tracks</h2>
              </div>

              {jobs.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-[300px] text-center border-2 border-dashed border-white/5 rounded-2xl bg-white/[0.01]">
                  <p className="text-neutral-500 mb-2">No tracks generated yet.</p>
                  <p className="text-sm text-neutral-600">Upload an acapella to get started.</p>
                </div>
              ) : (
                <JobList initialJobs={jobs} />
              )}
            </div>
          </div>
        </div>
      </main>
    </div>
  )
}
