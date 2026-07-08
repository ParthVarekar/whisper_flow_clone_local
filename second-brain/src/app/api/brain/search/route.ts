import { NextResponse } from 'next/server'
import { db } from '@/lib/db'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

// GET /api/brain/search?q=query
export async function GET(request: Request) {
  const { searchParams } = new URL(request.url)
  const q = searchParams.get('q')?.trim()

  if (!q) {
    return NextResponse.json({ results: [] })
  }

  // SQLite LIKE search across title, body, tags
  const notes = await db.note.findMany({
    where: {
      OR: [
        { title: { contains: q } },
        { body: { contains: q } },
        { tags: { contains: q } },
      ],
    },
    orderBy: { createdAt: 'desc' },
    take: 20,
    include: { tasks: true },
  })

  const results = notes.map(n => {
    // Score: title match = 3, tag match = 2, body match = 1
    let score = 0
    if (n.title.toLowerCase().includes(q.toLowerCase())) score += 3
    if (n.tags.toLowerCase().includes(q.toLowerCase())) score += 2
    if (n.body.toLowerCase().includes(q.toLowerCase())) score += 1
    return { ...n, score }
  }).sort((a, b) => b.score - a.score)

  return NextResponse.json({ results })
}
