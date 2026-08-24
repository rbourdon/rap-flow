'use server'

import { prisma } from '@/lib/db'
import { del } from '@vercel/blob'
import { revalidatePath } from 'next/cache'
import { auth } from '@/lib/auth'
import { headers } from 'next/headers'
import { WORKFLOW_STAGES, type WorkflowStage } from './workflow-stages'

// Fire-and-forget trigger to the Modal worker. The Next.js side only *triggers*
// the workflow (optionally at a specific stage / with param overrides) and
// receives HMAC callbacks - it never owns the orchestration.
function triggerWorker(opts: {
  jobId: string
  sourceUrl: string | null
  fromStage?: WorkflowStage
  params?: Record<string, unknown>
}) {
  const body: Record<string, unknown> = {
    jobId: opts.jobId,
    sourceUrl: opts.sourceUrl,
    callbackUrl: `${process.env.NEXT_PUBLIC_APP_URL}/api/jobs/${opts.jobId}/complete`,
    hmacSig: process.env.HMAC_SECRET || 'dummy-secret-for-dev',
    blobToken: process.env.BLOB_READ_WRITE_TOKEN,
  }
  if (opts.fromStage) body.fromStage = opts.fromStage
  if (opts.params && Object.keys(opts.params).length > 0) body.params = opts.params

  fetch(process.env.MODAL_WORKER_URL || 'http://localhost:3000/api/mock', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  }).catch(console.error)
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

  triggerWorker({ jobId: job.id, sourceUrl });

  revalidatePath('/');
  return job.id;
}

export async function createJobFromBlob(blobUrl: string, title?: string) {
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
      // Use the original filename as the title for uploads (URL jobs get their
      // title from yt-dlp metadata during ingest).
      title: title || null,
      status: 'PENDING',
    }
  });

  // Use the blob url as the source for the worker.
  triggerWorker({ jobId: job.id, sourceUrl: blobUrl });

  revalidatePath('/');
  return job.id;
}

// The URL the worker should ingest from, depending on the job's source type.
function jobSourceUrl(job: { sourceType: string; sourceUrl: string | null; inputBlobUrl: string | null }) {
  return job.sourceType === 'URL' ? job.sourceUrl : job.inputBlobUrl;
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

  if (job.status !== 'FAILED' && !(job.status === 'COMPLETED' && !job.resultBlobUrl)) {
    throw new Error('Only failed jobs can be retried');
  }

  // Update status back to PENDING and clear old results/errors. The staged
  // workflow reuses any artifacts that already succeeded (e.g. a completed
  // download), so a retry only redoes the work that actually failed.
  const updatedJob = await prisma.job.update({
    where: { id: jobId },
    data: {
      status: 'PENDING',
      error: null,
      resultBlobUrl: null,
      eventsBlobUrl: null
    }
  });

  triggerWorker({ jobId: updatedJob.id, sourceUrl: jobSourceUrl(job) });

  revalidatePath(`/jobs/${jobId}`);
  revalidatePath('/');
  return updatedJob.id;
}

// Reprocess an individual stage of an existing job. The worker forces
// recomputation from `fromStage` onward while reusing every upstream artifact
// (download, stems, events) from the durable cache. Optional `params` override
// render/separation tunables (e.g. re-render with different ducking) - changing
// them yields a fresh cache key, so the result is never stale.
export async function reprocessJob(
  jobId: string,
  fromStage: WorkflowStage,
  params?: Record<string, unknown>,
) {
  const session = await auth.api.getSession({
    headers: await headers()
  });

  if (!session?.user) {
    throw new Error('Unauthorized');
  }

  if (!WORKFLOW_STAGES.includes(fromStage)) {
    throw new Error(`Invalid stage: ${fromStage}`);
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

  const sourceUrl = jobSourceUrl(job);
  if (!sourceUrl) {
    throw new Error('Job has no source to reprocess');
  }

  const updatedJob = await prisma.job.update({
    where: { id: jobId },
    data: {
      status: 'PENDING',
      stage: null,
      error: null,
    }
  });

  triggerWorker({ jobId: updatedJob.id, sourceUrl, fromStage, params });

  revalidatePath(`/jobs/${jobId}`);
  revalidatePath('/');
  return updatedJob.id;
}

// Permanently delete a job and every Vercel Blob it produced. Vercel Blob's
// free tier is small, so removing a job must also reclaim the storage its
// input/result/stem/event files occupy. Blob deletion is best-effort and
// idempotent: `del` doesn't throw for URLs that were already removed (e.g.
// blobs deleted manually while the store was over quota), so a partially
// broken job can still be cleaned up and its DB row removed.
export async function deleteJob(jobId: string) {
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

  const blobUrls = [
    job.inputBlobUrl,
    job.resultBlobUrl,
    job.eventsBlobUrl,
    job.percBlobUrl,
    job.instBlobUrl,
  ].filter((url): url is string => Boolean(url));

  if (blobUrls.length > 0) {
    try {
      await del(blobUrls, { token: process.env.BLOB_READ_WRITE_TOKEN });
    } catch (error) {
      // Don't block DB deletion if blob cleanup fails (e.g. a blob was already
      // removed manually). Surface it in logs so orphaned blobs can be noticed.
      console.error(`Failed to delete blobs for job ${jobId}:`, error);
    }
  }

  await prisma.job.delete({
    where: { id: jobId }
  });

  revalidatePath('/');
}
