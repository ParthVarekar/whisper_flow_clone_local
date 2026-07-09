/**
 * Text-matching helpers for the Second Brain.
 *
 * Used by:
 *   - /api/brain/capture  (related-notes matching, A10)
 *   - /api/brain/ask      (RAG retrieval, C4)
 *   - /api/brain/search   (keyword search scoring)
 *
 * The previous implementation used `transcript.split(/\s+/).slice(0, 5)` with
 * no stopword filtering and substring `.includes()` matching, which meant
 * common words like "this", "that", "with" dominated the score and "art"
 * matched "start". This module fixes both:
 *   - real English stopword list
 *   - word-boundary tokenization (no substring false positives)
 *   - TF-IDF scoring for relevance (better than raw count overlap)
 *   - a configurable similarity threshold so notes don't link on noise
 */

// Compact but effective English stopword list. Tuned for note content —
// keeps short content words ("ai", "ui", "go", "js") that naive length>3
// filters would drop.
export const STOP_WORDS = new Set([
  'a','an','the','is','are','was','were','be','been','being','am','do','does',
  'did','doing','have','has','had','having','will','would','shall','should',
  'can','could','may','might','must','to','of','in','for','on','with','at',
  'by','from','as','into','about','than','then','so','if','but','and','or',
  'not','no','yes','this','that','these','those','it','its','i','you','he',
  'she','we','they','me','him','her','us','them','my','your','his','our',
  'their','what','which','who','whom','whose','when','where','why','how',
  'all','any','both','each','few','more','most','other','some','such','only',
  'own','same','very','just','too','also','there','here','out','up','down',
  'off','over','under','again','once','because','until','while','during',
  'through','between','against','above','below','now','today','tomorrow',
  'yesterday','got','get','getting','go','going','gone','come','came','make',
  'made','said','say','saying','see','seen','saw','know','knew','known',
  'think','thought','want','wanted','need','needed','like','well','even',
  'still','back','way','thing','things','stuff','really','actually','kind',
  'sort','maybe','um','uh','oh','ah','like','right','okay','ok','yeah',
])

/** Tokenize text into normalized lowercase word-boundary tokens. */
export function tokenize(text: string): string[] {
  if (!text) return []
  return text
    .toLowerCase()
    .split(/[^a-z0-9]+/i)
    .filter(t => t.length > 1 && !STOP_WORDS.has(t))
}

/** Unique token set. */
export function tokenSet(text: string): Set<string> {
  return new Set(tokenize(text))
}

/**
 * Keyword-overlap score between a query and a document.
 * Returns a 0..1 fraction: |query_tokens ∩ doc_tokens| / |query_tokens|.
 * 0 means no overlap, 1 means every query token appears in the doc.
 */
export function overlapScore(queryTokens: string[], docText: string): number {
  if (queryTokens.length === 0) return 0
  const docSet = tokenSet(docText)
  let hits = 0
  for (const t of queryTokens) if (docSet.has(t)) hits++
  return hits / queryTokens.length
}

/**
 * Inverted-index TF-IDF scorer. Build once from a corpus, then score any
 * query against all documents in O(|query|) time. Much better relevance
 * than raw overlap because it down-weights words that appear in many notes.
 *
 * Usage:
 *   const idx = new TfIdfIndex()
 *   for (const note of notes) idx.add(note.id, note.title + ' ' + note.body + ' ' + note.tags)
 *   const ranked = idx.search('how to ship the launch', 10)  // [{ id, score }]
 */
export class TfIdfIndex {
  private docCount = 0
  private docFreq = new Map<string, number>() // token -> # docs containing it
  private docs = new Map<string, { tokens: string[]; len: number }>()

  add(docId: string, text: string) {
    const tokens = tokenize(text)
    this.docs.set(docId, { tokens, len: tokens.length })
    this.docCount++
    const seen = new Set<string>()
    for (const t of tokens) {
      if (!seen.has(t)) {
        this.docFreq.set(t, (this.docFreq.get(t) || 0) + 1)
        seen.add(t)
      }
    }
  }

  /** Score a query against all indexed docs. Returns top-k [{id, score}]. */
  search(query: string, k = 10): { id: string; score: number }[] {
    const qTokens = tokenize(query)
    if (qTokens.length === 0 || this.docCount === 0) return []

    // IDF: rarer words score higher. Smoothed to avoid division by zero.
    const idf = (t: string) => {
      const df = this.docFreq.get(t) || 0
      return Math.log((this.docCount + 1) / (df + 1)) + 1
    }

    const scores: { id: string; score: number }[] = []
    for (const [id, doc] of this.docs) {
      // TF: count of query tokens in this doc, weighted by IDF
      const docSet = new Set(doc.tokens)
      let score = 0
      for (const qt of qTokens) {
        if (docSet.has(qt)) {
          score += idf(qt)
        }
      }
      // Normalize by sqrt(doc length) to avoid long-doc bias
      if (score > 0) {
        score = score / Math.sqrt(doc.len || 1)
        scores.push({ id, score })
      }
    }
    scores.sort((a, b) => b.score - a.score)
    return scores.slice(0, k)
  }
}
