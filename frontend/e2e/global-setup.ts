import { execSync } from 'node:child_process'
import pg from 'pg'

// Create the e2e database if needed, sync the schema, and clear old rows.
// Every spec signs in as its own fresh user, so specs never see each other's
// jobs; the wipe only stops the database growing across local runs.
export default async function globalSetup() {
  const url = new URL(process.env.DATABASE_URL!)
  const dbName = url.pathname.slice(1)
  if (!dbName.includes('e2e')) {
    throw new Error(`e2e refuses to run against a database not named *e2e*: ${dbName}`)
  }

  const admin = new URL(url)
  admin.pathname = '/postgres'
  const client = new pg.Client({ connectionString: admin.toString() })
  await client.connect()
  try {
    const { rowCount } = await client.query('SELECT 1 FROM pg_database WHERE datname = $1', [dbName])
    if (!rowCount) await client.query(`CREATE DATABASE "${dbName}"`)
  } finally {
    await client.end()
  }

  execSync('npx --no-install prisma db push', { stdio: 'pipe', env: process.env })

  const db = new pg.Client({ connectionString: url.toString() })
  await db.connect()
  try {
    await db.query('TRUNCATE "user", "session", "account", "verification", "job" CASCADE')
  } finally {
    await db.end()
  }
}
