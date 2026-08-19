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

export async function retryJob(jobId: string) {
  const session = await auth.api.getSession({
    headers: await headers()
  });

  if (!session?.user) {
    throw new Error('Unauthorized');
  }

  const job = await prisma.job.findUnique({
    where: { id: jobId }
  });

  if (!job) {
    throw new Error('Job not found');
  }

  if (job.userId !== session.user.id) {
    throw new Error('Unauthorized');
  }

  if (job.status !== 'FAILED') {
    throw new Error('Only failed jobs can be retried');
  }

  // Update status back to PENDING and clear old results/errors
  const updatedJob = await prisma.job.update({
    where: { id: jobId },
    data: {
      status: 'PENDING',
      error: null,
      resultBlobUrl: null,
      eventsBlobUrl: null
    }
  });

  // Re-fire Modal worker
  const modalSourceUrl = job.sourceType === 'URL' ? job.sourceUrl : job.inputBlobUrl;

  fetch(process.env.MODAL_WORKER_URL || 'http://localhost:3000/api/mock', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      jobId: updatedJob.id,
      sourceUrl: modalSourceUrl,
      callbackUrl: `${process.env.NEXT_PUBLIC_APP_URL}/api/jobs/${updatedJob.id}/complete`,
      hmacSig: process.env.HMAC_SECRET || 'dummy-secret-for-dev',
      blobToken: process.env.BLOB_READ_WRITE_TOKEN
    })
  }).catch(console.error);

  revalidatePath(`/jobs/${jobId}`);
  revalidatePath('/');
  return updatedJob.id;
}
