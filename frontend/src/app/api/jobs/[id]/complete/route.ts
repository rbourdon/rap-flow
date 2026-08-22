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


    if (data.status === 'PROCESSING') {
      await prisma.job.update({
        where: { id },
        data: {
          status: 'PROCESSING',
          stage: data.stage,
        }
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
      await prisma.job.update({
        where: { id },
        data: {
          status: 'COMPLETED',
          resultBlobUrl: data.resultUrl,
          eventsBlobUrl: data.eventsUrl,
        }
      });
    } else if (data.status === 'FAILED') {
      await prisma.job.update({
        where: { id },
        data: {
          status: 'FAILED',
          error: data.error,
        }
      });
    }

    return NextResponse.json({ success: true });

  } catch (error) {
    console.error('Webhook error:', error);
    return NextResponse.json({ error: 'Internal Server Error' }, { status: 500 });
  }
}
