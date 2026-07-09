import { NextResponse } from 'next/server'
import { db } from '@/lib/db'
import { chat, extractJSON } from '@/lib/llm'
import { tokenize } from '@/lib/matching'
import { parseDueDate, sanitizeDueDate, todayContext } from '@/lib/dates'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

// --- Capture mutex (A11) -------------------------------------------------
// Voice captures can fire in rapid succession (e.g. user hits the hotkey twice
// or double-clicks Speak). Without serialization the two LLM calls + DB writes
// interleave and the related-notes linker races against itself. We serialize
// captures through a single promise chain so the second capture waits for the
// first to fully land before starting.
let _captureChain: Promise<unknown> = Promise.resolve()
function withCaptureLock<T>(fn: () => Promise<T>): Promise<T> {
  const run = _captureChain.then(fn, fn) // run even if previous rejected
  // Don't let a rejection abort the chain for the next caller.
  _captureChain = run.catch(() => {})
  return run
}

// --- Types ---------------------------------------------------------------
type ClassifyResult = {
  type: string
  tags: string
  title: string
  tasks: { text: string; due: Date | null }[]
  confidence: number
}

const VALID_TYPES = new Set(['idea', 'task', 'reference', 'question', 'journal', 'dictation'])

// --- POST /api/brain/capture --------------------------------------------
// Receives a transcript, classifies it with the LLM, stores the note +
// extracted tasks, and links it to the top related notes by keyword overlap.
//
// Phase A fixes applied:
//   A1  — system prompt sent as role:'system' (was 'assistant')
//   A4  — today's date injected so the LLM can resolve "by Friday" correctly;
//         returned due dates are sanitized (no past dates, no >1yr out)
//   A6  — Link.type written as 'keyword' explicitly (matches schema default)
//   A8  — confidence < 0.5 → fallback to journal + #inbox tag
//   A10 — related-notes matching uses stopword-filtered tokenization + a
//         minimum score threshold (was naive substring on first 5 words)
//   A11 — captures serialized through a mutex
export async function POST(request: Request) {
  return withCaptureLock(async () => {
    const body = await request.json().catch(() => ({} as any))
    const { transcript, appContext, source } = body
    if (!transcript || !String(transcript).trim()) {
      return NextResponse.json({ error: 'transcript is required' }, { status: 400 })
    }

    // --- Classify with LLM (with graceful fallback) ---
    let classification: ClassifyResult = await classify(transcript).catch(err => {
      console.error('[brain] classification failed:', err?.message)
      return fallbackClassification(transcript)
    })

    // --- Store in DB ---
    const note = await db.note.create({
      data: {
        title: classification.title,
        body: transcript,
        rawTranscript: transcript,
        type: classification.type,
        tags: classification.tags,
        source: source || 'voice',
        appContext: appContext || null,
        confidence: classification.confidence,
        status: 'inbox', // awaits review (Phase B3)
        tasks: {
          create: classification.tasks.map(t => ({
            text: t.text,
            due: t.due,
          })),
        },
      },
      include: { tasks: true },
    })

    // --- Find + create related-note links (A10) ---
    const related = await findRelatedNotes(note.id, transcript, classification.tags)

    for (const r of related) {
      await db.link.create({
        data: {
          sourceId: note.id,
          targetId: r.id,
          similarity: r.score,
          type: 'keyword', // A6: matches schema default + comment
        },
      })
    }

    return NextResponse.json({
      note,
      relatedNotes: related.map(r => ({ id: r.id, title: r.title, score: r.score })),
    })
  })
}

// --- Helpers -------------------------------------------------------------

async function classify(transcript: string): Promise<ClassifyResult> {
  const systemPrompt = `You are a note classification assistant for a personal "second brain" app. Classify the user's spoken or typed thought.

${todayContext()}

Respond with ONLY valid JSON, no markdown fences, no explanation. The response must match exactly this shape:
{"type": "idea|task|reference|question|journal", "tags": "comma-separated-kebab-case-tags", "title": "short title (max 60 chars)", "tasks": [{"text": "actionable task in imperative form", "due": "YYYY-MM-DD or null"}], "confidence": 0.0-1.0}

Rules:
- "idea": the user is expressing a thought or possibility ("what if", "maybe", "I was thinking")
- "task": the user wants to do something ("remind me", "I need to", "let's", "by Friday")
- "reference": the user is quoting or citing ("according to", "X said")
- "question": the user is asking something ("how do", "what about", "I wonder")
- "journal": default when no clear signal
- Extract 1-5 lowercase kebab-case topic tags (no # prefix, no spaces)
- Extract actionable tasks only when the user clearly intends to do something. Parse relative dates ("Friday", "next week", "tomorrow") to YYYY-MM-DD using today's date as the anchor. Use null for tasks with no date.
- "title" must be a concise summary, not a verbatim slice of the transcript
- "confidence" reflects how clearly the thought matched one of the five types (0.0 = pure guess, 1.0 = unambiguous)`

  const raw = await chat(systemPrompt, transcript, { temperature: 0.0, timeoutMs: 15_000 })
  const parsed = extractJSON(raw)
  if (!parsed) {
    return fallbackClassification(transcript)
  }

  // Validate + sanitize
  let type = String(parsed.type || 'journal').toLowerCase().trim()
  if (!VALID_TYPES.has(type)) type = 'journal'

  let tags = cleanTags(parsed.tags)
  let confidence = Number(parsed.confidence)
  if (!isFinite(confidence)) confidence = 0.5
  confidence = Math.max(0, Math.min(1, confidence))

  // A8: low confidence → fall back to journal + #inbox so it surfaces in review
  if (confidence < 0.5) {
    type = 'journal'
    if (!tags) tags = 'inbox'
    else if (!tags.split(',').includes('inbox')) tags = 'inbox,' + tags
  }

  const tasks = Array.isArray(parsed.tasks)
    ? parsed.tasks
        .filter((t: any) => t && typeof t.text === 'string' && t.text.trim())
        .slice(0, 10)
        .map((t: any) => ({
          text: String(t.text).trim().slice(0, 500),
          due: sanitizeDueDate(parseDueDate(t.due)),
        }))
    : []

  return {
    type,
    tags,
    title: String(parsed.title || transcript.slice(0, 60)).trim().slice(0, 120) || transcript.slice(0, 60),
    tasks,
    confidence,
  }
}

function fallbackClassification(transcript: string): ClassifyResult {
  return {
    type: 'journal',
    tags: 'inbox',
    title: transcript.slice(0, 60),
    tasks: [],
    confidence: 0.0,
  }
}

/** Normalize a tags value (string or array) into a clean comma-separated string. */
function cleanTags(raw: unknown): string {
  let arr: string[]
  if (Array.isArray(raw)) arr = raw
  else if (typeof raw === 'string') arr = raw.split(',')
  else arr = []
  const cleaned = arr
    .map(t => String(t).trim().toLowerCase().replace(/\s+/g, '-').replace(/^#/, ''))
    .filter(Boolean)
  // dedupe, cap at 5
  return [...new Set(cleaned)].slice(0, 5).join(',')
}

/**
 * Find related notes by keyword overlap (A10).
 * Uses stopword-filtered tokenization + a minimum score threshold so that
 * common English words don't link unrelated notes. Scans the most recent
 * 500 notes (configurable). Returns top 3 by score, only those above threshold.
 */
async function findRelatedNotes(
  selfId: string,
  transcript: string,
  tags: string,
): Promise<{ id: string; title: string; score: number }[]> {
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
      // Jaccard-like score: hits / |union|. 0.15 threshold filters noise.
      const union = new Set([...querySet, ...docTokens]).size
      const score = union > 0 ? hits / union : 0
      return { id: n.id, title: n.title, score }
    })
    .filter(r => r.score >= 0.15)
    .sort((a, b) => b.score - a.score)
    .slice(0, 3)

  return scored
}
