import { Pool, neonConfig } from '@neondatabase/serverless'
import { PrismaNeon } from '@prisma/adapter-neon'
import { PrismaClient } from '@prisma/client'
import { WebSocket } from 'ws'

neonConfig.webSocketConstructor = WebSocket

const connectionString =
  typeof process.env.DATABASE_URL === 'string' && process.env.DATABASE_URL.length > 0
    ? process.env.DATABASE_URL
    : '******localhost:5432/postgres'

const pool = new Pool({ connectionString })
// @ts-expect-error - Pool type from serverless doesn't perfectly match PoolConfig expected by PrismaNeon
const adapter = new PrismaNeon(pool)

const globalForPrisma = globalThis as unknown as {
  prisma: PrismaClient | undefined
}

export const prisma = globalForPrisma.prisma ?? new PrismaClient({ adapter })

if (process.env.NODE_ENV !== 'production') globalForPrisma.prisma = prisma
