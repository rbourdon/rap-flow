"use client";
import { useState } from 'react';
import { authClient } from '@/lib/auth-client';
import { useRouter } from 'next/navigation';

export function AuthForm() {
    const [isLogin, setIsLogin] = useState(true);
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [name, setName] = useState('');
    const router = useRouter();
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError('');
        setLoading(true);

        try {
            if (isLogin) {
                const { error } = await authClient.signIn.email({
                    email,
                    password,
                });
                if (error) {
                    setError(error.message || 'An error occurred during sign in');
                } else {
                    router.refresh();
                }
            } else {
                const { error } = await authClient.signUp.email({
                    email,
                    password,
                    name: name || email.split('@')[0],
                });
                if (error) {
                    setError(error.message || 'An error occurred during sign up');
                } else {
                    router.refresh();
                }
            }
        } catch (err: any) {
            setError(err.message || 'An unexpected error occurred');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="max-w-md p-6 bg-white dark:bg-neutral-800 rounded-lg shadow-md border border-neutral-200 dark:border-neutral-700">
            <h2 className="text-xl font-bold mb-4">{isLogin ? 'Sign In' : 'Sign Up'}</h2>
            {error && <p className="text-red-500 mb-4">{error}</p>}
            <form onSubmit={handleSubmit} className="flex flex-col gap-4">
                {!isLogin && (
                    <input
                        type="text"
                        placeholder="Name"
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        className="p-2 border border-neutral-300 dark:border-neutral-600 rounded bg-transparent"
                        required
                    />
                )}
                <input
                    type="email"
                    placeholder="Email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    className="p-2 border border-neutral-300 dark:border-neutral-600 rounded bg-transparent"
                    required
                />
                <input
                    type="password"
                    placeholder="Password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="p-2 border border-neutral-300 dark:border-neutral-600 rounded bg-transparent"
                    required
                />
                <button type="submit" disabled={loading} className="p-2 bg-blue-500 text-white rounded hover:bg-blue-600 disabled:opacity-50">
                    {loading ? 'Processing...' : (isLogin ? 'Sign In' : 'Sign Up')}
                </button>
            </form>
            <button
                onClick={() => setIsLogin(!isLogin)}
                className="mt-4 text-sm text-blue-500 hover:underline"
            >
                {isLogin ? 'Need an account? Sign up' : 'Already have an account? Sign in'}
            </button>
        </div>
    );
}
