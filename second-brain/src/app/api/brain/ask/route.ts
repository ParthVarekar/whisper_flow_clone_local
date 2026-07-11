import { NextResponse } from 'next/server'
import { db } from '@/lib/db'
import { chat } from '@/lib/llm'
import { TfIdfIndex, tokenize } from '@/lib/matching'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'
export const maxDuration = 30

// POST /api/brain/ask — "Ask your brain" — RAG over your notes
// Body: { "question": "what should I work on today?" }
// Returns: { "answer": "...", "sources": [...] }
//
// Phase A fixes applied:
//   A1  — system prompt sent as role:'system' (was 'assistant')
//   A5  — honest "I don't have notes about that" when retrieval misses
//         (previously fell back to the 10 most recent notes regardless of
//         relevance, producing confident hallucinations)
//   C4  — TF-IDF retrieval replaces raw keyword-count overlap (better recall
//         + IDF down-weights words that appear in many notes)
export async function POST(request: Request) {
  const body = await request.json().catch(() => ({} as any))
  const question = String(body.question || '').trim()
  if (!question) {
    return NextResponse.json({ error: 'question is required' }, { status: 400 })
  }

  // --- Build TF-IDF index over all notes ---
  const allNotes = await db.note.findMany({
    orderBy: { createdAt: 'desc' },
    take: 500,
    include: { tasks: true },
  })

  if (allNotes.length === 0) {
    return NextResponse.json({
      answer: "Your brain is empty. Capture some thoughts first (Ctrl+Shift+B or the Speak button).",
      sources: [],
    })
  }

  const index = new TfIdfIndex()
  for (const n of allNotes) {
    index.add(n.id, `${n.title} ${n.body} ${n.tags}`)
  }
  const ranked = index.search(question, 10)

  // A5: if retrieval found nothing relevant, be honest — don't fall back to
  // unrelated notes (that produced hallucinations in the old code).
  if (ranked.length === 0) {
    return NextResponse.json({
      answer: "I couldn't find anything about that in your notes. Try rephrasing, or capture a thought on this topic first.",
      sources: [],
    })
  }

  const relevantNotes = ranked
    .map(r => allNotes.find(n => n.id === r.id))
    .filter((n): n is NonNullable<typeof n> => !!n)

  // --- Get open tasks (for context) ---
  const allTasks = await db.task.findMany({
    where: { done: false },
    include: { note: { select: { title: true } } },
    take: 50,
    orderBy: { due: 'asc' },
  })

  // --- Build context for the LLM ---
  const notesContext = relevantNotes
    .map(n => `[${n.type}] ${n.title}\nTags: ${n.tags}\n${n.body}`)
    .join('\n\n---\n\n')

  const tasksContext = allTasks.length > 0
    ? allTasks.map(t => `- ${t.text}${t.due ? ` (due: ${new Date(t.due).toLocaleDateString()})` : ''} [from: ${t.note.title}]`).join('\n')
    : '(no open tasks)'

  const systemPrompt = `You are the user's "second brain" — an AI assistant that has access to all their captured thoughts, ideas, tasks, and notes.

Answer the user's question based ONLY on the notes and tasks below. Be helpful, specific, and reference specific notes or tasks when relevant.

If the answer isn't in the notes, say so honestly: "I don't have any notes about that yet." Do NOT speculate beyond what's in the notes.

Keep answers concise (2-4 sentences). Reference note titles in [[brackets]] so they're clickable.

Here are the user's notes (most relevant first):
${notesContext}

Here are the user's open tasks:
${tasksContext}`

  try {
    const answer = await chat(systemPrompt, question, { temperature: 0.3, timeoutMs: 20_000 })
    return NextResponse.json({
      answer: answer || "I couldn't generate an answer. Please try again.",
      sources: relevantNotes.slice(0, 5).map(n => ({
        id: n.id,
        title: n.title,
        type: n.type,
        tags: n.tags,
      })),
    })
  } catch (err: any) {
    return NextResponse.json({
      answer: `I couldn't process that right now: ${err?.message || err}`,
      sources: [],
    })
  }
}

// keep tokenize import used (for potential future query-expansion)
void tokenize
