import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The Neon serverless driver (and its `ws` WebSocket dependency) relies on
  // Node.js-specific behavior that breaks when bundled by Next.js' Server
  // Components bundler. Left un-externalized, the module loads without
  // erroring but fails as soon as it actually opens a database connection
  // (e.g. when validating a logged-in user's session), producing a 500 with
  // no useful stack trace. Opting these packages out of bundling makes them
  // use native `require` instead, matching Prisma/Neon's own deployment
  // guidance for Next.js.
  serverExternalPackages: ["@neondatabase/serverless", "@prisma/adapter-neon", "ws"],
};

export default nextConfig;
