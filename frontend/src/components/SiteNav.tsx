import Link from 'next/link'
import { auth } from '@/lib/auth'
import { headers } from 'next/headers'
import { AuthForm } from '@/components/AuthForm'
import { SignOutButton } from '@/components/SignOutButton'

// The app's single logo mark, reused in the nav and footer.
function LogoMark({ className = '' }: { className?: string }) {
  return (
    <div className={`rounded-xl bg-gradient-to-tr from-indigo-500 to-cyan-400 flex items-center justify-center ${className}`}>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M6 3v18" />
        <path d="M12 7v10" />
        <path d="M18 10v4" />
      </svg>
    </div>
  )
}

// Shared top navigation rendered on every page from the root layout. Shows the
// app identity plus auth state (sign-in button, or the user's name + sign out).
export async function SiteNav() {
  const session = await auth.api.getSession({
    headers: await headers(),
  })

  return (
    <nav className="fixed top-0 w-full border-b border-white/5 bg-black/50 backdrop-blur-xl z-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 h-16 sm:h-20 flex items-center justify-between gap-3">
        <Link
          href="/"
          className="flex items-center gap-3 min-w-0 rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 focus-visible:ring-offset-black"
        >
          <LogoMark className="w-8 h-8" />
          <span className="font-bold text-xl tracking-tight text-white/90">rap-flow</span>
        </Link>

        {session?.user ? (
          <div className="flex items-center gap-3 sm:gap-4 min-w-0">
            <div className="hidden sm:block text-sm text-neutral-400 truncate">
              <span className="text-white/80 font-medium">{session.user.name || session.user.email}</span>
            </div>
            <SignOutButton />
          </div>
        ) : (
          <AuthForm className="scale-90 origin-right" />
        )}
      </div>
    </nav>
  )
}
