/**
 * Shared LLM helpers for the Second Brain.
 *
 * All z-ai-web-dev-sdk usage in the brain routes goes through here so that:
 *   1. The SDK is imported once (it's a dynamic import — caching it saves
 *      ~100ms of module-load per request).
 *   2. The system-prompt role is always 'system' (the previous code used
 *      'assistant', which weakened instruction adherence — see
 *      SECOND_BRAIN_UNDERSTANDING.md §6.1).
 *   3. Every LLM call has a consistent timeout + error envelope.
 *
 * IMPORTANT: z-ai-web-dev-sdk is a server-only SDK. Never import this file
 * from a client component.
 */
import type { } from 'z-ai-web-dev-sdk'

let _zaiPromise: Promise<any> | null = null

/**
 * Lazily create + cache the ZAI client. The SDK's create() reads credentials
 * from the env; we do this once per process.
 */
export async function getLLM() {
  if (!_zaiPromise) {
    const ZAI = (await import('z-ai-web-dev-sdk')).default
    _zaiPromise = ZAI.create()
  }
  return _zaiPromise
}

/**
 * Run a chat completion with the correct message roles.
 *
 * @param systemPrompt  instructions for the model (role: 'system')
 * @param userContent    the user's input (role: 'user')
 * @param opts           temperature, timeoutMs, thinking
 */
export async function chat(
  systemPrompt: string,
  userContent: string,
  opts: { temperature?: number; timeoutMs?: number } = {},
): Promise<string> {
  const zai = await getLLM()
  const timeoutMs = opts.timeoutMs ?? 20_000
  const temperature = opts.temperature ?? 0.0

  const completion = await Promise.race([
    zai.chat.completions.create({
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: userContent },
      ],
      thinking: { type: 'disabled' },
      temperature,
    }),
    new Promise<never>((_, reject) =>
      setTimeout(() => reject(new Error(`LLM timed out after ${timeoutMs}ms`)), timeoutMs),
    ),
  ]) as any

  return (completion.choices[0]?.message?.content || '').trim()
}

/**
 * Best-effort extraction of a JSON object from an LLM response that may be
 * wrapped in markdown code fences or surrounded by prose.
 */
export function extractJSON(raw: string): any | null {
  if (!raw) return null
  // Strip markdown code fences if present
  const fenced = raw.match(/```(?:json)?\s*([\s\S]*?)```/i)
  const candidate = fenced ? fenced[1] : raw
  const match = candidate.match(/\{[\s\S]*\}/)
  if (!match) return null
  try {
    return JSON.parse(match[0])
  } catch {
    return null
  }
}
