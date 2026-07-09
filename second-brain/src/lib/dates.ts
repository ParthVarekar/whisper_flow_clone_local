/**
 * Relative-date parsing + validation for task due dates.
 *
 * The LLM classification prompt asks the model to resolve phrases like "by
 * Friday" to an ISO date. Two problems with trusting the LLM blindly:
 *   1. The LLM doesn't know today's date unless we inject it (we now do).
 *   2. Even with today injected, the LLM occasionally returns a date in the
 *      past, or 10 years out, or in the wrong format.
 *
 * This module validates + sanitizes the LLM's output:
 *   - parse: accept ISO string or null, return a Date or null
 *   - sanitize: reject dates in the past or >1 year out, return null instead
 */

const ONE_YEAR_MS = 365 * 24 * 60 * 60 * 1000

/**
 * Parse an LLM-produced due-date value into a Date or null.
 * Accepts: ISO strings, null, empty strings, "YYYY-MM-DD".
 */
export function parseDueDate(raw: unknown): Date | null {
  if (raw == null || raw === '') return null
  if (raw instanceof Date) return isNaN(raw.getTime()) ? null : raw
  if (typeof raw !== 'string') return null
  const s = raw.trim()
  if (!s || s.toLowerCase() === 'null') return null
  const d = new Date(s)
  return isNaN(d.getTime()) ? null : d
}

/**
 * Sanitize a parsed due date:
 *   - null → null (no due date)
 *   - in the past → clamp to today (don't lose the task, but don't show as overdue wrongly)
 *   - >1 year in the future → null (likely an LLM error)
 *   - otherwise → the date, normalized to midnight local time
 */
export function sanitizeDueDate(d: Date | null, now: Date = new Date()): Date | null {
  if (!d) return null
  const t = d.getTime()
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  if (t < todayStart) {
    // Past date — clamp to today rather than dropping, so the task is still visible.
    return new Date(todayStart)
  }
  if (t > now.getTime() + ONE_YEAR_MS) {
    // Too far out — likely an LLM arithmetic error. Drop the due date.
    return null
  }
  // Normalize to midnight local for clean grouping by day.
  return new Date(d.getFullYear(), d.getMonth(), d.getDate())
}

/**
 * Format a Date as YYYY-MM-DD for LLM prompt injection.
 * Used to tell the LLM "Today is 2026-07-09 (Wednesday)" so it can resolve
 * relative dates like "Friday" correctly.
 */
export function todayContext(now: Date = new Date()): string {
  const y = now.getFullYear()
  const m = String(now.getMonth() + 1).padStart(2, '0')
  const d = String(now.getDate()).padStart(2, '0')
  const weekday = now.toLocaleDateString('en-US', { weekday: 'long' })
  return `Today is ${y}-${m}-${d} (${weekday}).`
}
