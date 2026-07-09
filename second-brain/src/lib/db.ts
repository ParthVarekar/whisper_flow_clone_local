import { PrismaClient } from '@prisma/client'

const globalForPrisma = globalThis as unknown as {
  prisma: PrismaClient | undefined
}

// ponytail: query logging disabled — it eats CPU + memory in dev mode and
// contributes to OOM kills on the 4GB sandbox. Re-enable with log: ['query'] if needed.
export const db =
  globalForPrisma.prisma ??
  new PrismaClient()

if (process.env.NODE_ENV !== 'production') globalForPrisma.prisma = db