import { NextResponse } from 'next/server'
import { db } from '@/lib/db'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'
export const maxDuration = 30

// POST /api/brain/ask — "Ask your brain" — RAG over your notes
// Body: { "question": "what should I work on today?" }
// Returns: { "answer": "...", "sources": [...] }
export async function POST(request: Request) {
  const body = await request.json()
  const question = body.question?.trim()

  if (!question) {
    return NextResponse.json({ error: 'question is required' }, { status: 400 })
  }

  // Step 1: Find relevant notes using keyword search
  const keywords = question.toLowerCase()
    .split(/\s+/)
    .filter(w => w.length > 2 && !['the', 'and', 'for', 'should', 'what', 'how', 'today', 'need', 'most', 'work', 'about', 'with', 'from', 'that', 'this', 'have', 'they', 'them', 'are', 'was', 'were', 'will', 'would', 'could', 'should', 'does', 'did', 'has', 'had'].includes(w))

  // Get all notes (we have <50, fine to load all)
  const allNotes = await db.note.findMany({
    orderBy: { createdAt: 'desc' },
    take: 50,
    include: { tasks: true },
  })

  // Score notes by keyword overlap
  const scored = allNotes.map(n => {
    const text = (n.title + ' ' + n.body + ' ' + n.tags).toLowerCase()
    let score = 0
    for (const kw of keywords) {
      if (text.includes(kw)) score++
    }
    return { note: n, score }
  }).filter(s => s.score > 0).sort((a, b) => b.score - a.score).slice(0, 10)

  // If no keyword matches, use all notes (for small brains)
  const relevantNotes = scored.length > 0 ? scored.map(s => s.note) : allNotes.slice(0, 10)

  if (relevantNotes.length === 0) {
    return NextResponse.json({
      answer: "Your brain is empty. Capture some thoughts first!",
      sources: [],
    })
  }

  // Step 2: Get all incomplete tasks
  const allTasks = await db.task.findMany({
    where: { done: false },
    include: { note: { select: { title: true } } },
  })

  // Step 3: Build context for the LLM
  const notesContext = relevantNotes.map(n =>
    `[${n.type}] ${n.title}\nTags: ${n.tags}\n${n.body}`
  ).join('\n\n---\n\n')

  const tasksContext = allTasks.length > 0
    ? allTasks.map(t => `- ${t.text}${t.due ? ` (due: ${new Date(t.due).toLocaleDateString()})` : ''}`).join('\n')
    : '(no open tasks)'

  // Step 4: Ask the LLM
  try {
    const ZAI = (await import('z-ai-web-dev-sdk')).default
    const zai = await ZAI.create()

    const systemPrompt = `You are the user's "second brain" — an AI assistant that has access to all their captured thoughts, ideas, tasks, and notes.

Answer the user's question based ONLY on the notes and tasks below. Be helpful, specific, and reference specific notes or tasks when relevant.

If the answer isn't in the notes, say so honestly: "I don't have any notes about that yet."

Keep answers concise (2-4 sentences). Reference note titles in [[brackets]] so they're clickable.

Here are the user's notes:
${notesContext}

Here are the user's open tasks:
${tasksContext}`

    const completion = await Promise.race([
      zai.chat.completions.create({
        messages: [
          { role: 'assistant', content: systemPrompt },
          { role: 'user', content: question },
        ],
        thinking: { type: 'disabled' },
        temperature: 0.3,
      }),
      new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error('LLM timeout')), 20000)
      ),
    ]) as any

    const answer = (completion.choices[0]?.message?.content || '').trim()

    return NextResponse.json({
      answer,
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
