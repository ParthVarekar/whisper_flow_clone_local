import { NextResponse } from 'next/server'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'
export const maxDuration = 30

/**
 * Transcription route — uses z-ai cloud ASR (no local Python/ffmpeg needed).
 *
 * Accepts: WAV, WebM (the z-ai ASR service supports these two formats).
 * The browser's MediaRecorder produces WebM natively; file uploads are
 * converted to WAV client-side before upload (see page.tsx).
 */
export async function POST(request: Request) {
  let formData: FormData
  try {
    formData = await request.formData()
  } catch {
    return NextResponse.json({ error: "no 'audio' file in request" }, { status: 400 })
  }

  const audioFile = formData.get('audio') as File | null
  if (!audioFile) {
    return NextResponse.json({ error: "no 'audio' file in request" }, { status: 400 })
  }

  try {
    // Convert audio to base64 for the z-ai ASR API
    const bytes = new Uint8Array(await audioFile.arrayBuffer())
    const base64Audio = Buffer.from(bytes).toString('base64')

    // Call z-ai cloud ASR — no local dependencies needed
    const ZAI = (await import('z-ai-web-dev-sdk')).default
    const zai = await ZAI.create()

    const response = await Promise.race([
      zai.audio.asr.create({ file_base64: base64Audio }),
      new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error('ASR timeout (20s)')), 20000)
      ),
    ]) as any

    const transcript = (response.text || '').trim()

    return NextResponse.json({
      transcript,
      language: 'en',
      segments: [],
    })
  } catch (err: any) {
    const message = err?.message || String(err)
    return NextResponse.json(
      { error: `transcription failed: ${message}` },
      { status: 500 }
    )
  }
}
