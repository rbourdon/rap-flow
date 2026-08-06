'use server'

import { prisma } from '@/lib/db'
import { revalidatePath } from 'next/cache'
import { auth } from '@/lib/auth'
import { headers } from 'next/headers'

export async function createJobFromUrl(sourceUrl: string) {
  const session = await auth.api.getSession({
    headers: await headers()
  });

  if (!session?.user) {
    throw new Error('Unauthorized');
  }

  const job = await prisma.job.create({
    data: {
      userId: session.user.id,
      sourceType: 'URL',
      sourceUrl,
      status: 'PENDING',
    }
  });

  // Fire and forget to Modal worker
  fetch(process.env.MODAL_WORKER_URL || 'http://localhost:3000/api/mock', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      jobId: job.id,
      sourceUrl: sourceUrl,
      callbackUrl: `${process.env.NEXT_PUBLIC_APP_URL}/api/jobs/${job.id}/complete`,
      hmacSig: process.env.HMAC_SECRET || 'dummy-secret-for-dev',
      blobToken: process.env.BLOB_READ_WRITE_TOKEN
    })
  }).catch(console.error);

  revalidatePath('/');
  return job.id;
}

export async function createJobFromBlob(blobUrl: string) {
  const session = await auth.api.getSession({
    headers: await headers()
  });

  if (!session?.user) {
    throw new Error('Unauthorized');
  }

  const job = await prisma.job.create({
    data: {
      userId: session.user.id,
      sourceType: 'UPLOAD',
      inputBlobUrl: blobUrl,
      status: 'PENDING',
    }
  });

  fetch(process.env.MODAL_WORKER_URL || 'http://localhost:3000/api/mock', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      jobId: job.id,
      sourceUrl: blobUrl, // use blob url as source
      callbackUrl: `${process.env.NEXT_PUBLIC_APP_URL}/api/jobs/${job.id}/complete`,
      hmacSig: process.env.HMAC_SECRET || 'dummy-secret-for-dev',
      blobToken: process.env.BLOB_READ_WRITE_TOKEN
    })
  }).catch(console.error);

  revalidatePath('/');
  return job.id;
}
