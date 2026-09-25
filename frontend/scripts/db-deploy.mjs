// Sync prisma/schema.prisma to $DATABASE_URL. Runs as part of `npm run build`.
//
// Vercel preview builds are skipped by default. The Neon integration hands
// preview deployments the SAME DATABASE_URL as production, so without this
// guard every PR's preview build would push that PR's (unmerged) schema into
// the production database - and with several PRs in flight at once, each
// preview would try to undo the others' columns. Once previews get their own
// database (enable "Create a database branch for preview deployments" on the
// Neon integration in Vercel), set PREVIEW_DB_PUSH=1 for the Preview
// environment to turn schema sync back on for them.
//
// `db push` rather than `migrate deploy` is deliberate; see frontend/README.md.
import { execSync } from 'node:child_process'

const skip = (why) => {
  console.warn(`Skipping prisma db push: ${why}`)
  process.exit(0)
}

if (!process.env.DATABASE_URL) {
  skip('DATABASE_URL is not set')
}

if (process.env.VERCEL_ENV === 'preview' && process.env.PREVIEW_DB_PUSH !== '1') {
  skip(
    'preview deployments share the production database. Schema changes in ' +
      'this PR reach the database when it merges and the production build ' +
      'runs. Set PREVIEW_DB_PUSH=1 once previews use their own Neon branch.',
  )
}

execSync('npx prisma db push', { stdio: 'inherit' })
