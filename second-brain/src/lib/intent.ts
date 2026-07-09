/**
 * Intent routing for the search bar (A9).
 *
 * The previous implementation used a first-word regex:
 *   /^(what|how|should|...)/i
 * which mis-routed "find my notes about X" to RAG (it's a search) and
 * "remind me to call mom" to RAG (it's a task query).
 *
 * This module uses a smarter heuristic:
 *   - Ends with "?" → ASK (RAG)
 *   - Starts with a question word AND has a question structure → ASK
 *   - Contains "find/show/list/my notes about" → SEARCH
 *   - Otherwise → SEARCH (default; keyword search is cheap, RAG is not)
 *
 * Defaulting to SEARCH is the safe choice: keyword search is fast and
 * non-blocking; RAG makes a slow LLM call and should only fire when the user
 * clearly wants a synthesized answer.
 */

export type SearchIntent = 'ask' | 'search'

const QUESTION_STARTERS = new Set([
  'what', 'how', 'why', 'when', 'where', 'who', 'which',
  'is', 'are', 'am', 'do', 'does', 'did', 'can', 'could',
  'would', 'should', 'will', 'shall', 'may', 'might', 'must',
  'whats', 'hows', 'whys', 'whens', 'wheres', 'whos', 'whats',
])

// Phrases that strongly indicate a search, not a question — even if they
// start with a question word.
const SEARCH_PHRASES = [
  /\bfind\b/i, /\bshow\b/i, /\blist\b/i, /\bsearch\b/i,
  /\bmy notes (about|on|for)\b/i, /\bnotes (about|on|containing)\b/i,
  /\btagged\b/i, /\btagged with\b/i,
]

// A helping verb after a question word suggests a real question:
// "what should I do" (question) vs "what notes" (search-ish).
const HELPING_VERBS = new Set([
  'is', 'are', 'was', 'were', 'do', 'does', 'did', 'can', 'could',
  'would', 'should', 'will', 'shall', 'may', 'might', 'must',
  'i', 'you', 'he', 'she', 'it', 'we', 'they',  // "what I" "how you"
])

export function detectIntent(query: string): SearchIntent {
  const q = query.trim()
  if (!q) return 'search'

  // Explicit question mark → always ask
  if (q.endsWith('?')) return 'ask'

  const firstWord = q.toLowerCase().split(/\s+/)[0]?.replace(/[^a-z]/g, '')

  // Search phrases override question-word detection
  for (const re of SEARCH_PHRASES) {
    if (re.test(q)) return 'search'
  }

  // Question word + (helping verb or pronoun) → likely a real question
  if (firstWord && QUESTION_STARTERS.has(firstWord)) {
    const secondWord = q.toLowerCase().split(/\s+/)[1]?.replace(/[^a-z]/g, '')
    if (secondWord && HELPING_VERBS.has(secondWord)) return 'ask'
    // "what about X" / "how about X" → question
    if (secondWord === 'about') return 'ask'
    // Single question word alone ("why?", "how?") → ask
    if (!secondWord) return 'ask'
  }

  // Default: keyword search (cheap, non-blocking)
  return 'search'
}
