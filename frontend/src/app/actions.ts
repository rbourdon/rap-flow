'use server'

import { prisma } from '@/lib/db'
import { revalidatePath } from 'next/cache'
import { auth } from '@/lib/auth'
import { headers } from 'next/headers'

async function getAppUrl() {
  const headersList = await headers();
  const host = headersList.get('host') || 'localhost:3000';
  const protocol = headersList.get('x-forwarded-proto') || 'http';

  if (process.env.NEXT_PUBLIC_APP_URL) {
    return process.env.NEXT_PUBLIC_APP_URL;
  }
  if (process.env.VERCEL_URL) {
    return `https://${process.env.VERCEL_URL}`;
  }
  return `${protocol}://${host}`;
}

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

  const appUrl = await getAppUrl();

  // Fire and forget to Modal worker
  fetch(process.env.MODAL_WORKER_URL || `${appUrl}/api/mock`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      jobId: job.id,
      sourceUrl: sourceUrl,
      callbackUrl: `${appUrl}/api/jobs/${job.id}/complete`,
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

  const appUrl = await getAppUrl();

  fetch(process.env.MODAL_WORKER_URL || `${appUrl}/api/mock`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      jobId: job.id,
      sourceUrl: blobUrl, // use blob url as source
      callbackUrl: `${appUrl}/api/jobs/${job.id}/complete`,
      hmacSig: process.env.HMAC_SECRET || 'dummy-secret-for-dev',
      blobToken: process.env.BLOB_READ_WRITE_TOKEN
    })
  }).catch(console.error);

  revalidatePath('/');
  return job.id;
}
