'use client';

import { useRef, useState } from 'react';
import { upload } from '@vercel/blob/client';
import { createJobFromBlob, createJobFromUrl } from '@/app/actions';
import { useRouter } from 'next/navigation';
import {
  isValidSourceUrl,
  isAudioFile,
  formatBytes,
  MAX_UPLOAD_BYTES,
} from '@/lib/upload';
import { btnPrimary, focusRing } from '@/lib/ui';

type Phase = 'idle' | 'uploading' | 'creating';

export function UploadWidget() {
  const [url, setUrl] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();

  const busy = phase !== 'idle';
  const canSubmit = !busy && (!!file || url.trim().length > 0);

  const chooseFile = (f: File | null) => {
    setError(null);
    if (!f) {
      setFile(null);
      return;
    }
    if (!isAudioFile(f)) {
      setError('That file is not audio. Please choose an mp3, wav, or flac file.');
      return;
    }
    if (f.size > MAX_UPLOAD_BYTES) {
      setError(`File is too large (${formatBytes(f.size)}). The limit is ${formatBytes(MAX_UPLOAD_BYTES)}.`);
      return;
    }
    setFile(f);
    // A picked file takes precedence over a typed URL.
    setUrl('');
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (busy) return;
    const dropped = e.dataTransfer.files?.[0];
    if (dropped) chooseFile(dropped);
  };

  const submit = async () => {
    if (busy) return;
    setError(null);

    try {
      if (file) {
        setPhase('uploading');
        const newBlob = await upload(file.name, file, {
          access: 'public',
          handleUploadUrl: '/api/upload',
        });
        setPhase('creating');
        const jobId = await createJobFromBlob(newBlob.url, file.name);
        router.push(`/jobs/${jobId}`);
        return;
      }

      const trimmed = url.trim();
      if (!trimmed) {
        setError('Paste a URL or choose an audio file to get started.');
        return;
      }
      if (!isValidSourceUrl(trimmed)) {
        setError('Enter a valid YouTube or SoundCloud URL (e.g. youtube.com/watch?v=…).');
        return;
      }
      setPhase('creating');
      const jobId = await createJobFromUrl(trimmed);
      router.push(`/jobs/${jobId}`);
    } catch (err) {
      console.error(err);
      setError(err instanceof Error ? err.message : 'Something went wrong. Please try again.');
      setPhase('idle');
    }
  };

  const onFormSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    submit();
  };

  const buttonLabel =
    phase === 'uploading' ? 'Uploading…' : phase === 'creating' ? 'Creating job…' : 'Create beat';

  return (
    <form onSubmit={onFormSubmit} className="flex flex-col gap-4">
      <div
        onDragOver={(e) => { e.preventDefault(); if (!busy) setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`relative rounded-2xl border-2 border-dashed p-6 transition-colors ${
          dragging ? 'border-indigo-500 bg-indigo-500/10' : 'border-white/10 bg-white/[0.02]'
        }`}
      >
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <label htmlFor="source-url" className="text-sm font-medium text-neutral-400">
              Paste a YouTube or SoundCloud URL
            </label>
            <input
              id="source-url"
              type="url"
              inputMode="url"
              value={url}
              onChange={(e) => { setUrl(e.target.value); if (file) setFile(null); setError(null); }}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } }}
              className={`w-full bg-white/5 border border-white/10 rounded-lg p-3 text-white placeholder-neutral-500 transition-all ${focusRing}`}
              placeholder="https://youtube.com/watch?v=…"
              disabled={busy}
            />
          </div>

          <div className="flex items-center gap-4">
            <div className="h-px bg-white/10 flex-grow" />
            <span className="text-xs font-bold text-neutral-400 uppercase tracking-widest">or drop a file</span>
            <div className="h-px bg-white/10 flex-grow" />
          </div>

          {file ? (
            <div className="flex items-center justify-between gap-3 rounded-lg bg-white/5 border border-white/10 p-3">
              <div className="min-w-0">
                <div className="text-sm text-white/90 truncate">{file.name}</div>
                <div className="text-xs text-neutral-400">{formatBytes(file.size)}</div>
              </div>
              <button
                type="button"
                onClick={() => { setFile(null); setError(null); if (fileInputRef.current) fileInputRef.current.value = ''; }}
                disabled={busy}
                aria-label="Remove selected file"
                className={`p-2 rounded-lg border border-white/10 text-neutral-400 hover:text-white hover:bg-white/10 transition disabled:opacity-50 ${focusRing}`}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M18 6 6 18M6 6l12 12" />
                </svg>
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={busy}
              className={`w-full rounded-lg border border-white/10 bg-white/5 hover:bg-white/10 text-sm text-neutral-300 py-3 transition disabled:opacity-50 ${focusRing}`}
            >
              Drag an audio file here, or <span className="text-indigo-300 font-medium">browse</span>
            </button>
          )}

          <input
            ref={fileInputRef}
            type="file"
            accept="audio/*"
            onChange={(e) => chooseFile(e.target.files?.[0] || null)}
            disabled={busy}
            className="sr-only"
          />
        </div>
      </div>

      {error && (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2" role="alert">
          <p className="text-sm text-red-300">{error}</p>
          <button
            type="button"
            onClick={() => setError(null)}
            className="text-xs text-red-300/80 hover:text-red-200 underline flex-shrink-0"
          >
            Dismiss
          </button>
        </div>
      )}

      <div className="flex flex-col gap-2">
        <button
          type="submit"
          disabled={!canSubmit}
          className={`${btnPrimary} px-6 py-3 w-full`}
        >
          {buttonLabel}
        </button>
        <p className="text-xs text-neutral-400 text-center">
          Processing takes a few minutes — you can leave this page; the job keeps running.
        </p>
      </div>
    </form>
  );
}
