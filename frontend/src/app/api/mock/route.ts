import { NextResponse } from 'next/server';
import crypto from 'crypto';

const HMAC_SECRET = process.env.HMAC_SECRET || 'dummy-secret-for-dev';

export async function POST(request: Request) {
  try {
    const data = await request.json();
    const { jobId, callbackUrl } = data;

    if (!jobId || !callbackUrl) {
      return NextResponse.json({ error: 'Missing parameters' }, { status: 400 });
    }

    const payload = {
      jobId,
      status: "COMPLETED",
      resultUrl: "https://dummy.blob.vercel-storage.com/mix.wav",
      eventsUrl: "https://dummy.blob.vercel-storage.com/events.json",
      events: []
    };

    const body = JSON.stringify(payload);
    const signature = crypto
      .createHmac('sha256', HMAC_SECRET)
      .update(body)
      .digest('hex');

    // Simulate async processing
    // NOTE: Using waitUntil to ensure background task completes in serverless environments
    if (request.signal) {
       // There isn't standard waitUntil on standard Request object here in vanilla next.js API routes without context
       // we can just await the fetch here since we want to complete it during this lifecycle so it doesn't get killed
       // In a true worker, this would be decoupled. Since it's a mock, we'll await it or fire it safely.
    }

    // Instead of a setTimeout that gets killed in Vercel, we can just await the fetch to the callback immediately
    // Since it's a mock anyway, immediate completion is fine for testing.
    await fetch(callbackUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-signature': signature
      },
      body
    }).catch(console.error);


    return NextResponse.json({ status: 'started', jobId });
  } catch (error) {
    console.error('Mock endpoint error:', error);
    return NextResponse.json({ error: 'Internal Server Error' }, { status: 500 });
  }
}
