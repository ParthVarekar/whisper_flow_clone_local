import { NextResponse } from 'next/server'
import { db } from '@/lib/db'
import { chat, extractJSON } from '@/lib/llm'
import { parseDueDate, sanitizeDueDate, todayContext } from '@/lib/dates'
import { tokenize } from '@/lib/matching'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

// PATCH /api/brain/notes/[id] — edit a note inline (B1)
//
// Body can include any subset of:
//   { title, body, type, tags, status }
//
// All fields are optional; only provided fields are updated. If body changes,
// we recompute related-note links (the old links are deleted and new ones
// computed from the new text).
export async function PATCH(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params
  const body = await request.json().catch(() => ({} as any))

  const data: any = {}
  if (typeof body.title === 'string') data.title = body.title.trim().slice(0, 200) || undefined
  if (typeof body.body === 'string') data.body = body.body
  if (typeof body.type === 'string') {
    const VALID = new Set(['idea', 'task', 'reference', 'question', 'journal', 'dictation', 'uncategorized'])
    data.type = VALID.has(body.type) ? body.type : 'journal'
  }
  if (typeof body.tags === 'string') {
    const tagArr = body.tags.split(',').map(t => t.trim().toLowerCase().replace(/\s+/g, '-').replace(/^#/, '')).filter(Boolean)
    data.tags = [...new Set(tagArr)].slice(0, 10).join(',')
  }
  if (typeof body.status === 'string') {
    const VALID_STATUS = new Set(['inbox', 'processed', 'archived'])
    data.status = VALID_STATUS.has(body.status) ? body.status : 'inbox'
    if (data.status === 'processed') data.reviewedAt = new Date()
  }

  if (Object.keys(data).length === 0) {
    return NextResponse.json({ error: 'no fields to update' }, { status: 400 })
  }

  const note = await db.note.update({
    where: { id },
    data,
    include: { tasks: true, links: { include: { target: true } }, backlinks: { include: { source: true } } },
  })

  // If body changed, recompute related-note links
  if (typeof body.body === 'string') {
    await db.link.deleteMany({ where: { sourceId: id, type: 'keyword' } })
    const related = await findRelatedNotes(id, note.body, note.tags)
    for (const r of related) {
      await db.link.create({
        data: { sourceId: id, targetId: r.id, similarity: r.score, type: 'keyword' },
      })
    }
  }

  return NextResponse.json({ note })
}

// POST /api/brain/notes/[id]/reclassify — re-run the LLM classifier on a note
// (B1 reclassify button). Updates type/tags/title/tasks based on the current body.
export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params
  const note = await db.note.findUnique({ where: { id }, include: { tasks: true } })
  if (!note) {
    return NextResponse.json({ error: 'note not found' }, { status: 404 })
  }

  let result: { type: string; tags: string; title: string; tasks: { text: string; due: Date | null }[]; confidence: number }

  try {
    const systemPrompt = `You are a note classification assistant for a personal "second brain" app. Classify the user's thought.

${todayContext()}

Respond with ONLY valid JSON, no markdown fences, no explanation. The response must match exactly this shape:
{"type": "idea|task|reference|question|journal", "tags": "comma-separated-kebab-case-tags", "title": "short title (max 60 chars)", "tasks": [{"text": "actionable task in imperative form", "due": "YYYY-MM-DD or null"}], "confidence": 0.0-1.0}

Rules:
- "idea": expressing a thought or possibility
- "task": wants to do something
- "reference": quoting or citing
- "question": asking something
- "journal": default when no clear signal
- Extract 1-5 lowercase kebab-case topic tags (no # prefix)
- Extract actionable tasks only when the user clearly intends to do something. Parse relative dates to YYYY-MM-DD using today's date as the anchor.
- "confidence" reflects how clearly the thought matched one of the five types`

    const raw = await chat(systemPrompt, note.body, { temperature: 0.0, timeoutMs: 15_000 })
    const parsed = extractJSON(raw)
    if (!parsed) {
      return NextResponse.json({ error: 'classification failed — no valid JSON from LLM' }, { status: 502 })
    }

    const VALID = new Set(['idea', 'task', 'reference', 'question', 'journal', 'dictation'])
    let type = String(parsed.type || 'journal').toLowerCase().trim()
    if (!VALID.has(type)) type = 'journal'

    const tagArr = (Array.isArray(parsed.tags) ? parsed.tags : String(parsed.tags || '').split(','))
      .map((t: string) => String(t).trim().toLowerCase().replace(/\s+/g, '-').replace(/^#/, ''))
      .filter(Boolean)
    let tags = [...new Set(tagArr)].slice(0, 5).join(',')

    let confidence = Math.max(0, Math.min(1, Number(parsed.confidence) || 0.5))
    if (confidence < 0.5) {
      type = 'journal'
      tags = tags ? (tags.split(',').includes('inbox') ? tags : 'inbox,' + tags) : 'inbox'
    }

    const tasks = Array.isArray(parsed.tasks)
      ? parsed.tasks.filter((t: any) => t && typeof t.text === 'string' && t.text.trim()).slice(0, 10)
          .map((t: any) => ({ text: String(t.text).trim().slice(0, 500), due: sanitizeDueDate(parseDueDate(t.due)) }))
      : []

    result = {
      type,
      tags,
      title: String(parsed.title || note.title).slice(0, 120),
      tasks,
      confidence,
    }
  } catch (err: any) {
    return NextResponse.json({ error: `classification failed: ${err?.message || err}` }, { status: 502 })
  }

  // Update the note + replace its tasks
  const updated = await db.$transaction(async (tx) => {
    await tx.task.deleteMany({ where: { noteId: id } })
    return tx.note.update({
      where: { id },
      data: {
        title: result.title,
        type: result.type,
        tags: result.tags,
        confidence: result.confidence,
        tasks: result.tasks.length > 0 ? { create: result.tasks } : undefined,
      },
      include: { tasks: true, links: { include: { target: true } }, backlinks: { include: { source: true } } },
    })
  })

  return NextResponse.json({ note: updated })
}

// --- Helper (mirrors capture route's findRelatedNotes) ---
async function findRelatedNotes(selfId: string, transcript: string, tags: string): Promise<{ id: string; title: string; score: number }[]> {
  const queryTokens = tokenize(`${transcript} ${tags}`)
  if (queryTokens.length === 0) return []

  const candidates = await db.note.findMany({
    where: { id: { not: selfId } },
    select: { id: true, title: true, body: true, tags: true },
    orderBy: { createdAt: 'desc' },
    take: 500,
  })

  const querySet = new Set(queryTokens)
  const scored = candidates
    .map(n => {
      const docTokens = new Set(tokenize(`${n.title} ${n.body} ${n.tags}`))
      let hits = 0
      for (const t of querySet) if (docTokens.has(t)) hits++
      const union = new Set([...querySet, ...docTokens]).size
      const score = union > 0 ? hits / union : 0
      return { id: n.id, title: n.title, score }
    })
    .filter(r => r.score >= 0.15)
    .sort((a, b) => b.score - a.score)
    .slice(0, 3)

  return scored
}
