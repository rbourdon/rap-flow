'use client';

import { useState } from 'react';
import { upload } from '@vercel/blob/client';
import { createJobFromBlob, createJobFromUrl } from '@/app/actions';

export function UploadWidget() {
  const [url, setUrl] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);

  const handleUrlSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url) return;
    setIsUploading(true);
    try {
      await createJobFromUrl(url);
      setUrl('');
    } catch (e) {
      console.error(e);
    }
    setIsUploading(false);
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
      await createJobFromBlob(newBlob.url);
      setFile(null);
    } catch (e) {
      console.error(e);
    }
    setIsUploading(false);
  };

  return (
    <div className="flex flex-col gap-4 border p-4 rounded-lg">
      <form onSubmit={handleUrlSubmit} className="flex flex-col gap-2">
        <label>Provide a URL (Youtube, Soundcloud):</label>
        <input
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          className="border p-2 rounded text-black"
          disabled={isUploading}
        />
        <button type="submit" disabled={isUploading || !url} className="bg-blue-500 text-white p-2 rounded">
          {isUploading ? 'Processing...' : 'Submit URL'}
        </button>
      </form>

      <div className="text-center font-bold">OR</div>

      <form onSubmit={handleFileUpload} className="flex flex-col gap-2">
        <label>Upload Audio File:</label>
        <input
          type="file"
          accept="audio/*"
          onChange={(e) => setFile(e.target.files?.[0] || null)}
          disabled={isUploading}
        />
        <button type="submit" disabled={isUploading || !file} className="bg-blue-500 text-white p-2 rounded">
          {isUploading ? 'Uploading...' : 'Upload File'}
        </button>
      </form>
    </div>
  );
}
