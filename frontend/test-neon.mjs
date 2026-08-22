import { neonConfig } from '@neondatabase/serverless'
import { PrismaNeon } from '@prisma/adapter-neon'
import ws from 'ws'

neonConfig.webSocketConstructor = ws
neonConfig.wsProxy = (host, port) => `127.0.0.1:5555/v1?address=${host}:${port}`
neonConfig.useSecureWebSocket = false
neonConfig.pipelineTLS = false
neonConfig.forceDisablePgSSL = true

const connectionString = 'postgresql://postgres@127.0.0.1:5432/testdb'
const adapter = new PrismaNeon({ connectionString })

const { PrismaClient } = await import('@prisma/client')
const prisma = new PrismaClient({ adapter })

try {
  const jobs = await prisma.job.findMany({ where: { userId: 'user1' }, orderBy: { createdAt: 'desc' } })
  console.log('SUCCESS', jobs)
} catch (e) {
  console.error('QUERY FAILED')
  console.error(e)
}
process.exit(0)
