"use client";
import { useState } from 'react';
import { authClient } from '@/lib/auth-client';
import { useRouter } from 'next/navigation';

export function AuthForm() {
    const router = useRouter();
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);

    const handleGoogleSignIn = async () => {
        setError('');
        setLoading(true);

        try {
            const { error } = await authClient.signIn.social({
                provider: 'google',
            });
            if (error) {
                setError(error.message || 'An error occurred during sign in');
            } else {
                router.refresh();
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'An unexpected error occurred');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="max-w-md p-6 bg-white dark:bg-neutral-800 rounded-lg shadow-md border border-neutral-200 dark:border-neutral-700 flex flex-col items-center">
            <h2 className="text-xl font-bold mb-4">Sign In</h2>
            {error && <p className="text-red-500 mb-4">{error}</p>}
            <button
                onClick={handleGoogleSignIn}
                disabled={loading}
                className="p-3 w-full flex items-center justify-center gap-2 bg-white dark:bg-neutral-900 border border-neutral-300 dark:border-neutral-700 text-neutral-800 dark:text-neutral-200 rounded hover:bg-neutral-50 dark:hover:bg-neutral-800 disabled:opacity-50 font-medium transition-colors"
            >
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" className="w-5 h-5">
                    <path fill="#FFC107" d="M43.611 20.083H42V20H24v8h11.303c-1.649 4.657-6.08 8-11.303 8c-6.627 0-12-5.373-12-12s5.373-12 12-12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4C12.955 4 4 12.955 4 24s8.955 20 20 20s20-8.955 20-20c0-1.341-.138-2.65-.389-3.917z" />
                    <path fill="#FF3D00" d="m6.306 14.691l6.571 4.819C14.655 15.108 18.961 12 24 12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4C16.318 4 9.656 8.337 6.306 14.691z" />
                    <path fill="#4CAF50" d="M24 44c5.166 0 9.86-1.977 13.409-5.192l-6.19-5.238A11.91 11.91 0 0 1 24 36c-5.222 0-9.654-3.343-11.124-8l-6.571 4.819C9.656 39.663 16.318 44 24 44z" />
                    <path fill="#1976D2" d="M43.611 20.083H42V20H24v8h11.303a12.04 12.04 0 0 1-4.087 5.571l.003-.002l6.19 5.238C36.971 39.205 44 34 44 24c0-1.341-.138-2.65-.389-3.917z" />
                </svg>
                {loading ? 'Connecting...' : 'Continue with Google'}
            </button>
        </div>
    );
}
