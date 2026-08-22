import { NextResponse } from 'next/server';
import { prisma } from '@/lib/db';
import { auth } from '@/lib/auth';
import { headers } from 'next/headers';

const ASSET_FIELDS = {
  mix: 'resultBlobUrl',
  events: 'eventsBlobUrl',
  perc: 'percBlobUrl',
  inst: 'instBlobUrl',
} as const;

type AssetType = keyof typeof ASSET_FIELDS;

function isAssetType(value: string | null): value is AssetType {
  return value === 'mix' || value === 'events' || value === 'perc' || value === 'inst';
}

// Proxies reads of the job's result files stored in Vercel Blob. The blobs
// are uploaded with `private` access (required by stores configured for
// private access), so they can't be fetched directly by the browser -
// requests must include the `BLOB_READ_WRITE_TOKEN` as a bearer token,
// which only this server route has access to.
export async function GET(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
) {
  const session = await auth.api.getSession({
    headers: await headers()
  });

  if (!session?.user) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  const { id } = await params;
  const { searchParams } = new URL(request.url);
  const type = searchParams.get('type');

  if (!isAssetType(type)) {
    return NextResponse.json({ error: 'Invalid asset type' }, { status: 400 });
  }

  const job = await prisma.job.findUnique({ where: { id } });

  if (!job || job.userId !== session.user.id) {
    return NextResponse.json({ error: 'Not found' }, { status: 404 });
  }

  const blobUrl = job[ASSET_FIELDS[type]];

  if (!blobUrl) {
    return NextResponse.json({ error: 'Not found' }, { status: 404 });
  }

  const token = process.env.BLOB_READ_WRITE_TOKEN;

  const blobRes = await fetch(blobUrl, {
    headers: token ? { authorization: 'Bearer ' + token } : {},
  });

  if (!blobRes.ok || !blobRes.body) {
    return NextResponse.json(
      { error: 'Failed to fetch asset' },
      { status: blobRes.status || 502 }
    );
  }

  const responseHeaders = new Headers();
  const contentType = blobRes.headers.get('content-type');
  if (contentType) responseHeaders.set('content-type', contentType);
  const contentLength = blobRes.headers.get('content-length');
  if (contentLength) responseHeaders.set('content-length', contentLength);
  if (type === 'mix') {
    responseHeaders.set(
      'content-disposition',
      `attachment; filename="${id}.wav"`
    );
  }

  return new NextResponse(blobRes.body, {
    status: 200,
    headers: responseHeaders,
  });
}
