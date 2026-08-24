import { NextResponse } from 'next/server';
import { prisma } from '@/lib/db';
import crypto from 'crypto';

const HMAC_SECRET = process.env.HMAC_SECRET || 'dummy-secret-for-dev';

export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const body = await request.text();
    const signature = request.headers.get('x-signature');

    if (!signature) {
      return NextResponse.json({ error: 'Missing signature' }, { status: 401 });
    }

    const expectedSignature = crypto
      .createHmac('sha256', HMAC_SECRET)
      .update(body)
      .digest('hex');

    if (signature !== expectedSignature) {
      return NextResponse.json({ error: 'Invalid signature' }, { status: 401 });
    }


    const data = JSON.parse(body);

    // Merge a per-stage update into the job's durable stageStates map. The
    // staged workflow reports each stage transition (RUNNING/COMPLETED/REUSED)
    // via `stageKey` + `stageState`; we keep the map so the UI can show
    // granular progress and offer per-stage reprocessing.
    async function mergeStageState(extra: Record<string, unknown> = {}) {
      if (!data.stageKey) {
        if (Object.keys(extra).length > 0) {
          await prisma.job.update({ where: { id }, data: extra });
        }
        return;
      }
      const existing = await prisma.job.findUnique({
        where: { id },
        select: { stageStates: true },
      });
      const stageStates = {
        ...((existing?.stageStates as Record<string, unknown>) ?? {}),
        [data.stageKey]: {
          state: data.stageState ?? data.status,
          reused: data.reused ?? false,
          updatedAt: new Date().toISOString(),
          // Non-fatal stage warnings (e.g. the groove stage falling back to the
          // heuristic when GrooVAE is unavailable) are surfaced here so the UI
          // can show them without the job being marked FAILED.
          ...(data.warning != null ? { warning: data.warning } : {}),
        },
      };
      await prisma.job.update({
        where: { id },
        data: { ...extra, stageStates },
      });
    }

    if (data.status === 'PROCESSING') {
      await mergeStageState({
        status: 'PROCESSING',
        // Persist the machine-readable stage id (stageKey) so the UI can map it
        // to a label via STAGE_LABELS. Fall back to `stage` for older workers.
        stage: data.stageKey ?? data.stage,
        // Source identity metadata (sent from the ingest stage). Only set fields
        // that are present so a metadata-only callback doesn't clobber others.
        ...(data.title != null ? { title: data.title } : {}),
        ...(data.thumbnailUrl != null ? { thumbnailUrl: data.thumbnailUrl } : {}),
        ...(data.durationSec != null ? { durationSec: data.durationSec } : {}),
        ...(data.uploader != null ? { uploader: data.uploader } : {}),
      });
      return NextResponse.json({ success: true });
    } else if (data.status === 'COMPLETED' && !data.resultUrl) {


      // Guard against a worker marking a job COMPLETED without producing a
      // result file, which would otherwise surface a confusing "no result
      // file was produced" message with no error attached.
      await prisma.job.update({
        where: { id },
        data: {
          status: 'FAILED',
          error: 'UPLOAD_FAILED: Job completed without producing a result file.',
        }
      });
    } else if (data.status === 'COMPLETED') {
      await mergeStageState({
        status: 'COMPLETED',
        stage: data.stageKey ?? data.stage,
        resultBlobUrl: data.resultUrl,
        eventsBlobUrl: data.eventsUrl,
        percBlobUrl: data.percUrl,
        instBlobUrl: data.instUrl,
        // New (nullable) artifacts. Older workers omit these; leaving them
        // undefined keeps the columns null and the UI falls back gracefully.
        midBlobUrl: data.midUrl ?? undefined,
        mixOpusBlobUrl: data.mixOpusUrl ?? undefined,
        percOpusBlobUrl: data.percOpusUrl ?? undefined,
        instOpusBlobUrl: data.instOpusUrl ?? undefined,
      });
    } else if (data.status === 'FAILED') {
      await mergeStageState({
        status: 'FAILED',
        error: data.error,
      });
    }

    return NextResponse.json({ success: true });

  } catch (error) {
    console.error('Webhook error:', error);
    return NextResponse.json({ error: 'Internal Server Error' }, { status: 500 });
  }
}
