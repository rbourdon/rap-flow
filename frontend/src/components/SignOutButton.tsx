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
            className="px-4 py-2 text-sm bg-red-500 text-white rounded hover:bg-red-600"
        >
            Sign Out
        </button>
    );
}
