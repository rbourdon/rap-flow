"use client";
import { authClient } from '@/lib/auth-client';
import { useRouter } from 'next/navigation';

export function SignOutButton() {
    const router = useRouter();

    const handleSignOut = async () => {
        await authClient.signOut({
            fetchOptions: {
                onSuccess: () => {
                    router.refresh();
                },
            },
        });
    };

    return (
        <button
            onClick={handleSignOut}
            className="px-4 py-2 text-sm font-medium bg-white/5 hover:bg-white/10 border border-white/10 text-white/80 hover:text-white rounded-lg transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 focus-visible:ring-offset-black"
        >
            Sign Out
        </button>
    );
}
