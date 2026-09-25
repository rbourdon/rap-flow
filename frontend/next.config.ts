import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Next refuses to run two `next dev` servers against one build directory.
  // The e2e suite sets NEXT_DIST_DIR=.next-e2e so it can run next to a normal
  // dev server (and so can a second agent working in the same checkout).
  // Unset everywhere else, including on Vercel.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // The Neon serverless driver (and its `ws` WebSocket dependency) relies on
  // Node.js-specific behavior that breaks when bundled by Next.js' Server
  // Components bundler. Left un-externalized, the module loads without
  // erroring but fails as soon as it actually opens a database connection
  // (e.g. when validating a logged-in user's session), producing a 500 with
  // no useful stack trace. Opting these packages out of bundling makes them
  // use native `require` instead, matching Prisma/Neon's own deployment
  // guidance for Next.js. `pg` / `@prisma/adapter-pg` back the local and CI
  // Postgres (see src/lib/db.ts) and get the same treatment.
  serverExternalPackages: [
    "@neondatabase/serverless",
    "@prisma/adapter-neon",
    "@prisma/adapter-pg",
    "pg",
    "ws",
  ],
};

export default nextConfig;
