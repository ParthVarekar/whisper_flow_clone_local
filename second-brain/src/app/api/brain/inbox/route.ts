import { NextResponse } from 'next/server'
import { db } from '@/lib/db'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

// GET /api/brain/inbox — unreviewed notes for the daily review screen (B3)
//
// Returns notes with status='inbox', newest first, limited to 20 by default.
// Optionally filter by type with ?type=task.
export async function GET(request: Request) {
  const { searchParams } = new URL(request.url)
  const limit = Math.min(parseInt(searchParams.get('limit') || '20'), 100)
  const type = searchParams.get('type')

  const where: any = { status: 'inbox' }
  if (type && type !== 'all') where.type = type

  const notes = await db.note.findMany({
    where,
    orderBy: { createdAt: 'desc' },
    take: limit,
    include: { tasks: true, links: { include: { target: { select: { id: true, title: true } } } } },
  })

  // Count by type for the review header
  const counts = await db.note.groupBy({
    by: ['type'],
    where: { status: 'inbox' },
    _count: { id: true },
  })
  const countByType: Record<string, number> = {}
  for (const c of counts) countByType[c.type] = c._count.id

  return NextResponse.json({
    notes,
    total: notes.length,
    counts: countByType,
  })
}
