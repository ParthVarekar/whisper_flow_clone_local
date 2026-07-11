/**
 * Local embedding model for semantic search (Step 3).
 *
 * Uses @xenova/transformers (Transformers.js) to run all-MiniLM-L6-v2
 * (384-dim, ~90MB) locally in the Node.js process. No cloud calls — this
 * is the "local-first" vision from the build plan.
 *
 * The model loads lazily on first use (to avoid loading 90MB into memory
 * on server startup). Once loaded, embeddings compute in ~50ms on CPU.
 *
 * Graceful degradation: if the model can't load (no internet to download,
 * OOM, etc.), all embedding functions return null and the caller falls back
 * to keyword search. The brain still works, just without semantic recall.
 *
 * Storage: embeddings are Float32Arrays stored as Buffer (Bytes) in SQLite.
 * Cosine similarity is computed in JS — fast enough for <10k notes. For
 * larger brains, upgrade to sqlite-vec (a native SQLite extension).
 */
import { pipeline } from '@xenova/transformers'

let _embedderPromise: Promise<any> | null = null
let _available = true // set false if model fails to load

const MODEL_ID = 'Xenova/all-MiniLM-L6-v2'

/** Lazily load the embedding model. Returns null if unavailable. */
async function getEmbedder(): Promise<any | null> {
  if (!_available) return null
  if (!_embedderPromise) {
    _embedderPromise = pipeline('feature-extraction', MODEL_ID, {
      quantized: true, // use quantized model (~23MB vs ~90MB)
    }).catch(err => {
      console.error('[embeddings] model load failed:', err instanceof Error ? err.message : err)
      _available = false
      return null
    })
  }
  return _embedderPromise
}

/**
 * Compute a 384-dim embedding for a text string.
 * Returns a Buffer (Float32Array bytes) suitable for Prisma Bytes column,
 * or null if the model is unavailable.
 */
export async function embed(text: string): Promise<Buffer | null> {
  if (!text || !text.trim()) return null
  const embedder = await getEmbedder()
  if (!embedder) return null

  try {
    const output = await embedder(text, { pooling: 'mean', normalize: true })
    // output.data is a Float32Array of 384 dims, already L2-normalized
    const float32 = new Float32Array(output.data)
    return Buffer.from(float32.buffer, float32.byteOffset, float32.byteLength)
  } catch (err) {
    console.error('[embeddings] compute failed:', err instanceof Error ? err.message : err)
    return null
  }
}

/**
 * Compute cosine similarity between two embedding buffers.
 * Since embeddings are L2-normalized, cosine similarity = dot product.
 * Returns 0..1 (clamped, since normalized vectors can have negative cosine
 * but for text similarity we treat negative as "unrelated" = 0).
 */
export function cosineSimilarity(a: Buffer, b: Buffer): number {
  if (!a || !b || a.length !== b.length) return 0
  const av = new Float32Array(a.buffer, a.byteOffset, a.length / 4)
  const bv = new Float32Array(b.buffer, b.byteOffset, b.length / 4)
  let dot = 0
  for (let i = 0; i < av.length; i++) dot += av[i] * bv[i]
  return Math.max(0, dot) // clamp negative to 0
}

/**
 * Check if the embedding model is available (loaded or loadable).
 * Returns false if a previous load attempt failed.
 */
export function isEmbeddingAvailable(): boolean {
  return _available
}

/**
 * Find the top-k most similar notes to a query embedding.
 * Loads all notes with embeddings into memory and computes cosine similarity.
 * Returns [{ id, title, score }] sorted by score descending.
 *
 * For <10k notes this takes <5ms in JS. For larger brains, upgrade to
 * sqlite-vec (a native SQLite extension that does k-NN search in the DB).
 */
export function rankBySimilarity(
  queryEmbedding: Buffer,
  notes: { id: string; title: string; embedding: Buffer | null }[],
  k: number = 10,
  threshold: number = 0.3,
): { id: string; title: string; score: number }[] {
  return notes
    .filter(n => n.embedding && n.embedding.length > 0)
    .map(n => ({
      id: n.id,
      title: n.title,
      score: cosineSimilarity(queryEmbedding, n.embedding!),
    }))
    .filter(r => r.score >= threshold)
    .sort((a, b) => b.score - a.score)
    .slice(0, k)
}
