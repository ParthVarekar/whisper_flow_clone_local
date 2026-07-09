import { NextResponse } from 'next/server'
import { existsSync, statSync } from 'fs'
import { readFileSync } from 'fs'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

const BUILD_LOG = '/home/z/my-project/whisper-build/build.log'

function readBuildLogTail(): string[] {
  try {
    const content = readFileSync(BUILD_LOG, 'utf-8')
    const lines = content.split('\n').filter(Boolean)
    return lines.slice(-8)
  } catch {
    return []
  }
}

export async function GET() {
  // Check if faster-whisper Python package is available
  let fasterWhisperReady = false
  let whisperCliReady = false

  try {
    const { spawnSync } = await import('child_process')
    const check = spawnSync('python3', ['-c', 'import faster_whisper; print("ok")'], {
      encoding: 'utf-8',
      timeout: 5000,
    })
    fasterWhisperReady = check.status === 0 && check.stdout.includes('ok')
  } catch {
    // ignore
  }

  // Also check for C++ whisper-cli (legacy path)
  try {
    const { existsSync } = await import('fs')
    whisperCliReady = existsSync('/home/z/my-project/whisper-build/whisper.cpp/build/bin/whisper-cli')
  } catch {
    // ignore
  }

  const available = fasterWhisperReady || whisperCliReady
  const logTail = readBuildLogTail()

  return NextResponse.json({
    available,
    faster_whisper: fasterWhisperReady,
    whisper_cli: whisperCliReady,
    model_path: fasterWhisperReady ? 'base (auto-downloaded by faster-whisper)' : null,
    model_size_mb: fasterWhisperReady ? 145 : null,
    building: !available,
    build_log_tail: logTail,
    backend: fasterWhisperReady ? 'faster-whisper (Python/CTranslate2)' : whisperCliReady ? 'whisper-cli (C++)' : 'none',
  })
}
