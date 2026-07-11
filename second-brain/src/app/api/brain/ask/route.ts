import { NextResponse } from 'next/server'
import { db } from '@/lib/db'
import { chat } from '@/lib/llm'
import { TfIdfIndex } from '@/lib/matching'
import { embed, rankBySimilarity } from '@/lib/embeddings'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'
export const maxDuration = 30

// POST /api/brain/ask — "Ask your brain" — RAG over your notes
// Body: { "question": "what should I work on today?" }
// Returns: { "answer": "...", "sources": [...] }
//
// Retrieval strategy (Step 3 upgrade): hybrid semantic + keyword
//   1. Compute the query embedding (all-MiniLM-L6-v2, local)
//   2. Rank all notes by cosine similarity to the query
//   3. Also rank by TF-IDF keyword match
//   4. Merge: semantic score * 0.7 + keyword score * 0.3 (normalized)
//   5. Take top 10 for RAG context
//
// If embeddings are unavailable (model not loaded), falls back to TF-IDF only.
// If TF-IDF also misses, returns honest "I couldn't find anything" (A5).
export async function POST(request: Request) {
  const body = await request.json().catch(() => ({} as any))
  const question = String(body.question || '').trim()
  if (!question) {
    return NextResponse.json({ error: 'question is required' }, { status: 400 })
  }

  // --- Load all notes (with embeddings for semantic search) ---
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

  // --- Semantic search (Step 3) ---
  // Embed the query and rank notes by cosine similarity. Falls back to null
  // if the model is unavailable.
  const queryEmbedding = await embed(question)
  let semanticResults: { id: string; title: string; score: number }[] = []
  if (queryEmbedding) {
    semanticResults = rankBySimilarity(
      queryEmbedding,
      allNotes.map(n => ({ id: n.id, title: n.title, embedding: n.embedding })),
      20,
      0.25,
    )
  }

  // --- Keyword search (TF-IDF, always available) ---
  const index = new TfIdfIndex()
  for (const n of allNotes) {
    index.add(n.id, `${n.title} ${n.body} ${n.tags}`)
  }
  const keywordResults = index.search(question, 20)

  // --- Merge semantic + keyword scores ---
  // Normalize each to 0..1, then weighted sum: semantic 0.7 + keyword 0.3
  const scoreMap = new Map<string, { title: string; semantic: number; keyword: number }>()
  const maxSem = semanticResults.length > 0 ? semanticResults[0].score : 1
  const maxKw = keywordResults.length > 0 ? keywordResults[0].score : 1
  for (const r of semanticResults) {
    scoreMap.set(r.id, { title: r.title, semantic: r.score / maxSem, keyword: 0 })
  }
  for (const r of keywordResults) {
    const existing = scoreMap.get(r.id)
    if (existing) {
      existing.keyword = r.score / maxKw
    } else {
      scoreMap.set(r.id, { title: r.title, semantic: 0, keyword: r.score / maxKw })
    }
  }

  const hasSemantic = semanticResults.length > 0
  const merged = [...scoreMap.entries()]
    .map(([id, v]) => ({
      id,
      title: v.title,
      score: hasSemantic ? v.semantic * 0.7 + v.keyword * 0.3 : v.keyword,
    }))
    .filter(r => r.score > 0.1)
    .sort((a, b) => b.score - a.score)
    .slice(0, 10)

  // A5: if retrieval found nothing relevant, be honest
  if (merged.length === 0) {
    return NextResponse.json({
      answer: "I couldn't find anything about that in your notes. Try rephrasing, or capture a thought on this topic first.",
      sources: [],
    })
  }

  const relevantNotes = merged
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
