import { betterAuth } from "better-auth";
import { prismaAdapter } from "better-auth/adapters/prisma";
import { prisma } from "./db";

const getEnvString = (value: unknown, fallback = "") =>
  typeof value === "string" ? value : fallback;

// Better Auth requires a base URL. For dynamic Vercel deployments (like preview environments),
// we use the VERCEL_URL environment variable provided by Vercel.
// We also use `BETTER_AUTH_URL` if explicitly set (e.g., in production).
const getBaseURL = () => {
  const betterAuthUrl = getEnvString(process.env.BETTER_AUTH_URL);
  if (betterAuthUrl) {
    return betterAuthUrl;
  }
  const vercelUrl = getEnvString(process.env.VERCEL_URL);
  if (vercelUrl) {
    return `https://${vercelUrl}`;
  }
  // Fallback for local development or static generation if neither is set
  return process.env.NODE_ENV === "production" ? "https://your-production-url.com" : "http://localhost:3000";
};

// Build the list of origins Better Auth should trust. `VERCEL_URL` only reflects the
// ephemeral per-deployment URL (e.g. preview builds), not stable domains such as the
// production `*.vercel.app` alias or a custom domain. To support those, additional
// origins can be supplied via the comma-separated `BETTER_AUTH_TRUSTED_ORIGINS` env var.
const getTrustedOrigins = (baseURL: string) => {
  const origins = new Set<string>([baseURL]);

  const vercelUrl = getEnvString(process.env.VERCEL_URL);
  if (vercelUrl) {
    origins.add(`https://${vercelUrl}`);
  }

  const extraOrigins = getEnvString(process.env.BETTER_AUTH_TRUSTED_ORIGINS)
    .split(",")
    .map((origin) => origin.trim())
    .filter(Boolean);
  for (const origin of extraOrigins) {
    origins.add(origin);
  }

  return Array.from(origins);
};

export const auth = betterAuth({
    secret: process.env.BETTER_AUTH_SECRET || "default_secret_for_dev_so_build_does_not_fail",
    database: prismaAdapter(prisma, {
        provider: "postgresql",
    }),
    socialProviders: {
        google: {
            clientId: getEnvString(process.env.GOOGLE_CLIENT_ID),
            clientSecret: getEnvString(process.env.GOOGLE_CLIENT_SECRET),
        }
    },
    // Required to be set in Next.js when deploying to Vercel/dynamic hosts
    baseURL: getBaseURL(),
    // Always trust the resolved base URL, the current Vercel deployment URL (if any),
    // and any additional origins configured via BETTER_AUTH_TRUSTED_ORIGINS (e.g. the
    // stable production `*.vercel.app` alias or a custom domain).
    trustedOrigins: getTrustedOrigins(getBaseURL()),
});
