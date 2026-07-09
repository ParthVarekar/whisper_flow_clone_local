import { NextResponse } from 'next/server'
import { db } from '@/lib/db'
import { chat, extractJSON } from '@/lib/llm'
import { parseDueDate, sanitizeDueDate, todayContext } from '@/lib/dates'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

// GET /api/brain/notes — list all notes, optionally filtered by type/tag/status
export async function GET(request: Request) {
  const { searchParams } = new URL(request.url)
  const type = searchParams.get('type')
  const tag = searchParams.get('tag')
  const status = searchParams.get('status') // inbox | processed | archived | all
  const limit = Math.min(parseInt(searchParams.get('limit') || '100'), 500)

  const where: any = {}
  if (type && type !== 'all') where.type = type
  if (tag) where.tags = { contains: tag }
  // By default, hide archived notes from the timeline. Explicit status=all shows everything.
  if (status && status !== 'all') where.status = status
  else if (!status) where.status = { not: 'archived' }

  const notes = await db.note.findMany({
    where,
    orderBy: { createdAt: 'desc' },
    take: limit,
    include: { tasks: true },
  })

  return NextResponse.json({ notes })
}

// POST /api/brain/notes — create a note manually (typed input)
// A7: previously this created a bare note with no classification, no tags, no
// type — polluting search and the timeline. Now it runs the same classifier
// as voice capture so manual notes are first-class brain citizens.
export async function POST(request: Request) {
  const body = await request.json().catch(() => ({} as any))
  const { title, body: text, type, tags, source } = body

  if (!text || !String(text).trim()) {
    return NextResponse.json({ error: 'body is required' }, { status: 400 })
  }

  // If the caller explicitly supplies type+tags (e.g. the edit flow), respect them.
  const hasExplicitClassification = typeof type === 'string' && type && typeof tags === 'string'

  let finalType = 'journal'
  let finalTags = ''
  let finalTitle = String(title || text.slice(0, 60)).slice(0, 120)
  let confidence = 0.0
  const tasks: { text: string; due: Date | null }[] = []

  if (!hasExplicitClassification) {
    // Run the classifier on the typed text
    try {
      const systemPrompt = `You are a note classification assistant for a personal "second brain" app. Classify the user's typed thought.

${todayContext()}

Respond with ONLY valid JSON, no markdown fences, no explanation. The response must match exactly this shape:
{"type": "idea|task|reference|question|journal", "tags": "comma-separated-kebab-case-tags", "title": "short title (max 60 chars)", "tasks": [{"text": "actionable task in imperative form", "due": "YYYY-MM-DD or null"}], "confidence": 0.0-1.0}

Rules:
- "idea": the user is expressing a thought or possibility
- "task": the user wants to do something
- "reference": the user is quoting or citing
- "question": the user is asking something
- "journal": default when no clear signal
- Extract 1-5 lowercase kebab-case topic tags (no # prefix)
- Extract actionable tasks only when the user clearly intends to do something. Parse relative dates to YYYY-MM-DD using today's date as the anchor. Use null for tasks with no date.
- "confidence" reflects how clearly the thought matched one of the five types (0.0 = pure guess, 1.0 = unambiguous)`

      const raw = await chat(systemPrompt, String(text), { temperature: 0.0, timeoutMs: 15_000 })
      const parsed = extractJSON(raw)
      if (parsed) {
        const VALID = new Set(['idea', 'task', 'reference', 'question', 'journal', 'dictation'])
        finalType = VALID.has(String(parsed.type).toLowerCase()) ? String(parsed.type).toLowerCase() : 'journal'
        const tagArr = (Array.isArray(parsed.tags) ? parsed.tags : String(parsed.tags || '').split(','))
          .map((t: string) => String(t).trim().toLowerCase().replace(/\s+/g, '-').replace(/^#/, ''))
          .filter(Boolean)
        finalTags = [...new Set(tagArr)].slice(0, 5).join(',')
        if (parsed.title) finalTitle = String(parsed.title).slice(0, 120)
        confidence = Math.max(0, Math.min(1, Number(parsed.confidence) || 0.5))
        if (Array.isArray(parsed.tasks)) {
          for (const t of parsed.tasks.slice(0, 10)) {
            if (t && typeof t.text === 'string' && t.text.trim()) {
              tasks.push({ text: t.text.trim().slice(0, 500), due: sanitizeDueDate(parseDueDate(t.due)) })
            }
          }
        }
        // Low-confidence → inbox
        if (confidence < 0.5) {
          finalType = 'journal'
          finalTags = finalTags ? (finalTags.split(',').includes('inbox') ? finalTags : 'inbox,' + finalTags) : 'inbox'
        }
      } else {
        finalTags = 'inbox'
      }
    } catch (err: any) {
      console.error('[brain] manual-note classification failed:', err?.message)
      finalType = 'journal'
      finalTags = 'inbox'
    }
  } else {
    finalType = type
    finalTags = tags
  }

  const note = await db.note.create({
    data: {
      title: finalTitle,
      body: String(text),
      type: finalType,
      tags: finalTags,
      source: source || 'text',
      confidence,
      status: 'inbox',
      tasks: tasks.length > 0 ? { create: tasks } : undefined,
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
