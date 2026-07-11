import { NextResponse } from 'next/server'
import { db } from '@/lib/db'
import { embed, rankBySimilarity } from '@/lib/embeddings'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

// GET /api/brain/search?q=query
//
// Hybrid search (Step 3): semantic + keyword. Returns up to 20 results.
//   1. Keyword: SQLite LIKE on title/body/tags (exact substring match)
//   2. Semantic: cosine similarity on embeddings (meaning match)
//   3. Merge + dedupe, semantic results first (higher quality), then keyword-only.
//
// If embeddings are unavailable, falls back to keyword-only search.
export async function GET(request: Request) {
  const { searchParams } = new URL(request.url)
  const q = searchParams.get('q')?.trim()

  if (!q) {
    return NextResponse.json({ results: [] })
  }

  // --- Keyword search (SQLite LIKE) ---
  const keywordNotes = await db.note.findMany({
    where: {
      OR: [
        { title: { contains: q } },
        { body: { contains: q } },
        { tags: { contains: q } },
      ],
    },
    orderBy: { createdAt: 'desc' },
    take: 30,
    include: { tasks: true },
  })

  const keywordScored = keywordNotes.map(n => {
    let score = 0
    if (n.title.toLowerCase().includes(q.toLowerCase())) score += 3
    if (n.tags.toLowerCase().includes(q.toLowerCase())) score += 2
    if (n.body.toLowerCase().includes(q.toLowerCase())) score += 1
    return { note: n, keywordScore: score, semanticScore: 0 }
  })

  // --- Semantic search ---
  const queryEmbedding = await embed(q)
  let semanticResults: { id: string; title: string; score: number }[] = []
  if (queryEmbedding) {
    // Load all notes with embeddings (not just keyword matches)
    const allWithEmbeddings = await db.note.findMany({
      where: { embedding: { not: null } },
      select: { id: true, title: true, embedding: true },
      take: 500,
      orderBy: { createdAt: 'desc' },
    })
    semanticResults = rankBySimilarity(queryEmbedding, allWithEmbeddings, 20, 0.3)
  }

  // --- Merge: start with keyword results, add semantic results that aren't
  //     already in the keyword set. Semantic matches get a bonus. ---
  const mergedMap = new Map<string, { note: typeof keywordNotes[0]; keywordScore: number; semanticScore: number }>()
  for (const r of keywordScored) {
    mergedMap.set(r.note.id, r)
  }

  // Add semantic results (fetch full note for ones not already in keyword set)
  const semanticOnlyIds = semanticResults.map(r => r.id).filter(id => !mergedMap.has(id))
  if (semanticOnlyIds.length > 0) {
    const semanticNotes = await db.note.findMany({
      where: { id: { in: semanticOnlyIds } },
      include: { tasks: true },
    })
    for (const r of semanticResults) {
      const note = semanticNotes.find(n => n.id === r.id)
      if (note) {
        mergedMap.set(r.id, { note, keywordScore: 0, semanticScore: r.score })
      }
    }
  }

  // Update semantic scores for notes that appear in both
  for (const r of semanticResults) {
    const existing = mergedMap.get(r.id)
    if (existing) existing.semanticScore = r.score
  }

  // Final score: semantic * 0.6 + keyword * 0.4 (normalized)
  const results = [...mergedMap.values()]
    .map(r => ({
      ...r.note,
      score: r.semanticScore * 0.6 + (r.keywordScore / 6) * 0.4, // normalize keyword to 0..1
      keywordScore: r.keywordScore,
      semanticScore: r.semanticScore,
    }))
    .sort((a, b) => b.score - a.score)
    .slice(0, 20)

  return NextResponse.json({ results })
}
