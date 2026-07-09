import { NextResponse } from 'next/server'
import { db } from '@/lib/db'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

// GET /api/brain/tasks — list all tasks
export async function GET() {
  const tasks = await db.task.findMany({
    orderBy: [{ done: 'asc' }, { due: 'asc' }],
    include: { note: { select: { id: true, title: true } } },
  })
  return NextResponse.json({ tasks })
}

// PATCH /api/brain/tasks — toggle task completion
// Body: { id, done }
export async function PATCH(request: Request) {
  const body = await request.json().catch(() => ({} as any))
  const { id, done } = body
  if (typeof id !== 'number' || typeof done !== 'boolean') {
    return NextResponse.json({ error: 'id (number) and done (boolean) required' }, { status: 400 })
  }

  const task = await db.task.update({
    where: { id },
    data: { done },
    include: { note: { select: { id: true, title: true } } },
  })

  return NextResponse.json({ task })
}

// POST /api/brain/tasks — create a task on a note
// Body: { noteId, text, due? }
export async function POST(request: Request) {
  const body = await request.json().catch(() => ({} as any))
  const { noteId, text, due } = body
  if (!noteId || !text || !String(text).trim()) {
    return NextResponse.json({ error: 'noteId and text required' }, { status: 400 })
  }

  const note = await db.note.findUnique({ where: { id: String(noteId) } })
  if (!note) return NextResponse.json({ error: 'note not found' }, { status: 404 })

  const dueDate = due ? new Date(due) : null
  if (dueDate && isNaN(dueDate.getTime())) {
    return NextResponse.json({ error: 'invalid due date' }, { status: 400 })
  }

  const task = await db.task.create({
    data: {
      noteId: String(noteId),
      text: String(text).trim().slice(0, 500),
      due: dueDate,
    },
    include: { note: { select: { id: true, title: true } } },
  })

  return NextResponse.json({ task })
}

// DELETE /api/brain/tasks?id=N
export async function DELETE(request: Request) {
  const { searchParams } = new URL(request.url)
  const id = searchParams.get('id')
  if (!id) return NextResponse.json({ error: 'id required' }, { status: 400 })

  await db.task.delete({ where: { id: parseInt(id) } })
  return NextResponse.json({ ok: true })
}
