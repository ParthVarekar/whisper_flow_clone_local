'use client'

import { useState, useEffect, useCallback, useRef } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import {
  Brain, Search, Mic, Plus, Trash2, CheckCircle2, Circle,
  Lightbulb, CheckSquare, FileText, HelpCircle, PenLine, Link2, Clock, Loader2, Square,
} from 'lucide-react'

type Note = {
  id: string
  title: string
  body: string
  rawTranscript: string | null
  type: string
  tags: string
  source: string
  appContext: string | null
  confidence: number
  createdAt: string
  tasks: Task[]
}

type Task = {
  id: number
  text: string
  done: boolean
  due: string | null
  note: { id: string; title: string }
}

const TYPE_ICONS: Record<string, any> = {
  idea: Lightbulb,
  task: CheckSquare,
  reference: FileText,
  question: HelpCircle,
  journal: PenLine,
  dictation: PenLine,
}

const TYPE_COLORS: Record<string, string> = {
  idea: 'text-amber-500',
  task: 'text-emerald-500',
  reference: 'text-blue-500',
  question: 'text-purple-500',
  journal: 'text-slate-500',
  dictation: 'text-slate-400',
}

export default function Home() {
  const [notes, setNotes] = useState<Note[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<Note[] | null>(null)
  const [selectedNote, setSelectedNote] = useState<Note | null>(null)
  const [showCapture, setShowCapture] = useState(false)
  const [captureText, setCaptureText] = useState('')
  const [capturing, setCapturing] = useState(false)
  const [relatedNotes, setRelatedNotes] = useState<any[]>([])
  const [filter, setFilter] = useState<string>('all')
  const [loading, setLoading] = useState(true)
  // Voice capture state
  const [isRecording, setIsRecording] = useState(false)
  const [voiceStatus, setVoiceStatus] = useState<string>('')
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])

  const fetchNotes = useCallback(async () => {
    const res = await fetch('/api/brain/notes')
    const data = await res.json()
    setNotes(data.notes || [])
    setLoading(false)
  }, [])

  const fetchTasks = useCallback(async () => {
    const res = await fetch('/api/brain/tasks')
    const data = await res.json()
    setTasks(data.tasks || [])
  }, [])

  useEffect(() => {
    fetchNotes()
    fetchTasks()
  }, [fetchNotes, fetchTasks])

  const [askAnswer, setAskAnswer] = useState<string | null>(null)
  const [asking, setAsking] = useState(false)

  const handleSearch = async () => {
    if (!searchQuery.trim()) {
      setSearchResults(null)
      setAskAnswer(null)
      return
    }

    // Detect if this is a question → use "Ask your brain" RAG
    const isQuestion = /^(what|how|should|which|why|when|where|who|can|could|would|is|are|do|does|tell|give|show|find|list|remind)/i.test(searchQuery.trim())

    if (isQuestion) {
      setAsking(true)
      setAskAnswer(null)
      setSearchResults(null)
      try {
        const res = await fetch('/api/brain/ask', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question: searchQuery }),
        })
        const data = await res.json()
        setAskAnswer(data.answer || 'No answer found.')
        // Also show source notes if any
        if (data.sources && data.sources.length > 0) {
          const sourceIds = data.sources.map((s: any) => s.id)
          const allNotesRes = await fetch('/api/brain/notes')
          const allNotesData = await allNotesRes.json()
          setSearchResults(allNotesData.notes?.filter((n: Note) => sourceIds.includes(n.id)) || [])
        }
      } catch {
        setAskAnswer('Sorry, I could not process that question.')
      } finally {
        setAsking(false)
      }
    } else {
      // Regular keyword search
      setAskAnswer(null)
      const res = await fetch(`/api/brain/search?q=${encodeURIComponent(searchQuery)}`)
      const data = await res.json()
      setSearchResults(data.results || [])
    }
  }

  const handleCapture = async () => {
    if (!captureText.trim()) return
    setCapturing(true)
    try {
      const res = await fetch('/api/brain/capture', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ transcript: captureText, source: 'text' }),
      })
      const data = await res.json()
      if (data.note) {
        setNotes(prev => [data.note, ...prev])
        setRelatedNotes(data.relatedNotes || [])
        setCaptureText('')
        setShowCapture(false)
        fetchTasks()
      }
    } catch (err) {
      console.error('capture failed:', err)
    } finally {
      setCapturing(false)
    }
  }

  // ---- Voice capture: record → transcribe → classify → store ----
  const startVoiceCapture = async () => {
    setVoiceStatus('Listening...')
    setIsRecording(true)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream)
      mediaRecorderRef.current = mr
      chunksRef.current = []
      mr.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data) }
      mr.onstop = async () => {
        stream.getTracks().forEach(t => t.stop())
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' })
        await transcribeAndCapture(blob)
      }
      mr.start()
    } catch (err: any) {
      setVoiceStatus(`Mic error: ${err?.message || err}`)
      setIsRecording(false)
      setTimeout(() => setVoiceStatus(''), 3000)
    }
  }

  const stopVoiceCapture = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop()
    }
    setIsRecording(false)
  }

  const transcribeAndCapture = async (blob: Blob) => {
    setVoiceStatus('Transcribing...')
    try {
      // Step 1: transcribe via z-ai ASR
      const fd = new FormData()
      fd.append('audio', blob, 'voice.webm')
      const transcribeRes = await fetch('/api/transcribe', { method: 'POST', body: fd })
      const transcribeData = await transcribeRes.json()
      if (!transcribeRes.ok) throw new Error(transcribeData.error || 'transcription failed')
      const transcript = transcribeData.transcript || ''
      if (!transcript.trim()) {
        setVoiceStatus('No speech detected')
        setTimeout(() => setVoiceStatus(''), 3000)
        return
      }

      // Step 2: classify + store via brain capture
      setVoiceStatus('Classifying...')
      const captureRes = await fetch('/api/brain/capture', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ transcript, source: 'voice' }),
      })
      const captureData = await captureRes.json()
      if (captureData.note) {
        setNotes(prev => [captureData.note, ...prev])
        setRelatedNotes(captureData.relatedNotes || [])
        fetchTasks()
        setVoiceStatus(`✓ Captured: ${captureData.note.title.slice(0, 40)}`)
        setTimeout(() => setVoiceStatus(''), 3000)
      }
    } catch (err: any) {
      setVoiceStatus(`Error: ${err?.message || err}`)
      setTimeout(() => setVoiceStatus(''), 5000)
    }
  }

  const handleDelete = async (id: string) => {
    await fetch(`/api/brain/notes?id=${id}`, { method: 'DELETE' })
    setNotes(prev => prev.filter(n => n.id !== id))
    if (selectedNote?.id === id) setSelectedNote(null)
  }

  const toggleTask = async (id: number, done: boolean) => {
    await fetch('/api/brain/tasks', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id, done: !done }),
    })
    setTasks(prev => prev.map(t => t.id === id ? { ...t, done: !done } : t))
  }

  const displayedNotes = searchResults || notes
  const filteredNotes = filter === 'all' ? displayedNotes : displayedNotes.filter(n => n.type === filter)

  const formatTime = (iso: string) => {
    const d = new Date(iso)
    const now = new Date()
    const diff = now.getTime() - d.getTime()
    if (diff < 60000) return 'just now'
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  }

  const overdueTasks = tasks.filter(t => !t.done && t.due && new Date(t.due) < new Date())
  const todayTasks = tasks.filter(t => !t.done && t.due && new Date(t.due).toDateString() === new Date().toDateString())
  const unscheduledTasks = tasks.filter(t => !t.done && !t.due)

  return (
    <main className="min-h-screen bg-slate-50 dark:bg-slate-950">
      <div className="flex h-screen">
        {/* Sidebar — Tasks */}
        <aside className="w-72 border-r border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 flex flex-col">
          <div className="p-4 border-b border-slate-200 dark:border-slate-800">
            <div className="flex items-center gap-2 mb-1">
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-purple-500 to-indigo-500 flex items-center justify-center">
                <Brain className="w-4 h-4 text-white" />
              </div>
              <h1 className="font-bold text-lg">Second Brain</h1>
            </div>
            <p className="text-xs text-slate-500">Your spare brain remembers</p>
          </div>

          <div className="p-3 border-b border-slate-200 dark:border-slate-800 space-y-2">
            <div className="flex gap-2">
              <Button
                onClick={() => setShowCapture(!showCapture)}
                className="flex-1"
                size="sm"
                variant="outline"
              >
                <Plus className="w-4 h-4 mr-1" />
                Type
              </Button>
              <Button
                onClick={() => (isRecording ? stopVoiceCapture() : startVoiceCapture())}
                className={`flex-1 ${isRecording ? 'bg-rose-500 hover:bg-rose-600' : ''}`}
                size="sm"
              >
                {isRecording ? <Square className="w-4 h-4 mr-1" /> : <Mic className="w-4 h-4 mr-1" />}
                {isRecording ? 'Stop' : 'Speak'}
              </Button>
            </div>
            {voiceStatus && (
              <div className={`text-xs px-2 py-1.5 rounded flex items-center gap-1.5 ${
                voiceStatus.startsWith('✓') ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300' :
                voiceStatus.startsWith('Error') || voiceStatus.startsWith('No ') ? 'bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-300' :
                'bg-purple-50 text-purple-700 dark:bg-purple-950 dark:text-purple-300'
              }`}>
                {voiceStatus.startsWith('Listening') && (
                  <span className="relative flex h-2 w-2">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-rose-400 opacity-75"></span>
                    <span className="relative inline-flex rounded-full h-2 w-2 bg-rose-500"></span>
                  </span>
                )}
                {(voiceStatus.startsWith('Transcribing') || voiceStatus.startsWith('Classifying')) && (
                  <Loader2 className="w-3 h-3 animate-spin" />
                )}
                {voiceStatus}
              </div>
            )}
          </div>

          <div className="flex-1 overflow-y-auto p-3 space-y-3">
            {overdueTasks.length > 0 && (
              <div>
                <h3 className="text-xs font-semibold text-rose-500 uppercase mb-1.5">Overdue</h3>
                {overdueTasks.map(t => <TaskItem key={t.id} task={t} onToggle={toggleTask} formatTime={formatTime} />)}
              </div>
            )}
            {todayTasks.length > 0 && (
              <div>
                <h3 className="text-xs font-semibold text-amber-500 uppercase mb-1.5">Today</h3>
                {todayTasks.map(t => <TaskItem key={t.id} task={t} onToggle={toggleTask} formatTime={formatTime} />)}
              </div>
            )}
            {unscheduledTasks.length > 0 && (
              <div>
                <h3 className="text-xs font-semibold text-slate-400 uppercase mb-1.5">Unscheduled</h3>
                {unscheduledTasks.map(t => <TaskItem key={t.id} task={t} onToggle={toggleTask} formatTime={formatTime} />)}
              </div>
            )}
            {tasks.length === 0 && (
              <p className="text-xs text-slate-400 text-center py-4">No tasks yet</p>
            )}
          </div>
        </aside>

        {/* Main content */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Search bar */}
          <div className="p-4 border-b border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900">
            <div className="flex gap-2">
              <div className="relative flex-1">
                <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <Input
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                  placeholder="Ask your brain..."
                  className="pl-9"
                />
              </div>
              <Button onClick={handleSearch} size="sm" variant="outline">
                <Search className="w-4 h-4" />
              </Button>
            </div>

            {/* Filter tabs */}
            <div className="flex gap-1 mt-3">
              {['all', 'idea', 'task', 'reference', 'question', 'journal'].map(f => (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  className={`px-3 py-1 text-xs rounded-full transition-colors ${
                    filter === f
                      ? 'bg-slate-800 text-white dark:bg-slate-200 dark:text-slate-900'
                      : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400 hover:bg-slate-200'
                  }`}
                >
                  {f}
                </button>
              ))}
            </div>
          </div>

          {/* Ask your brain answer */}
          {(asking || askAnswer) && (
            <div className="p-4 border-b border-slate-200 dark:border-slate-800 bg-indigo-50 dark:bg-indigo-950/20">
              {asking ? (
                <div className="flex items-center gap-2 text-sm text-indigo-600 dark:text-indigo-300">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Asking your brain...
                </div>
              ) : (
                <div>
                  <div className="flex items-start gap-2">
                    <Brain className="w-4 h-4 mt-0.5 text-indigo-500 flex-shrink-0" />
                    <p className="text-sm text-slate-700 dark:text-slate-200 whitespace-pre-wrap leading-relaxed">{askAnswer}</p>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Capture form */}
          {showCapture && (
            <div className="p-4 border-b border-slate-200 dark:border-slate-800 bg-purple-50 dark:bg-purple-950/20">
              <Textarea
                value={captureText}
                onChange={(e) => setCaptureText(e.target.value)}
                placeholder="Type a thought, idea, or task. The AI will classify and tag it automatically..."
                className="mb-2"
                autoFocus
              />
              <div className="flex gap-2">
                <Button onClick={handleCapture} disabled={capturing || !captureText.trim()} size="sm">
                  {capturing ? <><Mic className="w-3 h-3 mr-1 animate-pulse" /> Capturing...</> : 'Capture'}
                </Button>
                <Button onClick={() => { setShowCapture(false); setCaptureText('') }} size="sm" variant="ghost">
                  Cancel
                </Button>
              </div>
            </div>
          )}

          {/* Related notes notification */}
          {relatedNotes.length > 0 && (
            <div className="p-3 bg-blue-50 dark:bg-blue-950/20 border-b border-blue-200 dark:border-blue-900">
              <div className="flex items-center gap-2 text-sm text-blue-700 dark:text-blue-300">
                <Link2 className="w-4 h-4 flex-shrink-0" />
                <span>Linked to {relatedNotes.length} related note(s):</span>
                {relatedNotes.map((r, i) => (
                  <span key={r.id} className="text-xs underline cursor-pointer" onClick={() => {
                    const n = notes.find(n => n.id === r.id)
                    if (n) setSelectedNote(n)
                  }}>
                    {r.title}{i < relatedNotes.length - 1 ? ', ' : ''}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Timeline / Search results */}
          <div className="flex-1 overflow-y-auto p-4">
            {loading ? (
              <p className="text-center text-slate-400 py-8">Loading your brain...</p>
            ) : filteredNotes.length === 0 ? (
              <div className="text-center py-12">
                <Brain className="w-12 h-12 mx-auto mb-3 text-slate-300" />
                <p className="text-slate-500 text-sm">
                  {searchResults ? 'No results found.' : 'Your brain is empty.'}
                </p>
                {!searchResults && (
                  <p className="text-slate-400 text-xs mt-1">
                    Click "Speak" to capture a thought by voice, or "Type" to write one.
                  </p>
                )}
              </div>
            ) : (
              <div className="space-y-3 max-w-3xl mx-auto">
                {filteredNotes.map(note => {
                  const Icon = TYPE_ICONS[note.type] || PenLine
                  const color = TYPE_COLORS[note.type] || 'text-slate-500'
                  return (
                    <Card
                      key={note.id}
                      className={`cursor-pointer hover:shadow-md transition-shadow ${selectedNote?.id === note.id ? 'ring-2 ring-purple-400' : ''}`}
                      onClick={() => setSelectedNote(note)}
                    >
                      <CardContent className="py-3">
                        <div className="flex items-start gap-3">
                          <Icon className={`w-5 h-5 mt-0.5 flex-shrink-0 ${color}`} />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center justify-between gap-2">
                              <h3 className="font-medium text-sm truncate">{note.title}</h3>
                              <span className="text-xs text-slate-400 flex-shrink-0">{formatTime(note.createdAt)}</span>
                            </div>
                            <p className="text-sm text-slate-600 dark:text-slate-400 mt-1 line-clamp-2">{note.body}</p>
                            <div className="flex items-center gap-2 mt-2 flex-wrap">
                              {note.tags.split(',').filter(Boolean).map((tag, i) => (
                                <Badge key={i} variant="secondary" className="text-[10px]">
                                  #{tag.trim()}
                                </Badge>
                              ))}
                              {note.tasks.length > 0 && (
                                <Badge variant="outline" className="text-[10px]">
                                  <CheckSquare className="w-2.5 h-2.5 mr-0.5" />
                                  {note.tasks.filter(t => !t.done).length} tasks
                                </Badge>
                              )}
                            </div>
                          </div>
                          <button
                            onClick={(e) => { e.stopPropagation(); handleDelete(note.id) }}
                            className="text-slate-300 hover:text-rose-500 transition-colors"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                      </CardContent>
                    </Card>
                  )
                })}
              </div>
            )}
          </div>
        </div>

        {/* Note detail panel */}
        {selectedNote && (
          <aside className="w-96 border-l border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 flex flex-col">
            <div className="p-4 border-b border-slate-200 dark:border-slate-800 flex items-center justify-between">
              <h2 className="font-semibold text-sm truncate">{selectedNote.title}</h2>
              <Button size="sm" variant="ghost" onClick={() => setSelectedNote(null)} className="h-7 px-2">
                ✕
              </Button>
            </div>
            <div className="flex-1 overflow-y-auto p-4">
              <div className="flex items-center gap-2 mb-3 flex-wrap">
                <Badge variant="outline" className="text-[10px]">{selectedNote.type}</Badge>
                {selectedNote.tags.split(',').filter(Boolean).map((tag, i) => (
                  <Badge key={i} variant="secondary" className="text-[10px]">#{tag.trim()}</Badge>
                ))}
              </div>
              <div className="flex items-center gap-2 text-xs text-slate-400 mb-3">
                <Clock className="w-3 h-3" />
                {new Date(selectedNote.createdAt).toLocaleString()}
                {selectedNote.confidence > 0 && (
                  <span>· {Math.round(selectedNote.confidence * 100)}% confidence</span>
                )}
              </div>
              <div className="prose prose-sm dark:prose-invert max-w-none">
                <p className="whitespace-pre-wrap text-sm leading-relaxed">{selectedNote.body}</p>
              </div>

              {selectedNote.tasks.length > 0 && (
                <div className="mt-4">
                  <h4 className="text-xs font-semibold text-slate-400 uppercase mb-2">Tasks</h4>
                  {selectedNote.tasks.map(t => (
                    <div key={t.id} className="flex items-center gap-2 py-1">
                      <button onClick={() => toggleTask(t.id, t.done)}>
                        {t.done ? <CheckCircle2 className="w-4 h-4 text-emerald-500" /> : <Circle className="w-4 h-4 text-slate-400" />}
                      </button>
                      <span className={`text-sm ${t.done ? 'line-through text-slate-400' : ''}`}>{t.text}</span>
                      {t.due && <span className="text-xs text-slate-400 ml-auto">{new Date(t.due).toLocaleDateString()}</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </aside>
        )}
      </div>
    </main>
  )
}

function TaskItem({ task, onToggle, formatTime }: { task: Task; onToggle: (id: number, done: boolean) => void; formatTime: (s: string) => string }) {
  return (
    <div className="flex items-center gap-2 py-1.5 px-2 rounded hover:bg-slate-100 dark:hover:bg-slate-800 cursor-pointer"
         onClick={() => onToggle(task.id, task.done)}>
      {task.done ? <CheckCircle2 className="w-4 h-4 text-emerald-500 flex-shrink-0" /> : <Circle className="w-4 h-4 text-slate-400 flex-shrink-0" />}
      <div className="flex-1 min-w-0">
        <p className={`text-xs ${task.done ? 'line-through text-slate-400' : ''}`}>{task.text}</p>
        {task.due && <p className="text-[10px] text-slate-400">{new Date(task.due).toLocaleDateString()}</p>}
      </div>
    </div>
  )
}
