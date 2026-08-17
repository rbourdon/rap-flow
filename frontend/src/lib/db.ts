import { Pool, neonConfig, PoolConfig } from '@neondatabase/serverless'
import { PrismaNeon } from '@prisma/adapter-neon'
import { PrismaClient } from '@prisma/client'
import ws from 'ws'
import { parse } from 'pg-connection-string'

neonConfig.webSocketConstructor = ws

const connectionString = process.env.DATABASE_URL || 'postgresql://postgres:postgres@localhost:5432/postgres'

const dbConfig = parse(connectionString) as PoolConfig & { options?: string | string[] }
if (Array.isArray(dbConfig.options)) {
  dbConfig.options = dbConfig.options.join(' ')
}

const pool = new Pool(dbConfig as PoolConfig)
// @ts-expect-error - Pool type from serverless doesn't perfectly match PoolConfig expected by PrismaNeon
const adapter = new PrismaNeon(pool)

const globalForPrisma = globalThis as unknown as {
  prisma: PrismaClient | undefined
}

export const prisma = globalForPrisma.prisma ?? new PrismaClient({ adapter })

if (process.env.NODE_ENV !== 'production') globalForPrisma.prisma = prisma
