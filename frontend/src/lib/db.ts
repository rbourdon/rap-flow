import { Pool, neonConfig } from '@neondatabase/serverless'
import { PrismaNeon } from '@prisma/adapter-neon'
import { PrismaClient } from '@prisma/client'

// Next.js (Node >= 20, or Edge) has built-in WebSocket support.
if (typeof WebSocket !== 'undefined') {
  neonConfig.webSocketConstructor = WebSocket
} else if (typeof globalThis.WebSocket !== 'undefined') {
  neonConfig.webSocketConstructor = globalThis.WebSocket
}

const connectionString = process.env.DATABASE_URL || 'postgresql://postgres:postgres@localhost:5432/postgres'

const pool = new Pool({ connectionString })
// @ts-expect-error - Pool type from serverless doesn't perfectly match PoolConfig expected by PrismaNeon
const adapter = new PrismaNeon(pool)

const globalForPrisma = globalThis as unknown as {
  prisma: PrismaClient | undefined
}

export const prisma = globalForPrisma.prisma ?? new PrismaClient({ adapter })

if (process.env.NODE_ENV !== 'production') globalForPrisma.prisma = prisma
