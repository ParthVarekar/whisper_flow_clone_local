import { NextResponse } from 'next/server'
import { db } from '@/lib/db'
import { embed, isEmbeddingAvailable } from '@/lib/embeddings'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'
export const maxDuration = 60

// POST /api/brain/backfill — compute embeddings for notes that don't have one yet.
//
// This is run once after installing the embedding model, to backfill existing
// notes. It's also safe to run repeatedly — it skips notes that already have
// an embedding.
//
// Body: { limit?: number }  (default 100, max 500)
// Returns: { processed, skipped, failed, available }
export async function POST(request: Request) {
  const body = await request.json().catch(() => ({} as any))
  const limit = Math.min(parseInt(body.limit) || 100, 500)

  if (!isEmbeddingAvailable()) {
    return NextResponse.json({
      processed: 0,
      skipped: 0,
      failed: 0,
      available: false,
      message: 'Embedding model is not available. The model may still be downloading, or the @xenova/transformers package failed to load. Check server logs.',
    }, { status: 503 })
  }

  const notes = await db.note.findMany({
    where: { embedding: null },
    select: { id: true, title: true, body: true, tags: true },
    take: limit,
    orderBy: { createdAt: 'desc' },
  })

  let processed = 0
  let failed = 0

  for (const note of notes) {
    const emb = await embed(`${note.title} ${note.body} ${note.tags}`)
    if (emb) {
      await db.note.update({ where: { id: note.id }, data: { embedding: emb } })
      processed++
    } else {
      failed++
    }
  }

  return NextResponse.json({
    processed,
    skipped: 0,
    failed,
    available: true,
    message: `Computed embeddings for ${processed} note(s).`,
  })
}
