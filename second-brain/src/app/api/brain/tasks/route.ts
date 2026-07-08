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
export async function PATCH(request: Request) {
  const body = await request.json()
  const { id, done } = body

  const task = await db.task.update({
    where: { id: parseInt(id) },
    data: { done },
  })

  return NextResponse.json({ task })
}
