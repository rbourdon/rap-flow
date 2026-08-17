import { auth } from "@/lib/auth";
import { NextRequest } from "next/server";

export const runtime = 'edge';

export async function GET(req: NextRequest) {
  return auth.handler(req);
}

export async function POST(req: NextRequest) {
  return auth.handler(req);
}
