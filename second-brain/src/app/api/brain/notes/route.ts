import { NextResponse } from 'next/server'
import { db } from '@/lib/db'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

// GET /api/brain/notes — list all notes, optionally filtered by type/tag
export async function GET(request: Request) {
  const { searchParams } = new URL(request.url)
  const type = searchParams.get('type')
  const tag = searchParams.get('tag')
  const limit = Math.min(parseInt(searchParams.get('limit') || '50'), 200)

  const where: any = {}
  if (type && type !== 'all') where.type = type
  if (tag) where.tags = { contains: tag }

  const notes = await db.note.findMany({
    where,
    orderBy: { createdAt: 'desc' },
    take: limit,
    include: { tasks: true },
  })

  return NextResponse.json({ notes })
}

// POST /api/brain/notes — create a note manually (text input)
export async function POST(request: Request) {
  const body = await request.json()
  const { title, body: text, type, tags } = body

  if (!text) {
    return NextResponse.json({ error: 'body is required' }, { status: 400 })
  }

  const note = await db.note.create({
    data: {
      title: title || text.slice(0, 60),
      body: text,
      type: type || 'journal',
      tags: tags || '',
      source: 'text',
    },
    include: { tasks: true },
  })

  return NextResponse.json({ note })
}

// DELETE /api/brain/notes?id=xxx
export async function DELETE(request: Request) {
  const { searchParams } = new URL(request.url)
  const id = searchParams.get('id')
  if (!id) return NextResponse.json({ error: 'id required' }, { status: 400 })

  await db.note.delete({ where: { id } })
  return NextResponse.json({ ok: true })
}
