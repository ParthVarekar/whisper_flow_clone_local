import { NextResponse } from 'next/server'
import { db } from '@/lib/db'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

// POST /api/brain/capture — receives a transcript, classifies it with LLM, stores note
export async function POST(request: Request) {
  const body = await request.json()
  const { transcript, appContext, source } = body

  if (!transcript || !transcript.trim()) {
    return NextResponse.json({ error: 'transcript is required' }, { status: 400 })
  }

  // Classify with z-ai LLM
  let classification = {
    type: 'journal',
    tags: 'inbox',
    tasks: [] as { text: string; due: string | null }[],
    title: transcript.slice(0, 60),
    confidence: 0.0,
  }

  try {
    const ZAI = (await import('z-ai-web-dev-sdk')).default
    const zai = await ZAI.create()

    const systemPrompt = `You are a note classification assistant. Classify the user's voice transcript.

Respond with ONLY valid JSON, no markdown, no explanation:
{"type": "idea|task|reference|question|journal", "tags": "comma-separated tags", "title": "short title (max 60 chars)", "tasks": [{"text": "actionable task", "due": "ISO date or null"}], "confidence": 0.0-1.0}

Rules:
- "idea": user is expressing a thought, "what if", "maybe", "I was thinking"
- "task": user wants to do something, "remind me", "I need to", "let's", "by Friday"
- "reference": user is quoting or citing something, "according to", "X said"
- "question": user is asking something, "how do", "what about", "I wonder"
- "journal": default when unclear
- Extract actionable tasks with due dates if mentioned (parse "Friday" to next Friday's ISO date)
- Tags: 1-5 lowercase kebab-case topic tags
- Keep tags relevant to the content`

    const completion = await Promise.race([
      zai.chat.completions.create({
        messages: [
          { role: 'assistant', content: systemPrompt },
          { role: 'user', content: transcript },
        ],
        thinking: { type: 'disabled' },
        temperature: 0.0,
      }),
      new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error('classification timeout')), 15000)
      ),
    ]) as any

    const raw = (completion.choices[0]?.message?.content || '').trim()
    // Extract JSON from response (handle markdown code fences)
    const jsonMatch = raw.match(/\{[\s\S]*\}/)
    if (jsonMatch) {
      const parsed = JSON.parse(jsonMatch[0])
      classification = {
        type: parsed.type || 'journal',
        tags: parsed.tags || 'inbox',
        title: parsed.title || transcript.slice(0, 60),
        tasks: Array.isArray(parsed.tasks) ? parsed.tasks : [],
        confidence: parsed.confidence || 0.5,
      }
    }
  } catch (err: any) {
    // LLM failed — store as uncategorized journal
    console.error('[brain] classification failed:', err?.message)
  }

  // Store in database
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
      tasks: {
        create: classification.tasks.map((t: any) => ({
          text: t.text,
          due: t.due ? new Date(t.due) : null,
        })),
      },
    },
    include: { tasks: true },
  })

  // Find related notes (simple keyword matching for now; embeddings later)
  const words = transcript.toLowerCase().split(/\s+/).filter(w => w.length > 3).slice(0, 5)
  let relatedNotes: any[] = []
  if (words.length > 0) {
    // Simple LIKE search for related notes
    const allNotes = await db.note.findMany({
      where: { id: { not: note.id } },
      take: 100,
      orderBy: { createdAt: 'desc' },
    })
    relatedNotes = allNotes
      .map(n => {
        const nwords = (n.body + ' ' + n.tags).toLowerCase()
        let score = 0
        for (const w of words) if (nwords.includes(w)) score++
        return { note: n, score }
      })
      .filter(r => r.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, 3)
  }

  // Create links to related notes
  for (const r of relatedNotes) {
    await db.link.create({
      data: {
        sourceId: note.id,
        targetId: r.note.id,
        similarity: r.score / words.length,
        type: 'keyword',
      },
    })
  }

  return NextResponse.json({
    note,
    relatedNotes: relatedNotes.map(r => ({ id: r.note.id, title: r.note.title, score: r.score })),
  })
}
