import { Pool, neonConfig } from '@neondatabase/serverless'
import { PrismaNeon } from '@prisma/adapter-neon'
import { PrismaClient } from '@prisma/client'
import { WebSocket } from 'ws'

neonConfig.webSocketConstructor = WebSocket

const globalForPrisma = globalThis as unknown as {
  prisma: PrismaClient | undefined
}

// DATABASE_URL is only provided at runtime (not at build time), so the Prisma
// client must be created lazily on first use rather than at module load. This
// keeps `next build`'s page-data collection (which imports this module) from
// requiring a database connection string.
function createPrismaClient(): PrismaClient {
  const connectionString = process.env.DATABASE_URL
  if (typeof connectionString !== 'string' || connectionString.length === 0) {
    throw new Error('DATABASE_URL environment variable is not set')
  }

  const pool = new Pool({ connectionString })
  // @ts-expect-error - Pool type from serverless doesn't perfectly match PoolConfig expected by PrismaNeon
  const adapter = new PrismaNeon(pool)
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
