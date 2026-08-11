import { betterAuth } from "better-auth";
import { prismaAdapter } from "better-auth/adapters/prisma";
import { prisma } from "./db";

// Better Auth requires a base URL. For dynamic Vercel deployments (like preview environments),
// we use the VERCEL_URL environment variable provided by Vercel.
// We also use `BETTER_AUTH_URL` if explicitly set (e.g., in production).
const getBaseURL = () => {
  if (process.env.BETTER_AUTH_URL) {
    return process.env.BETTER_AUTH_URL;
  }
  if (process.env.VERCEL_URL) {
    return `https://${process.env.VERCEL_URL}`;
  }
  // Fallback for local development or static generation if neither is set
  return process.env.NODE_ENV === "production" ? "https://your-production-url.com" : "http://localhost:3000";
};

export const auth = betterAuth({
    database: prismaAdapter(prisma, {
        provider: "postgresql",
    }),
    emailAndPassword: {
        enabled: true,
    },
    // Required to be set in Next.js when deploying to Vercel/dynamic hosts
    baseURL: getBaseURL(),
    // Allow the specific Vercel deployment URL if it exists
    trustedOrigins: process.env.VERCEL_URL ? [`https://${process.env.VERCEL_URL}`] : [],
});
