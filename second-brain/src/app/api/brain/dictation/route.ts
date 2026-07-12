import { NextResponse } from 'next/server'
import { db } from '@/lib/db'
import { chat, extractJSON } from '@/lib/llm'
import { parseDueDate, sanitizeDueDate, todayContext } from '@/lib/dates'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'
export const maxDuration = 30

// POST /api/brain/dictation — log a Wispr Flow dictation to the brain (B4)
//
// This is the integration point between the Wispr Flow voice daemon
// (whisper_flow/ Python app) and the Second Brain. After Wispr Flow pastes
// dictated text into a focused app, it can optionally POST the text here to
// log it as a brain note. The note is classified the same way as a voice
// capture, but with source='dictation' and the focused app's name recorded.
//
// Body: { text, appContext?, autoClassify? }
//   - text:          the dictated text (already cleaned by Wispr Flow)
//   - appContext:    the focused app name (e.g. "Slack", "VS Code")
//   - autoClassify:  if false, store as type='dictation' + tag '#dictation-log'
//                    without an LLM call (faster, for "just log it" mode)
//
// Returns: { note, relatedNotes }
export async function POST(request: Request) {
  const body = await request.json().catch(() => ({} as any))
  const { text, appContext, autoClassify } = body

  if (!text || !String(text).trim()) {
    return NextResponse.json({ error: 'text is required' }, { status: 400 })
  }

  let type = 'dictation'
  let tags = 'dictation-log'
  let title = String(text).slice(0, 60)
  let confidence = 0.0
  const tasks: { text: string; due: Date | null }[] = []

  if (autoClassify !== false) {
    // Run the same classifier as capture, but default to 'dictation' type
    try {
      const systemPrompt = `You are a note classification assistant for a personal "second brain" app. The user dictated this text into another app (it was already cleaned up by a dictation tool). Classify it.

${todayContext()}

Respond with ONLY valid JSON, no markdown fences, no explanation:
{"type": "idea|task|reference|question|journal|dictation", "tags": "comma-separated-kebab-case-tags", "title": "short title (max 60 chars)", "tasks": [{"text": "actionable task", "due": "YYYY-MM-DD or null"}], "confidence": 0.0-1.0}

Rules:
- "dictation": default for dictated text that's just being logged
- "task": the user clearly committed to doing something
- Other types apply if the dictation is clearly an idea/reference/question
- Extract 1-5 lowercase kebab-case topic tags
- Always include 'dictation-log' as one of the tags
- Extract actionable tasks only when clearly intended. Parse relative dates to YYYY-MM-DD.`

      const raw = await chat(systemPrompt, String(text), { temperature: 0.0, timeoutMs: 15_000 })
      const parsed = extractJSON(raw)
      if (parsed) {
        const VALID = new Set(['idea', 'task', 'reference', 'question', 'journal', 'dictation'])
        type = VALID.has(String(parsed.type).toLowerCase()) ? String(parsed.type).toLowerCase() : 'dictation'
        const tagArr = (Array.isArray(parsed.tags) ? parsed.tags : String(parsed.tags || '').split(','))
          .map((t: string) => String(t).trim().toLowerCase().replace(/\s+/g, '-').replace(/^#/, ''))
          .filter(Boolean)
        // Always ensure dictation-log is present
        if (!tagArr.includes('dictation-log')) tagArr.unshift('dictation-log')
        tags = [...new Set(tagArr)].slice(0, 5).join(',')
        if (parsed.title) title = String(parsed.title).slice(0, 120)
        confidence = Math.max(0, Math.min(1, Number(parsed.confidence) || 0.5))
        if (Array.isArray(parsed.tasks)) {
          for (const t of parsed.tasks.slice(0, 10)) {
            if (t && typeof t.text === 'string' && t.text.trim()) {
              tasks.push({ text: t.text.trim().slice(0, 500), due: sanitizeDueDate(parseDueDate(t.due)) })
            }
          }
        }
      }
    } catch (err: any) {
      console.error('[brain] dictation classification failed:', err?.message)
      // Fall through with default dictation type
    }
  }

  const note = await db.note.create({
    data: {
      title,
      body: String(text),
      rawTranscript: String(text),
      type,
      tags,
      source: 'dictation',
      appContext: appContext || null,
      confidence,
      status: 'inbox',
      tasks: tasks.length > 0 ? { create: tasks } : undefined,
    },
    include: { tasks: true },
  })

  return NextResponse.json({
    note,
    relatedNotes: [],
    message: `Logged to brain as ${type}`,
  })
}
