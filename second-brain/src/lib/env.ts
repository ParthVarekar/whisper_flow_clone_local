/**
 * Centralized, zod-validated environment configuration.
 *
 * All env vars are read here and nowhere else. The zod parse runs at import
 * time, so a missing required var crashes the process at boot (not at the
 * first request that needs it).
 *
 * ponytail: we use zod instead of ad-hoc `process.env.FOO || 'default'`
 * scattered across routes — one source of truth, one validation pass.
 * Upgrade path: add more vars here as the app grows.
 */
import { z } from 'zod'

const EnvSchema = z.object({
  NODE_ENV: z.enum(['development', 'test', 'production']).default('development'),
  // Persistent Whisper Python server (mini-services/whisper-server or unified-server)
  WHISPER_SERVER_URL: z.string().url().default('http://127.0.0.1:5001'),
  // Path to the one-shot Python transcription script (fallback when server is down)
  TRANSCRIBE_SCRIPT: z.string().default('/home/z/my-project/whisper-build/transcribe_oneshot.py'),
  // LLM timeout in milliseconds
  LLM_TIMEOUT_MS: z.coerce.number().default(20_000),
  // Max audio upload size in bytes (10 MB)
  MAX_AUDIO_BYTES: z.coerce.number().default(10 * 1024 * 1024),
})

export type Env = z.infer<typeof EnvSchema>

function loadEnv(): Env {
  const result = EnvSchema.safeParse(process.env)
  if (!result.success) {
    const issues = result.error.issues
      .map((i) => `  ${i.path.join('.')}: ${i.message}`)
      .join('\n')
    throw new Error(`Invalid environment configuration:\n${issues}`)
  }
  return result.data
}

export const env = loadEnv()
