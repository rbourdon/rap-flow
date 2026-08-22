import { neonConfig } from '@neondatabase/serverless'
import { PrismaNeon } from '@prisma/adapter-neon'
import { PrismaClient } from '@prisma/client'
import ws from 'ws'

neonConfig.webSocketConstructor = ws

const globalForPrisma = globalThis as unknown as {
  prisma: PrismaClient | undefined
}

// DATABASE_URL is only provided at runtime (not at build time), so the Prisma
// client must be created lazily on first use rather than at module load. This
// keeps `next build`'s page-data collection (which imports this module) from
// requiring a database connection string.
function createPrismaClient(): PrismaClient {
  const connectionString = String(process.env.DATABASE_URL)
  if (!connectionString || connectionString === 'undefined') {
    throw new Error('DATABASE_URL environment variable is not set')
  }

  // PrismaNeon is a driver adapter *factory*: it expects the raw Neon pool
  // config (e.g. `{ connectionString }`) and creates its own internal Pool.
  // Passing an already-constructed `Pool` instance here (as this code
  // previously did) causes the Pool's own `options` object to be forwarded
  // to `neon.Pool`'s constructor as if it were part of the connection
  // config, which then leaks into the Postgres startup packet and crashes
  // `addCString` with "Received an instance of Object".
  const adapter = new PrismaNeon({ connectionString })
  return new PrismaClient({ adapter })
}

let cachedPrisma: PrismaClient | undefined

function getPrismaClient(): PrismaClient {
  if (cachedPrisma) return cachedPrisma
  if (globalForPrisma.prisma) {
    cachedPrisma = globalForPrisma.prisma
    return cachedPrisma
  }

  cachedPrisma = createPrismaClient()
  if (process.env.NODE_ENV !== 'production') globalForPrisma.prisma = cachedPrisma
  return cachedPrisma
}

export const prisma = new Proxy({} as PrismaClient, {
  get(_target, prop, receiver) {
    const client = getPrismaClient()
    const value = Reflect.get(client as object, prop, receiver)
    return typeof value === 'function' ? value.bind(client) : value
  },
})
