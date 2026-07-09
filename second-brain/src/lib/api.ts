/**
 * Shared API response types — discriminated unions so callers MUST handle
 * both success and error at compile time.
 *
 * ponytail: discriminated unions over thrown exceptions at API boundaries.
 * The type system forces the client to handle the error case; with exceptions
 * the client can forget the try/catch and get a runtime crash.
 */

export type ApiError = {
  ok: false
  error: {
    code: 'no_audio' | 'invalid_input' | 'ffmpeg_failed' | 'whisper_failed' | 'llm_failed' | 'timeout' | 'server_error'
    message: string
  }
}

export type ApiOk<T> = {
  ok: true
  data: T
}

export type ApiResult<T> = ApiOk<T> | ApiError

// Transcription response
export type Segment = {
  start: number
  end: number
  text: string
  no_speech_prob?: number
  avg_logprob?: number
}

export type TranscribeData = {
  transcript: string
  language: string
  segments: Segment[]
  time_ms?: number
}

// Polish response
export type PolishData = {
  polished: string
  warning?: string
}

// Shared mode type
export type Mode = 'raw' | 'clean' | 'chat' | 'formal' | 'command'

export const MODES: Mode[] = ['raw', 'clean', 'chat', 'formal', 'command']

/**
 * JSON helper: wraps a value in the ok/error envelope.
 * Usage: `return jsonOk(res, { transcript, ... })` or `return jsonErr('whisper_failed', 'msg', 500)`
 */
export function jsonOk<T>(data: T, status = 200): Response {
  return Response.json({ ok: true, data } satisfies ApiOk<T>, { status })
}

export function jsonErr(code: ApiError['error']['code'], message: string, status = 400): Response {
  return Response.json({ ok: false, error: { code, message } } satisfies ApiError, { status })
}
