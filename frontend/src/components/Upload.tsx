'use client';

import { useState } from 'react';
import { upload } from '@vercel/blob/client';
import { createJobFromBlob, createJobFromUrl } from '@/app/actions';
import { useRouter } from 'next/navigation';

export function UploadWidget() {
  const [url, setUrl] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const router = useRouter();

  const handleUrlSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url) return;
    setIsUploading(true);
    try {
      const jobId = await createJobFromUrl(url);
      setUrl('');
      router.push(`/jobs/${jobId}`);
    } catch (e) {
      console.error(e);
      setIsUploading(false);
    }
  };

  const handleFileUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) return;
    setIsUploading(true);
    try {
      const newBlob = await upload(file.name, file, {
        access: 'public',
        handleUploadUrl: '/api/upload',
      });
      const jobId = await createJobFromBlob(newBlob.url);
      setFile(null);
      router.push(`/jobs/${jobId}`);
    } catch (e) {
      console.error(e);
      setIsUploading(false);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <form onSubmit={handleUrlSubmit} className="flex flex-col gap-3">
        <label className="text-sm font-medium text-neutral-400">Provide a URL (Youtube, Soundcloud):</label>
        <div className="flex gap-3">
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            className="flex-grow bg-white/5 border border-white/10 rounded-lg p-3 text-white placeholder-neutral-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500/50 transition-all"
            placeholder="https://..."
            disabled={isUploading}
          />
          <button
            type="submit"
            disabled={isUploading || !url}
            className="bg-indigo-600 hover:bg-indigo-500 disabled:bg-neutral-800 disabled:text-neutral-500 text-white font-medium px-6 py-3 rounded-lg transition-colors whitespace-nowrap"
          >
            {isUploading ? 'Processing...' : 'Submit URL'}
          </button>
        </div>
      </form>

      <div className="flex items-center gap-4">
        <div className="h-px bg-white/10 flex-grow"></div>
        <span className="text-xs font-bold text-neutral-600 uppercase tracking-widest">OR</span>
        <div className="h-px bg-white/10 flex-grow"></div>
      </div>

      <form onSubmit={handleFileUpload} className="flex flex-col gap-3">
        <label className="text-sm font-medium text-neutral-400">Upload Audio File:</label>
        <div className="flex gap-3 items-center">
          <input
            type="file"
            accept="audio/*"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
            disabled={isUploading}
            className="flex-grow text-sm text-neutral-400 file:mr-4 file:py-2.5 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-semibold file:bg-white/10 file:text-white hover:file:bg-white/20 transition-all"
          />
          <button
            type="submit"
            disabled={isUploading || !file}
            className="bg-cyan-600 hover:bg-cyan-500 disabled:bg-neutral-800 disabled:text-neutral-500 text-white font-medium px-6 py-3 rounded-lg transition-colors whitespace-nowrap"
          >
            {isUploading ? 'Uploading...' : 'Upload File'}
          </button>
        </div>
      </form>
    </div>
  );
}
