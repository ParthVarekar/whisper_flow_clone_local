'use client'

/**
 * Capture overlay — shown in the Tauri capture-overlay window.
 *
 * This is a minimal, borderless page that appears when the user presses
 * Ctrl+Shift+B. It:
 *   1. Immediately starts recording audio via MediaRecorder
 *   2. Shows a "Listening..." indicator with a pulsing animation
 *   3. On stop (click or Esc): sends the audio to the Tauri backend via
 *      `invoke('capture_from_audio')`, which forwards it to the Python
 *      sidecar for transcription, then to /api/brain/capture
 *   4. Shows "✓ Captured: [title]" → auto-closes after 2s
 *
 * In the browser (no Tauri), it falls back to calling /api/transcribe +
 * /api/brain/capture directly (same as the main page's voice button).
 */

import { useEffect, useRef, useState, useCallback } from 'react'
import { Mic, Square, Loader2, CheckCircle2, X } from 'lucide-react'

type Status = 'recording' | 'transcribing' | 'capturing' | 'done' | 'error'

export default function CapturePage() {
  const [status, setStatus] = useState<Status>('recording')
  const [message, setMessage] = useState('Listening...')
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])

  const stopAndCapture = useCallback(async () => {
    if (status !== 'recording') return
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop()
    }
  }, [status])

  // Process the recorded audio: transcribe → classify → store
  const processCapture = useCallback(async (blob: Blob) => {
    setStatus('transcribing')
    setMessage('Transcribing...')

    try {
      // Check if we're in Tauri (has the invoke function)
      const isTauri = typeof window !== 'undefined' && (window as any).__TAURI__

      if (isTauri) {
        // Tauri path: send audio to Rust backend → Python sidecar → capture API
        const { invoke } = await import('@tauri-apps/api/core')
        const { writeFile, removeFile } = await import('@tauri-apps/plugin-fs')
        const { appDataDir } = await import('@tauri-apps/api/path')

        const audioPath = `${await appDataDir()}/capture-${Date.now()}.webm`
        const audioBytes = new Uint8Array(await blob.arrayBuffer())
        await writeFile(audioPath, audioBytes)

        const result = await invoke('capture_from_audio', { audioPath })
        setMessage(`✓ Captured: ${(result as any).title}`)
        await removeFile(audioPath).catch(() => {})
      } else {
        // Browser fallback: call the Next.js API directly
        const fd = new FormData()
        fd.append('audio', blob, 'voice.webm')
        const transcribeRes = await fetch('/api/transcribe', { method: 'POST', body: fd })
        const transcribeData = await transcribeRes.json()
        if (!transcribeRes.ok) throw new Error(transcribeData.error || 'transcription failed')
        const transcript = transcribeData.transcript || ''

        if (!transcript.trim()) {
          setStatus('error')
          setMessage('No speech detected')
          setTimeout(() => window.close(), 2000)
          return
        }

        setStatus('capturing')
        setMessage('Classifying...')
        const captureRes = await fetch('/api/brain/capture', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ transcript, source: 'voice' }),
        })
        const captureData = await captureRes.json()
        if (captureData.note) {
          setMessage(`✓ Captured: ${captureData.note.title.slice(0, 50)}`)
        }
      }

      setStatus('done')
      setTimeout(() => window.close(), 2000)
    } catch (err: any) {
      setStatus('error')
      setMessage(`Error: ${err?.message || err}`)
      setTimeout(() => window.close(), 4000)
    }
  }, [])

  // Start recording on mount
  useEffect(() => {
    let stream: MediaStream | null = null

    async function start() {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        const mr = new MediaRecorder(stream)
        mediaRecorderRef.current = mr
        chunksRef.current = []
        mr.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data) }
        mr.onstop = async () => {
          stream?.getTracks().forEach(t => t.stop())
          const blob = new Blob(chunksRef.current, { type: 'audio/webm' })
          await processCapture(blob)
        }
        mr.start()
      } catch (err: any) {
        setStatus('error')
        setMessage(`Mic error: ${err?.message || err}`)
        setTimeout(() => window.close(), 3000)
      }
    }

    start()

    // Esc to stop
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') stopAndCapture()
    }
    window.addEventListener('keydown', onKey)

    return () => {
      window.removeEventListener('keydown', onKey)
      stream?.getTracks().forEach(t => t.stop())
    }
  }, [stopAndCapture, processCapture])

  // Auto-stop after 60s (safety)
  useEffect(() => {
    const timer = setTimeout(() => stopAndCapture(), 60_000)
    return () => clearTimeout(timer)
  }, [stopAndCapture])

  const bgColor = {
    recording: 'bg-rose-500',
    transcribing: 'bg-purple-500',
    capturing: 'bg-purple-500',
    done: 'bg-emerald-500',
    error: 'bg-rose-600',
  }[status]

  return (
    <div className={`flex flex-col items-center justify-center h-screen ${bgColor} text-white p-4 select-none`}>
      <div className="flex items-center gap-3">
        {status === 'recording' && (
          <>
            <span className="relative flex h-3 w-3">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-white opacity-75"></span>
              <span className="relative inline-flex rounded-full h-3 w-3 bg-white"></span>
            </span>
            <Mic className="w-5 h-5" />
            <span className="text-sm font-medium">{message}</span>
            <button
              onClick={stopAndCapture}
              className="ml-2 p-1 rounded hover:bg-white/20 transition-colors"
              title="Stop (Esc)"
            >
              <Square className="w-4 h-4" />
            </button>
          </>
        )}
        {(status === 'transcribing' || status === 'capturing') && (
          <>
            <Loader2 className="w-5 h-5 animate-spin" />
            <span className="text-sm font-medium">{message}</span>
          </>
        )}
        {status === 'done' && (
          <>
            <CheckCircle2 className="w-5 h-5" />
            <span className="text-sm font-medium">{message}</span>
          </>
        )}
        {status === 'error' && (
          <>
            <X className="w-5 h-5" />
            <span className="text-sm font-medium">{message}</span>
          </>
        )}
      </div>
      {status === 'recording' && (
        <p className="text-xs text-white/70 mt-2">Press Esc or click ■ to stop</p>
      )}
    </div>
  )
}
