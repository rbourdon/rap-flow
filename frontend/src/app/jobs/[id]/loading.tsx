export default function Loading() {
  return (
    <div className="min-h-screen bg-black text-white selection:bg-white/20 selection:text-white font-sans flex flex-col">
      <div className="fixed inset-0 bg-grid-white/[0.02] bg-[size:50px_50px]" />
      <main className="flex-grow pt-20 md:pt-32 pb-20 relative z-10 w-full max-w-7xl mx-auto px-4 sm:px-6">
        <div className="text-indigo-400 mb-4 inline-block opacity-50">&larr; Back to Jobs</div>

        <div className="h-12 w-64 max-w-full bg-white/5 rounded-lg mb-4 animate-pulse"></div>

        <div className="bg-white/[0.02] border border-white/5 rounded-3xl p-5 sm:p-8 shadow-2xl backdrop-blur-sm mb-8 animate-pulse">
          <div className="flex flex-col gap-3">
            <div className="h-6 w-48 max-w-full bg-white/5 rounded-md"></div>
            <div className="h-6 w-96 max-w-full bg-white/5 rounded-md"></div>
            <div className="h-6 w-64 max-w-full bg-white/5 rounded-md"></div>
          </div>
        </div>

        <div className="mt-8 bg-white/[0.02] border border-white/5 rounded-3xl p-5 sm:p-8 shadow-2xl backdrop-blur-sm animate-pulse">
          <div className="h-8 w-48 max-w-full bg-white/5 rounded-lg mb-6"></div>
          <div className="h-[200px] w-full bg-white/5 rounded-xl border border-white/10 mb-6"></div>
          <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center gap-4">
             <div className="h-12 w-32 bg-white/5 rounded-full"></div>
             <div className="h-12 w-full sm:max-w-md bg-white/5 rounded-2xl"></div>
          </div>
        </div>
      </main>
    </div>
  );
}
