'use server'

import { prisma } from '@/lib/db'
import { revalidatePath } from 'next/cache'
import { auth } from '@/lib/auth'
import { headers } from 'next/headers'

// Machine-readable workflow stages, in execution order. Must stay in sync with
// backend/workflow.py STAGES.
export const WORKFLOW_STAGES = [
  'ingest',
  'separate',
  'detect',
  'render',
  'finalize',
] as const

export type WorkflowStage = (typeof WORKFLOW_STAGES)[number]

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
