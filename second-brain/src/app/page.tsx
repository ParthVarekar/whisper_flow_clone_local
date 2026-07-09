'use client'

import { useState, useEffect, useCallback, useRef } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import {
  Brain, Search, Mic, Plus, Trash2, CheckCircle2, Circle,
  Lightbulb, CheckSquare, FileText, HelpCircle, PenLine, Link2, Clock,
  Loader2, Square, Download, RefreshCw, Edit3, Check, X, Inbox as InboxIcon,
  Archive, Sparkles, ChevronRight,
} from 'lucide-react'
import { detectIntent } from '@/lib/intent'

// --- Types ----------------------------------------------------------------

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
  status: string
  reviewedAt: string | null
  createdAt: string
  updatedAt: string
  tasks: Task[]
  links?: { id: number; targetId: string | null; target: { id: string; title: string } | null; similarity: number; type: string }[]
  backlinks?: { id: number; sourceId: string; source: { id: string; title: string }; similarity: number; type: string }[]
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
  uncategorized: PenLine,
}

const TYPE_COLORS: Record<string, string> = {
  idea: 'text-amber-500',
  task: 'text-emerald-500',
  reference: 'text-sky-500',
  question: 'text-purple-500',
  journal: 'text-slate-500',
  dictation: 'text-slate-400',
  uncategorized: 'text-slate-400',
}

const TYPE_LIST = ['idea', 'task', 'reference', 'question', 'journal', 'dictation']

// --- Main component -------------------------------------------------------

export default function Home() {
  // Data
  const [notes, setNotes] = useState<Note[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [inboxCount, setInboxCount] = useState(0)
  const [loading, setLoading] = useState(true)

  // UI state
  const [view, setView] = useState<'timeline' | 'inbox'>('timeline')
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<Note[] | null>(null)
  const [selectedNote, setSelectedNote] = useState<Note | null>(null)
  const [editing, setEditing] = useState(false)
  const [editDraft, setEditDraft] = useState<{ title: string; body: string; type: string; tags: string }>({ title: '', body: '', type: '', tags: '' })
  const [saving, setSaving] = useState(false)
  const [reclassifying, setReclassifying] = useState(false)

  // Capture
  const [showCapture, setShowCapture] = useState(false)
  const [captureText, setCaptureText] = useState('')
  const [capturing, setCapturing] = useState(false)
  const [relatedNotes, setRelatedNotes] = useState<any[]>([])

  // Voice
  const [isRecording, setIsRecording] = useState(false)
  const [voiceStatus, setVoiceStatus] = useState<string>('')
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])

  // Ask-your-brain
  const [askAnswer, setAskAnswer] = useState<string | null>(null)
  const [asking, setAsking] = useState(false)

  // Filters
  const [filter, setFilter] = useState<string>('all')

  // --- Data fetchers ---
  const fetchNotes = useCallback(async () => {
    try {
      const res = await fetch('/api/brain/notes')
      const data = await res.json()
      setNotes(data.notes || [])
    } catch (e) {
      console.error('fetch notes failed', e)
    } finally {
      setLoading(false)
    }
  }, [])

  const fetchTasks = useCallback(async () => {
    try {
      const res = await fetch('/api/brain/tasks')
      const data = await res.json()
      setTasks(data.tasks || [])
    } catch (e) {
      console.error('fetch tasks failed', e)
    }
  }, [])

  const fetchInboxCount = useCallback(async () => {
    try {
      const res = await fetch('/api/brain/inbox?limit=1')
      const data = await res.json()
      const total = Object.values(data.counts || {}).reduce((a: number, b: any) => a + Number(b), 0)
      setInboxCount(total)
    } catch (e) {
      // non-critical
    }
  }, [])

  useEffect(() => {
    fetchNotes()
    fetchTasks()
    fetchInboxCount()
  }, [fetchNotes, fetchTasks, fetchInboxCount])

  // --- Search / Ask ---
  const handleSearch = async () => {
    const q = searchQuery.trim()
    if (!q) {
      setSearchResults(null)
      setAskAnswer(null)
      return
    }

    const intent = detectIntent(q)

    if (intent === 'ask') {
      setAsking(true)
      setAskAnswer(null)
      setSearchResults(null)
      try {
        const res = await fetch('/api/brain/ask', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question: q }),
        })
        const data = await res.json()
        setAskAnswer(data.answer || 'No answer found.')
        if (data.sources && data.sources.length > 0) {
          const sourceIds = data.sources.map((s: any) => s.id)
          setSearchResults(notes.filter(n => sourceIds.includes(n.id)))
        }
      } catch {
        setAskAnswer('Sorry, I could not process that question.')
      } finally {
        setAsking(false)
      }
    } else {
      setAskAnswer(null)
      try {
        const res = await fetch(`/api/brain/search?q=${encodeURIComponent(q)}`)
        const data = await res.json()
        setSearchResults(data.results || [])
      } catch {
        setSearchResults([])
      }
    }
  }

  // --- Capture (text + voice) ---
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
        fetchInboxCount()
      }
    } catch (err) {
      console.error('capture failed:', err)
    } finally {
      setCapturing(false)
    }
  }

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
        fetchInboxCount()
        setVoiceStatus(`✓ Captured: ${captureData.note.title.slice(0, 40)}`)
        setTimeout(() => setVoiceStatus(''), 3000)
      }
    } catch (err: any) {
      setVoiceStatus(`Error: ${err?.message || err}`)
      setTimeout(() => setVoiceStatus(''), 5000)
    }
  }

  // --- Note editing (B1) ---
  const startEdit = (note: Note) => {
    setEditing(true)
    setEditDraft({ title: note.title, body: note.body, type: note.type, tags: note.tags })
  }

  const cancelEdit = () => {
    setEditing(false)
  }

  const saveEdit = async () => {
    if (!selectedNote) return
    setSaving(true)
    try {
      const res = await fetch(`/api/brain/notes/${selectedNote.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(editDraft),
      })
      const data = await res.json()
      if (data.note) {
        const updated = { ...data.note, tasks: selectedNote.tasks } as Note
        setSelectedNote(updated)
        setNotes(prev => prev.map(n => n.id === updated.id ? updated : n))
        setEditing(false)
        fetchTasks()
      }
    } catch (e) {
      console.error('save edit failed', e)
    } finally {
      setSaving(false)
    }
  }

  const reclassify = async () => {
    if (!selectedNote) return
    setReclassifying(true)
    try {
      const res = await fetch(`/api/brain/notes/${selectedNote.id}`, {
        method: 'POST', // POST on [id] = reclassify
      })
      const data = await res.json()
      if (data.note) {
        setSelectedNote(data.note)
        setNotes(prev => prev.map(n => n.id === data.note.id ? data.note : n))
        fetchTasks()
      } else if (data.error) {
        alert(data.error)
      }
    } catch (e) {
      console.error('reclassify failed', e)
    } finally {
      setReclassifying(false)
    }
  }

  const markProcessed = async (note: Note) => {
    try {
      const res = await fetch(`/api/brain/notes/${note.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: 'processed' }),
      })
      const data = await res.json()
      if (data.note) {
        setSelectedNote(data.note)
        setNotes(prev => prev.map(n => n.id === data.note.id ? data.note : n))
        fetchInboxCount()
      }
    } catch (e) {
      console.error('mark processed failed', e)
    }
  }

  const archiveNote = async (note: Note) => {
    try {
      const res = await fetch(`/api/brain/notes/${note.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: 'archived' }),
      })
      const data = await res.json()
      if (data.note) {
        setNotes(prev => prev.filter(n => n.id !== note.id))
        setSelectedNote(null)
        fetchInboxCount()
      }
    } catch (e) {
      console.error('archive failed', e)
    }
  }

  // --- Task management ---
  const toggleTask = async (id: number, done: boolean) => {
    setTasks(prev => prev.map(t => t.id === id ? { ...t, done: !done } : t))
    try {
      await fetch('/api/brain/tasks', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, done: !done }),
      })
    } catch (e) {
      // revert on failure
      setTasks(prev => prev.map(t => t.id === id ? { ...t, done } : t))
    }
  }

  const addTask = async (noteId: string, text: string) => {
    if (!text.trim()) return
    try {
      const res = await fetch('/api/brain/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ noteId, text }),
      })
      const data = await res.json()
      if (data.task) {
        setTasks(prev => [...prev, data.task])
        // Also update the selected note's tasks
        if (selectedNote && selectedNote.id === noteId) {
          setSelectedNote({ ...selectedNote, tasks: [...selectedNote.tasks, data.task] })
        }
      }
    } catch (e) {
      console.error('add task failed', e)
    }
  }

  const deleteTask = async (id: number) => {
    setTasks(prev => prev.filter(t => t.id !== id))
    if (selectedNote) {
      setSelectedNote({ ...selectedNote, tasks: selectedNote.tasks.filter(t => t.id !== id) })
    }
    try {
      await fetch(`/api/brain/tasks?id=${id}`, { method: 'DELETE' })
    } catch (e) {
      console.error('delete task failed', e)
    }
  }

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this note permanently?')) return
    await fetch(`/api/brain/notes?id=${id}`, { method: 'DELETE' })
    setNotes(prev => prev.filter(n => n.id !== id))
    if (selectedNote?.id === id) setSelectedNote(null)
    fetchTasks()
    fetchInboxCount()
  }

  // --- Derived ---
  const displayedNotes = searchResults || notes
  const filteredNotes = filter === 'all' ? displayedNotes : displayedNotes.filter(n => n.type === filter)
  const groupedNotes = groupByDay(filteredNotes)

  const overdueTasks = tasks.filter(t => !t.done && t.due && new Date(t.due) < new Date(new Date().toDateString()))
  const todayTasks = tasks.filter(t => !t.done && t.due && new Date(t.due).toDateString() === new Date().toDateString())
  const unscheduledTasks = tasks.filter(t => !t.done && !t.due)

  const formatTime = (iso: string) => {
    const d = new Date(iso)
    const now = new Date()
    const diff = now.getTime() - d.getTime()
    if (diff < 60000) return 'just now'
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  }

  return (
    <main className="min-h-screen bg-slate-50 dark:bg-slate-950 text-slate-900 dark:text-slate-100">
      <div className="flex h-screen">
        {/* ===== Sidebar — Tasks + Inbox ===== */}
        <aside className="w-72 border-r border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 flex flex-col">
          <div className="p-4 border-b border-slate-200 dark:border-slate-800">
            <div className="flex items-center gap-2 mb-1">
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-purple-500 to-pink-500 flex items-center justify-center">
                <Brain className="w-4 h-4 text-white" />
              </div>
              <h1 className="font-bold text-lg">Second Brain</h1>
            </div>
            <p className="text-xs text-slate-500">your spare brain remembers</p>
          </div>

          {/* Capture buttons */}
          <div className="p-3 border-b border-slate-200 dark:border-slate-800 space-y-2">
            <div className="flex gap-2">
              <Button
                onClick={() => { setShowCapture(!showCapture); setView('timeline') }}
                className="flex-1"
                size="sm"
                variant="outline"
              >
                <Plus className="w-4 h-4 mr-1" />
                Type
              </Button>
              <Button
                onClick={() => isRecording ? stopVoiceCapture() : startVoiceCapture()}
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

          {/* View switcher: Timeline / Inbox */}
          <div className="p-3 border-b border-slate-200 dark:border-slate-800">
            <div className="flex gap-1">
              <button
                onClick={() => { setView('timeline'); setSearchResults(null); setAskAnswer(null) }}
                className={`flex-1 px-3 py-1.5 text-xs rounded-md flex items-center justify-center gap-1.5 transition-colors ${
                  view === 'timeline' ? 'bg-slate-800 text-white dark:bg-slate-200 dark:text-slate-900' : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400 hover:bg-slate-200'
                }`}
              >
                <Clock className="w-3 h-3" />
                Timeline
              </button>
              <button
                onClick={() => { setView('inbox'); setSearchResults(null); setAskAnswer(null); setSelectedNote(null) }}
                className={`flex-1 px-3 py-1.5 text-xs rounded-md flex items-center justify-center gap-1.5 transition-colors ${
                  view === 'inbox' ? 'bg-slate-800 text-white dark:bg-slate-200 dark:text-slate-900' : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400 hover:bg-slate-200'
                }`}
              >
                <InboxIcon className="w-3 h-3" />
                Inbox
                {inboxCount > 0 && (
                  <span className="ml-0.5 px-1.5 py-0.5 rounded-full bg-rose-500 text-white text-[9px] font-bold leading-none">
                    {inboxCount}
                  </span>
                )}
              </button>
            </div>
          </div>

          {/* Task list */}
          <div className="flex-1 overflow-y-auto p-3 space-y-3">
            <div className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Tasks</div>
            {overdueTasks.length > 0 && (
              <div>
                <h3 className="text-xs font-semibold text-rose-500 uppercase mb-1.5">Overdue</h3>
                {overdueTasks.map(t => <TaskItem key={t.id} task={t} onToggle={toggleTask} />)}
              </div>
            )}
            {todayTasks.length > 0 && (
              <div>
                <h3 className="text-xs font-semibold text-amber-500 uppercase mb-1.5">Today</h3>
                {todayTasks.map(t => <TaskItem key={t.id} task={t} onToggle={toggleTask} />)}
              </div>
            )}
            {unscheduledTasks.length > 0 && (
              <div>
                <h3 className="text-xs font-semibold text-slate-400 uppercase mb-1.5">Unscheduled</h3>
                {unscheduledTasks.map(t => <TaskItem key={t.id} task={t} onToggle={toggleTask} />)}
              </div>
            )}
            {tasks.filter(t => t.done).length > 0 && (
              <div>
                <h3 className="text-xs font-semibold text-emerald-500 uppercase mb-1.5">Done</h3>
                {tasks.filter(t => t.done).slice(0, 10).map(t => <TaskItem key={t.id} task={t} onToggle={toggleTask} />)}
              </div>
            )}
            {tasks.length === 0 && (
              <p className="text-xs text-slate-400 text-center py-4">No tasks yet. Capture a thought with a due date.</p>
            )}
          </div>

          {/* Export button */}
          <div className="p-3 border-t border-slate-200 dark:border-slate-800">
            <a href="/api/brain/export" download>
              <Button variant="ghost" size="sm" className="w-full text-xs">
                <Download className="w-3.5 h-3.5 mr-1.5" />
                Export brain (.md)
              </Button>
            </a>
          </div>
        </aside>

        {/* ===== Main content ===== */}
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
                  placeholder="Search keywords, or ask a question (ends with ?)..."
                  className="pl-9"
                />
              </div>
              <Button onClick={handleSearch} size="sm" variant="outline">
                <Search className="w-4 h-4" />
              </Button>
            </div>

            {/* Filter tabs */}
            {view === 'timeline' && (
              <div className="flex gap-1 mt-3 flex-wrap">
                {['all', ...TYPE_LIST].map(f => (
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
            )}
          </div>

          {/* Ask-your-brain answer */}
          {(asking || askAnswer) && (
            <div className="p-4 border-b border-slate-200 dark:border-slate-800 bg-purple-50 dark:bg-purple-950/20">
              {asking ? (
                <div className="flex items-center gap-2 text-sm text-purple-600 dark:text-purple-300">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Asking your brain...
                </div>
              ) : (
                <div>
                  <div className="flex items-start gap-2">
                    <Sparkles className="w-4 h-4 mt-0.5 text-purple-500 flex-shrink-0" />
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
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleCapture()
                }}
              />
              <div className="flex gap-2 items-center">
                <Button onClick={handleCapture} disabled={capturing || !captureText.trim()} size="sm">
                  {capturing ? <><Loader2 className="w-3 h-3 mr-1 animate-pulse" /> Capturing...</> : 'Capture'}
                </Button>
                <Button onClick={() => { setShowCapture(false); setCaptureText('') }} size="sm" variant="ghost">
                  Cancel
                </Button>
                <span className="text-xs text-slate-400 ml-auto">⌘+Enter to capture</span>
              </div>
            </div>
          )}

          {/* Related notes notification */}
          {relatedNotes.length > 0 && (
            <div className="p-3 bg-sky-50 dark:bg-sky-950/20 border-b border-sky-200 dark:border-sky-900">
              <div className="flex items-center gap-2 text-sm text-sky-700 dark:text-sky-300">
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
                <button className="ml-auto text-xs text-slate-400 hover:text-slate-600" onClick={() => setRelatedNotes([])}>
                  <X className="w-3 h-3" />
                </button>
              </div>
            </div>
          )}

          {/* Content area */}
          <div className="flex-1 overflow-y-auto p-4">
            {view === 'inbox' ? (
              <InboxView
                selectedNote={selectedNote}
                onSelect={setSelectedNote}
                onProcessed={markProcessed}
                onArchived={archiveNote}
                onRefresh={fetchInboxCount}
                formatTime={formatTime}
              />
            ) : loading ? (
              <div className="flex items-center justify-center py-12 text-slate-400">
                <Loader2 className="w-6 h-6 animate-spin mr-2" />
                Loading your brain...
              </div>
            ) : filteredNotes.length === 0 ? (
              <div className="text-center py-12">
                <Brain className="w-12 h-12 mx-auto mb-3 text-slate-300" />
                <p className="text-slate-500 text-sm">
                  {searchResults ? 'No results found. Try different keywords.' : 'Your brain is empty.'}
                </p>
                {!searchResults && (
                  <p className="text-slate-400 text-xs mt-1">
                    Click &quot;Speak&quot; to capture a thought by voice, or &quot;Type&quot; to write one.
                  </p>
                )}
              </div>
            ) : (
              <div className="space-y-6 max-w-3xl mx-auto">
                {groupedNotes.map(group => (
                  <div key={group.label}>
                    <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2 sticky top-0 bg-slate-50 dark:bg-slate-950 py-1">
                      {group.label} <span className="text-slate-300">· {group.notes.length}</span>
                    </h2>
                    <div className="space-y-3">
                      {group.notes.map(note => (
                        <NoteCard
                          key={note.id}
                          note={note}
                          selected={selectedNote?.id === note.id}
                          onClick={() => { setSelectedNote(note); setEditing(false) }}
                          onDelete={handleDelete}
                          formatTime={formatTime}
                        />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* ===== Detail panel ===== */}
        {selectedNote && (
          <aside className="w-96 border-l border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 flex flex-col">
            {editing ? (
              <EditPanel
                draft={editDraft}
                setDraft={setEditDraft}
                onSave={saveEdit}
                onCancel={cancelEdit}
                saving={saving}
              />
            ) : (
              <DetailPanel
                note={selectedNote}
                onClose={() => { setSelectedNote(null); setEditing(false) }}
                onEdit={() => startEdit(selectedNote)}
                onReclassify={reclassify}
                reclassifying={reclassifying}
                onMarkProcessed={() => markProcessed(selectedNote)}
                onArchive={() => archiveNote(selectedNote)}
                onToggleTask={toggleTask}
                onAddTask={addTask}
                onDeleteTask={deleteTask}
                onSelectNote={(n) => { setSelectedNote(n); setEditing(false) }}
                allNotes={notes}
                formatTime={formatTime}
              />
            )}
          </aside>
        )}
      </div>
    </main>
  )
}

// --- Sub-components -------------------------------------------------------

function groupByDay(notes: Note[]): { label: string; notes: Note[] }[] {
  const groups: { label: string; notes: Note[] }[] = []
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yesterday = new Date(today.getTime() - 86400000)

  for (const note of notes) {
    const d = new Date(note.createdAt)
    const dDay = new Date(d.getFullYear(), d.getMonth(), d.getDate())
    let label: string
    if (dDay.getTime() === today.getTime()) label = 'Today'
    else if (dDay.getTime() === yesterday.getTime()) label = 'Yesterday'
    else label = d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: d.getFullYear() === now.getFullYear() ? undefined : 'numeric' })

    let g = groups.find(g => g.label === label)
    if (!g) { g = { label, notes: [] }; groups.push(g) }
    g.notes.push(note)
  }
  return groups
}

function NoteCard({ note, selected, onClick, onDelete, formatTime }: {
  note: Note
  selected: boolean
  onClick: () => void
  onDelete: (id: string) => void
  formatTime: (s: string) => string
}) {
  const Icon = TYPE_ICONS[note.type] || PenLine
  const color = TYPE_COLORS[note.type] || 'text-slate-500'
  return (
    <Card
      className={`cursor-pointer hover:shadow-md transition-shadow ${selected ? 'ring-2 ring-purple-400' : ''}`}
      onClick={onClick}
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
              {note.status === 'inbox' && (
                <Badge variant="outline" className="text-[10px] border-amber-400 text-amber-600">
                  <InboxIcon className="w-2.5 h-2.5 mr-0.5" />
                  inbox
                </Badge>
              )}
              {note.tags.split(',').filter(Boolean).map((tag, i) => (
                <Badge key={i} variant="secondary" className="text-[10px]">
                  #{tag.trim()}
                </Badge>
              ))}
              {note.tasks && note.tasks.length > 0 && (
                <Badge variant="outline" className="text-[10px]">
                  <CheckSquare className="w-2.5 h-2.5 mr-0.5" />
                  {note.tasks.filter(t => !t.done).length}/{note.tasks.length}
                </Badge>
              )}
            </div>
          </div>
          <button
            onClick={(e) => { e.stopPropagation(); onDelete(note.id) }}
            className="text-slate-300 hover:text-rose-500 transition-colors p-1"
            title="Delete note"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </CardContent>
    </Card>
  )
}

function TaskItem({ task, onToggle }: { task: Task; onToggle: (id: number, done: boolean) => void }) {
  return (
    <div className="flex items-center gap-2 py-1.5 px-2 rounded hover:bg-slate-100 dark:hover:bg-slate-800 cursor-pointer"
         onClick={() => onToggle(task.id, task.done)}>
      {task.done ? <CheckCircle2 className="w-4 h-4 text-emerald-500 flex-shrink-0" /> : <Circle className="w-4 h-4 text-slate-400 flex-shrink-0" />}
      <div className="flex-1 min-w-0">
        <p className={`text-xs ${task.done ? 'line-through text-slate-400' : ''}`}>{task.text}</p>
        {task.due && <p className="text-[10px] text-slate-400">{new Date(task.due).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}</p>}
      </div>
    </div>
  )
}

function DetailPanel({ note, onClose, onEdit, onReclassify, reclassifying, onMarkProcessed, onArchive, onToggleTask, onAddTask, onDeleteTask, onSelectNote, allNotes, formatTime }: {
  note: Note
  onClose: () => void
  onEdit: () => void
  onReclassify: () => void
  reclassifying: boolean
  onMarkProcessed: () => void
  onArchive: () => void
  onToggleTask: (id: number, done: boolean) => void
  onAddTask: (noteId: string, text: string) => void
  onDeleteTask: (id: number) => void
  onSelectNote: (note: Note) => void
  allNotes: Note[]
  formatTime: (s: string) => string
}) {
  const [newTaskText, setNewTaskText] = useState('')
  const Icon = TYPE_ICONS[note.type] || PenLine

  const links = (note.links || []).filter(l => l.target)
  const backlinks = (note.backlinks || []).filter(l => l.source)

  return (
    <>
      <div className="p-4 border-b border-slate-200 dark:border-slate-800 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Icon className={`w-4 h-4 flex-shrink-0 ${TYPE_COLORS[note.type] || 'text-slate-500'}`} />
          <h2 className="font-semibold text-sm truncate">{note.title}</h2>
        </div>
        <Button size="sm" variant="ghost" onClick={onClose} className="h-7 px-2 flex-shrink-0">
          <X className="w-4 h-4" />
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Tags + meta */}
        <div className="flex items-center gap-2 flex-wrap">
          <Badge variant="outline" className="text-[10px]">{note.type}</Badge>
          {note.status === 'inbox' && (
            <Badge variant="outline" className="text-[10px] border-amber-400 text-amber-600">inbox</Badge>
          )}
          {note.tags.split(',').filter(Boolean).map((tag, i) => (
            <Badge key={i} variant="secondary" className="text-[10px]">#{tag.trim()}</Badge>
          ))}
        </div>
        <div className="flex items-center gap-2 text-xs text-slate-400">
          <Clock className="w-3 h-3" />
          {new Date(note.createdAt).toLocaleString()}
          {note.confidence > 0 && (
            <span>· {Math.round(note.confidence * 100)}% confidence</span>
          )}
          {note.source && <span>· via {note.source}</span>}
        </div>

        {/* Body */}
        <div className="prose prose-sm dark:prose-invert max-w-none">
          <p className="whitespace-pre-wrap text-sm leading-relaxed">{note.body}</p>
        </div>

        {/* Tasks */}
        {note.tasks.length > 0 && (
          <div>
            <h4 className="text-xs font-semibold text-slate-400 uppercase mb-2">Tasks</h4>
            {note.tasks.map(t => (
              <div key={t.id} className="flex items-center gap-2 py-1 group">
                <button onClick={() => onToggleTask(t.id, t.done)}>
                  {t.done ? <CheckCircle2 className="w-4 h-4 text-emerald-500" /> : <Circle className="w-4 h-4 text-slate-400" />}
                </button>
                <span className={`text-sm flex-1 ${t.done ? 'line-through text-slate-400' : ''}`}>{t.text}</span>
                {t.due && <span className="text-xs text-slate-400">{new Date(t.due).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}</span>}
                <button onClick={() => onDeleteTask(t.id)} className="text-slate-300 hover:text-rose-500 opacity-0 group-hover:opacity-100 transition-opacity">
                  <Trash2 className="w-3 h-3" />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Add task */}
        <div className="flex gap-2">
          <Input
            value={newTaskText}
            onChange={(e) => setNewTaskText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && newTaskText.trim()) {
                onAddTask(note.id, newTaskText)
                setNewTaskText('')
              }
            }}
            placeholder="Add a task..."
            className="h-8 text-xs"
          />
          <Button
            size="sm"
            variant="outline"
            className="h-8 px-2"
            onClick={() => {
              if (newTaskText.trim()) {
                onAddTask(note.id, newTaskText)
                setNewTaskText('')
              }
            }}
          >
            <Plus className="w-3 h-3" />
          </Button>
        </div>

        {/* Related notes (links + backlinks) */}
        {(links.length > 0 || backlinks.length > 0) && (
          <div>
            <h4 className="text-xs font-semibold text-slate-400 uppercase mb-2 flex items-center gap-1">
              <Link2 className="w-3 h-3" />
              Connections
            </h4>
            {links.length > 0 && (
              <div className="mb-2">
                <p className="text-[10px] text-slate-400 mb-1">Links to</p>
                {links.map(l => (
                  <button
                    key={l.id}
                    onClick={() => {
                      const target = allNotes.find(n => n.id === l.target?.id)
                      if (target) onSelectNote(target)
                    }}
                    className="flex items-center gap-1 text-xs text-sky-600 dark:text-sky-400 hover:underline py-0.5"
                  >
                    <ChevronRight className="w-3 h-3" />
                    {l.target?.title}
                    <span className="text-slate-400">({(l.similarity * 100).toFixed(0)}%)</span>
                  </button>
                ))}
              </div>
            )}
            {backlinks.length > 0 && (
              <div>
                <p className="text-[10px] text-slate-400 mb-1">Linked from</p>
                {backlinks.map(l => (
                  <button
                    key={l.id}
                    onClick={() => {
                      const source = allNotes.find(n => n.id === l.source.id)
                      if (source) onSelectNote(source)
                    }}
                    className="flex items-center gap-1 text-xs text-sky-600 dark:text-sky-400 hover:underline py-0.5"
                  >
                    <ChevronRight className="w-3 h-3" />
                    {l.source.title}
                    <span className="text-slate-400">({(l.similarity * 100).toFixed(0)}%)</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Action bar */}
      <div className="p-3 border-t border-slate-200 dark:border-slate-800 flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={onEdit} className="text-xs">
          <Edit3 className="w-3 h-3 mr-1" />
          Edit
        </Button>
        <Button size="sm" variant="outline" onClick={onReclassify} disabled={reclassifying} className="text-xs">
          {reclassifying ? <Loader2 className="w-3 h-3 mr-1 animate-spin" /> : <RefreshCw className="w-3 h-3 mr-1" />}
          Reclassify
        </Button>
        {note.status === 'inbox' && (
          <Button size="sm" variant="outline" onClick={onMarkProcessed} className="text-xs">
            <Check className="w-3 h-3 mr-1" />
            Mark reviewed
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={onArchive} className="text-xs text-slate-500 ml-auto">
          <Archive className="w-3 h-3 mr-1" />
          Archive
        </Button>
      </div>
    </>
  )
}

function EditPanel({ draft, setDraft, onSave, onCancel, saving }: {
  draft: { title: string; body: string; type: string; tags: string }
  setDraft: (d: { title: string; body: string; type: string; tags: string }) => void
  onSave: () => void
  onCancel: () => void
  saving: boolean
}) {
  return (
    <>
      <div className="p-4 border-b border-slate-200 dark:border-slate-800 flex items-center justify-between">
        <h2 className="font-semibold text-sm">Edit note</h2>
        <Button size="sm" variant="ghost" onClick={onCancel} className="h-7 px-2">
          <X className="w-4 h-4" />
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        <div>
          <label className="text-xs font-semibold text-slate-400 uppercase">Title</label>
          <Input
            value={draft.title}
            onChange={(e) => setDraft({ ...draft, title: e.target.value })}
            className="mt-1"
          />
        </div>
        <div>
          <label className="text-xs font-semibold text-slate-400 uppercase">Type</label>
          <div className="flex gap-1 mt-1 flex-wrap">
            {['idea', 'task', 'reference', 'question', 'journal', 'dictation', 'uncategorized'].map(t => (
              <button
                key={t}
                onClick={() => setDraft({ ...draft, type: t })}
                className={`px-2 py-1 text-xs rounded ${
                  draft.type === t ? 'bg-slate-800 text-white dark:bg-slate-200 dark:text-slate-900' : 'bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400'
                }`}
              >
                {t}
              </button>
            ))}
          </div>
        </div>
        <div>
          <label className="text-xs font-semibold text-slate-400 uppercase">Tags (comma-separated)</label>
          <Input
            value={draft.tags}
            onChange={(e) => setDraft({ ...draft, tags: e.target.value })}
            className="mt-1"
            placeholder="tag1, tag2, tag3"
          />
        </div>
        <div>
          <label className="text-xs font-semibold text-slate-400 uppercase">Body</label>
          <Textarea
            value={draft.body}
            onChange={(e) => setDraft({ ...draft, body: e.target.value })}
            className="mt-1 min-h-[200px]"
          />
        </div>
      </div>
      <div className="p-3 border-t border-slate-200 dark:border-slate-800 flex gap-2">
        <Button size="sm" onClick={onSave} disabled={saving}>
          {saving ? <Loader2 className="w-3 h-3 mr-1 animate-spin" /> : <Check className="w-3 h-3 mr-1" />}
          Save
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </>
  )
}

// --- Inbox / Review view (B3) --------------------------------------------

function InboxView({ selectedNote, onSelect, onProcessed, onArchived, onRefresh, formatTime }: {
  selectedNote: Note | null
  onSelect: (n: Note) => void
  onProcessed: (n: Note) => void
  onArchived: (n: Note) => void
  onRefresh: () => void
  formatTime: (s: string) => string
}) {
  const [inboxNotes, setInboxNotes] = useState<Note[]>([])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [loading, setLoading] = useState(true)
  const [filter, setInboxFilter] = useState<string>('all')

  const fetchInbox = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch(`/api/brain/inbox?limit=50${filter !== 'all' ? `&type=${filter}` : ''}`)
      const data = await res.json()
      setInboxNotes(data.notes || [])
      setCounts(data.counts || {})
    } catch (e) {
      console.error('fetch inbox failed', e)
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => {
    fetchInbox()
  }, [fetchInbox])

  const total = Object.values(counts).reduce((a: number, b: any) => a + Number(b), 0)

  const handleAction = async (note: Note, action: 'processed' | 'archived') => {
    if (action === 'processed') onProcessed(note)
    else onArchived(note)
    // Remove from local list immediately for snappy UX
    setInboxNotes(prev => prev.filter(n => n.id !== note.id))
    onRefresh()
  }

  return (
    <div className="max-w-3xl mx-auto">
      <div className="mb-4">
        <h2 className="text-lg font-semibold flex items-center gap-2">
          <InboxIcon className="w-5 h-5 text-amber-500" />
          Review your inbox
          {total > 0 && <span className="text-sm font-normal text-slate-400">({total} unreviewed)</span>}
        </h2>
        <p className="text-xs text-slate-500 mt-1">
          Process each note: fix its tags/type, mark it reviewed, or archive it. Keep your brain clean.
        </p>

        {/* Type filter */}
        <div className="flex gap-1 mt-3 flex-wrap">
          <button
            onClick={() => setInboxFilter('all')}
            className={`px-3 py-1 text-xs rounded-full transition-colors ${filter === 'all' ? 'bg-slate-800 text-white dark:bg-slate-200 dark:text-slate-900' : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400'}`}
          >
            all ({total})
          </button>
          {TYPE_LIST.filter(t => counts[t]).map(t => (
            <button
              key={t}
              onClick={() => setInboxFilter(t)}
              className={`px-3 py-1 text-xs rounded-full transition-colors ${filter === t ? 'bg-slate-800 text-white dark:bg-slate-200 dark:text-slate-900' : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400'}`}
            >
              {t} ({counts[t]})
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-12 text-slate-400">
          <Loader2 className="w-6 h-6 animate-spin mr-2" />
          Loading inbox...
        </div>
      ) : inboxNotes.length === 0 ? (
        <div className="text-center py-12">
          <CheckCircle2 className="w-12 h-12 mx-auto mb-3 text-emerald-400" />
          <p className="text-slate-600 dark:text-slate-300 text-sm font-medium">Inbox zero!</p>
          <p className="text-slate-400 text-xs mt-1">All caught up. Capture more thoughts to fill your brain.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {inboxNotes.map(note => {
            const Icon = TYPE_ICONS[note.type] || PenLine
            const color = TYPE_COLORS[note.type] || 'text-slate-500'
            return (
              <Card key={note.id} className={selectedNote?.id === note.id ? 'ring-2 ring-purple-400' : ''}>
                <CardContent className="py-3">
                  <div className="flex items-start gap-3">
                    <Icon className={`w-5 h-5 mt-0.5 flex-shrink-0 ${color}`} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between gap-2">
                        <h3 className="font-medium text-sm truncate cursor-pointer hover:text-purple-600" onClick={() => onSelect(note)}>
                          {note.title}
                        </h3>
                        <span className="text-xs text-slate-400 flex-shrink-0">{formatTime(note.createdAt)}</span>
                      </div>
                      <p className="text-sm text-slate-600 dark:text-slate-400 mt-1 line-clamp-3">{note.body}</p>
                      <div className="flex items-center gap-2 mt-2 flex-wrap">
                        {note.tags.split(',').filter(Boolean).map((tag, i) => (
                          <Badge key={i} variant="secondary" className="text-[10px]">#{tag.trim()}</Badge>
                        ))}
                      </div>
                      <div className="flex gap-2 mt-3">
                        <Button size="sm" variant="outline" className="text-xs h-7" onClick={() => onSelect(note)}>
                          <Edit3 className="w-3 h-3 mr-1" />
                          Open
                        </Button>
                        <Button size="sm" variant="outline" className="text-xs h-7" onClick={() => handleAction(note, 'processed')}>
                          <Check className="w-3 h-3 mr-1" />
                          Reviewed
                        </Button>
                        <Button size="sm" variant="ghost" className="text-xs h-7 text-slate-500" onClick={() => handleAction(note, 'archived')}>
                          <Archive className="w-3 h-3 mr-1" />
                          Archive
                        </Button>
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            )
          })}
        </div>
      )}
    </div>
  )
}
