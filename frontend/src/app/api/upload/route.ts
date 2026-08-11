import { handleUpload, type HandleUploadBody } from '@vercel/blob/client';
import { NextResponse } from 'next/server';
import { auth } from '@/lib/auth';
import { headers } from 'next/headers';

export async function POST(request: Request): Promise<NextResponse> {
  const body = (await request.json()) as HandleUploadBody;

  try {
    const jsonResponse = await handleUpload({
      body,
      request,
      onBeforeGenerateToken: async () => {
        const session = await auth.api.getSession({
            headers: await headers()
        });

        if (!session?.user) {
          throw new Error('Unauthorized');
        }

        return {
          allowedContentTypes: ['audio/mpeg', 'audio/wav', 'audio/flac', 'audio/x-wav'],
          tokenPayload: JSON.stringify({
            userId: session.user.id
          }),
        };
      },
      onUploadCompleted: async ({ blob }) => {
        console.log('Upload completed', blob.url);
      },
    });

    return NextResponse.json(jsonResponse);
  } catch (error) {
    return NextResponse.json(
      { error: (error as Error).message },
      { status: 400 },
    );
  }
}
