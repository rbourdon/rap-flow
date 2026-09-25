// Mint a signed-in Better Auth session without going through Google OAuth.
//
//   node scripts/dev-session.cjs [email]        # prints JSON with the cookie
//
// Upserts a user and a fresh session row directly in $DATABASE_URL, then signs
// the session token exactly as Better Auth does (better-call's
// signCookieValue: `${token}.${base64(HMAC-SHA256(secret, token))}`, URL-
// encoded), so the app treats the cookie as a real login. Refuses to touch
// anything but a local database: this is for dev servers and e2e runs only.
//
// Also importable (the e2e fixtures do). CommonJS on purpose: Playwright's
// TypeScript loader require()s its imports, which an .mjs can't satisfy.
const crypto = require('node:crypto')
const pg = require('pg')

// Same fallback as src/lib/auth.ts, so an unconfigured dev server accepts it.
const DEFAULT_SECRET = 'default_secret_for_dev_so_build_does_not_fail'

const SESSION_COOKIE = 'better-auth.session_token'

async function createDevSession({
  email = 'dev@rap-flow.local',
  name = 'Dev User',
  databaseUrl = process.env.DATABASE_URL,
  secret = process.env.BETTER_AUTH_SECRET || DEFAULT_SECRET,
} = {}) {
  if (!databaseUrl) throw new Error('DATABASE_URL is not set')
  const { hostname } = new URL(databaseUrl)
  if (!['localhost', '127.0.0.1', '[::1]'].includes(hostname) && process.env.ALLOW_REMOTE_DEV_SESSION !== '1') {
    throw new Error(`refusing to mint a session on non-local database host ${hostname}`)
  }

  const client = new pg.Client({ connectionString: databaseUrl })
  await client.connect()
  try {
    const now = new Date()
    const { rows } = await client.query(
      `INSERT INTO "user" (id, name, email, "emailVerified", "createdAt", "updatedAt")
       VALUES ($1, $2, $3, true, $4, $4)
       ON CONFLICT (email) DO UPDATE SET "updatedAt" = EXCLUDED."updatedAt"
       RETURNING id`,
      [crypto.randomUUID(), name, email, now],
    )
    const userId = rows[0].id
    const token = crypto.randomBytes(24).toString('base64url')
    await client.query(
      `INSERT INTO "session" (id, token, "userId", "expiresAt", "createdAt", "updatedAt", "userAgent")
       VALUES ($1, $2, $3, $4, $5, $5, 'dev-session')`,
      [crypto.randomUUID(), token, userId, new Date(now.getTime() + 7 * 86_400_000), now],
    )
    const signature = crypto.createHmac('sha256', secret).update(token).digest('base64')
    return {
      userId,
      email,
      cookieName: SESSION_COOKIE,
      cookieValue: encodeURIComponent(`${token}.${signature}`),
    }
  } finally {
    await client.end()
  }
}

async function main() {
  const session = await createDevSession({ email: process.argv[2] || undefined })
  console.log(JSON.stringify(session, null, 2))
  console.error(
    `\nIn a browser: document.cookie = "${session.cookieName}=${session.cookieValue}; path=/"` +
      `\nWith curl:    curl -b '${session.cookieName}=${session.cookieValue}' http://localhost:3000/`,
  )
}

module.exports = { createDevSession, SESSION_COOKIE }

if (require.main === module) {
  main().catch((err) => {
    console.error(err.message)
    process.exit(1)
  })
}
