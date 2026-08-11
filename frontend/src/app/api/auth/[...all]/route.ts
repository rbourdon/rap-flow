import { auth } from "@/lib/auth";
import { toNextJsHandler } from "better-auth/next-js";

// toNextJsHandler expects the auth object itself, not auth.handler
const handler = toNextJsHandler(auth);

// Export GET and POST explicitly to fix Next.js 15+ routing type constraint issues
export const GET = handler.GET;
export const POST = handler.POST;
