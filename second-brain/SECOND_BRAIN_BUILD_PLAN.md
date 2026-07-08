# SECOND BRAIN — Complete Build Plan

## The product in one sentence

A **local-first desktop app** that captures your spoken thoughts, auto-organizes them with AI, and proactively surfaces relevant knowledge — a spare brain that remembers what you forget.

---

## Technical Decisions (locked)

| Decision | Choice | Why |
|----------|--------|-----|
| Desktop framework | **Tauri v2** | 3-10MB binary (vs 200MB Electron), 40-80MB RAM, native global hotkeys + tray, Python sidecar support, reuses Next.js frontend via static export |
| Markdown viewer | **Build our own** (CodeMirror 6 + react-markdown) | Obsidian is proprietary (can't fork). Logseq is AGPL+Clojure (wrong paradigm+license). Building minimal is ~8-10 days vs months forking |
| LLM — Tier 1 (always-on) | **Qwen2.5-0.5B + GBNF grammar** | Already downloaded. 469MB, ~700MB RAM, ~150ms. GBNF forces valid JSON even from tiny models. Runs classification + tag extraction |
| LLM — Tier 2 (on-demand) | **Qwen2.5-3B-Instruct Q4** (~2GB) | Best sub-3B for JSON (95.7% parse rate). Native tool-calling via `--jinja`. Loaded lazily for reasoning tasks, unloaded on idle |
| STT | **Whisper (base or small)** via whisper.cpp | Already have the pipeline from Wispr Flow |
| Storage | **Markdown files on disk** + SQLite index | .md files are the source of truth (Obsidian-compatible, future-proof, no lock-in). SQLite is a derived index that can be rebuilt |
| Search | **SQLite FTS5** (keyword) + **sqlite-vec** (semantic) | One .db file, zero servers, both search types in one query |
| Vault location | `~/SecondBrain/` (user-configurable) | Plain folder of .md files — opens in Obsidian for free if user wants |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    TAURI DESKTOP APP                     │
│                                                          │
│  ┌─────────────┐  ┌───────────────┐  ┌───────────────┐ │
│  │  Frontend   │  │  Rust Backend │  │ Python Sidecar│ │
│  │  (Next.js   │  │  (Tauri cmds) │  │ (whisper.cpp  │ │
│  │   static    │  │  - hotkeys    │  │  + llama.cpp  │ │
│  │   export)   │  │  - tray       │  │  + classifier)│ │
│  │             │  │  - file watch │  │               │ │
│  │  - Timeline │  │  - SQLite     │  │  POST /capture│ │
│  │  - Search   │  │    via plugin │  │  POST /transcribe│
│  │  - Note view│  │  - sidecar    │  │  POST /ask    │
│  │  - Task list│  │    mgmt       │  │  GET  /search │ │
│  │  - Ask brain│  │               │  │               │ │
│  └──────┬──────┘  └───────┬───────┘  └───────┬───────┘ │
│         │                 │                   │         │
│         └────────────────┴───────────────────┘         │
│                          │                              │
│              ┌───────────▼───────────┐                  │
│              │   ~/SecondBrain/      │                  │
│              │   ├── .brain/         │                  │
│              │   │   ├── brain.db    │ (SQLite + FTS5)  │
│              │   │   ├── vec.db      │ (sqlite-vec)     │
│              │   │   └── config.json │                  │
│              │   ├── Inbox/          │                  │
│              │   ├── Ideas/          │                  │
│              │   ├── Tasks/          │                  │
│              │   ├── References/     │                  │
│              │   ├── Projects/       │                  │
│              │   └── Daily/          │                  │
│              └───────────────────────┘                  │
└──────────────────────────────────────────────────────────┘
```

**Data flow:**
1. User presses **Ctrl+Shift+B** (brain capture hotkey)
2. Tauri Rust backend detects hotkey → starts mic recording (Python sidecar)
3. User speaks → releases hotkey
4. Python sidecar: Whisper transcribes → Qwen-0.5B classifies (idea/task/reference?) + extracts tags + detects tasks
5. Python writes a `.md` file to `~/SecondBrain/Inbox/` with frontmatter (tags, type, timestamp, raw transcript, polished text)
6. Rust file watcher detects new file → indexes in SQLite + computes embedding
7. sqlite-vec finds semantically similar notes → if found, sends subtle notification
8. Frontend timeline updates with the new capture

---

## Feature List (with exact behavior + edge cases)

### Feature 1: Voice Capture (Ctrl+Shift+B)
**What it does:** Hold Ctrl+Shift+B → speak → release. Your spoken thought is captured, transcribed, classified, and stored.

**Exact behavior:**
- Hotkey press → overlay window appears (small, top-center, shows "Listening..." + waveform)
- Speaking → live waveform animation
- Hotkey release → overlay shows "Processing..." → transcript appears → classification runs
- On success → overlay shows "✓ Captured: [first 40 chars]" for 2s → fades out
- On empty transcript → "No speech detected" → fades out after 3s
- On error → "Capture failed: [error]" → fades out after 5s

**Edge cases handled:**
- Hotkey held >60s → auto-stop + transcribe what we have
- Hotkey pressed while already processing → queue the new capture (don't lose it)
- No microphone → "No microphone detected" notification
- Whisper crashes → return raw error, don't hang
- LLM classification fails → store note as `type: uncategorized`, tag `#inbox`, don't block

**What it does NOT do:**
- Does NOT paste text into the focused app (that's Wispr Flow's dictation mode, separate hotkey)
- Does NOT require internet
- Does NOT send audio anywhere off-device

### Feature 2: Auto-Classification
**What it does:** When a note is captured, the LLM classifies it as one of:

| Type | Trigger signals | What happens |
|------|----------------|--------------|
| `idea` | "what if", "maybe we should", "I was thinking" | Stored in `Ideas/`, tagged with topic |
| `task` | "remind me", "I need to", "let's", "by Friday" | Stored in `Tasks/`, due date extracted, added to task list |
| `reference` | "according to", "X said that", quotes | Stored in `References/`, source noted |
| `question` | "how do we", "what about", "I wonder" | Stored in `Ideas/`, tagged `#question` |
| `journal` | Default when no clear signal | Stored in `Daily/`, timestamped |

**Exact behavior:**
- Qwen-0.5B runs with a **GBNF grammar** that forces output to: `{"type": "idea|task|reference|question|journal", "tags": ["tag1", "tag2"], "tasks": [{"text": "...", "due": "2026-01-15|null"}], "confidence": 0.0-1.0}`
- If confidence < 0.5 → default to `journal`, tag `#inbox`
- If task extraction finds dates → parse to ISO format ("Friday" → next Friday's date)
- Tags are limited to a controlled vocabulary (top 50 tags) + free-form for new ones

**Edge cases:**
- LLM returns malformed JSON (shouldn't happen with GBNF, but...) → fallback to `journal` + `#inbox`
- User speaks in mixed language → classify based on content, don't force English
- User says something that's both an idea AND a task ("What if we ship by Friday?") → classify as `task` (actionable wins), tag `#idea` too
- Date parsing fails → store task without due date, tag `#unscheduled`

### Feature 3: Auto-Tagging
**What it does:** Every captured note gets 1-5 tags automatically.

**Tag taxonomy (3 layers):**
1. **Type tags** (system): `#idea`, `#task`, `#reference`, `#question`, `#journal`
2. **Topic tags** (LLM-extracted): `#ai`, `#coding`, `#design`, `#meeting`, etc.
3. **Status tags** (workflow): `#inbox` (new), `#processed` (reviewed), `#archived`

**Exact behavior:**
- LLM extracts topic tags from the transcript content
- New tags are allowed (the LLM can invent tags it hasn't seen before)
- User can merge/rename tags later via the UI
- Tags are stored both in the .md frontmatter AND in SQLite for querying

**Edge cases:**
- LLM generates too many tags (>5) → keep top 5 by confidence
- LLM generates duplicate tags with different casing → normalize to lowercase
- Tag contains spaces → replace with kebab-case (`#machine learning` → `#machine-learning`)

### Feature 4: Semantic Auto-Linking
**What it does:** When a new note is captured, the system finds existing notes that are semantically similar and links them.

**Exact behavior:**
- New note is embedded using all-MiniLM-L6-v2 (384-dim, ~90MB, <50ms CPU)
- sqlite-vec queries for top 5 most similar notes (cosine similarity > 0.75)
- If matches found:
  - `## Related` section added to the note with `[[wikilinks]]` to matching notes
  - Subtle notification: "Linked to 3 related notes" (clickable → opens the note)
- If no matches above threshold → no links added, no notification

**Edge cases:**
- First note ever captured → no existing notes to link → skip silently
- All 5 matches are the same note repeated → deduplicate, show only unique matches
- Match is the note itself (shouldn't happen, but guard against it)
- Embedding model fails → skip linking, store note anyway, retry embedding later

### Feature 5: Proactive Surfacing
**What it does:** When you capture a new note, the system checks if it's related to anything you're currently working on and subtly notifies you.

**Exact behavior:**
- "Currently working on" = notes captured/edited in the last 24 hours
- When a new note is captured, check if it's semantically similar to any note from the last 24h
- If yes → notification: "This connects to what you were working on: [note title]"
- Notification is non-blocking (system notification, not a modal)
- Clicking the notification opens both notes side-by-side

**Edge cases:**
- User disables notifications → store the connection silently, show it in the timeline
- Too many connections (>5) → show only the top 3 by similarity
- User is in "focus mode" (configurable) → suppress all notifications

### Feature 6: Search ("Ask Your Brain")
**What it does:** A search bar where you can type or speak a question, and the system finds relevant notes using both keyword (FTS5) and semantic (vector) search.

**Exact behavior:**
- Text input: type a query → results appear as you type (debounced 300ms)
- Voice input: click mic icon → speak → transcribe → search
- Results ranked by: semantic similarity (70%) + keyword match (30%) + recency (10%)
- Each result shows: title, snippet with highlighted match, tags, date, similarity score
- Clicking a result opens the note in the viewer
- "Ask your brain" mode: instead of keyword search, ask a question ("What did I think about embeddings?") → LLM answers using RAG over matching notes

**Edge cases:**
- No results → "No notes match. Try different keywords."
- Brain is empty (first use) → "Your brain is empty. Capture your first thought with Ctrl+Shift+B."
- RAG answer has no sources → "I couldn't find anything about that in your notes."
- Query is very short (1 char) → wait for more input before searching

### Feature 7: Task Management
**What it does:** All tasks extracted from voice captures are aggregated into a task list.

**Exact behavior:**
- Task list view shows all tasks sorted by due date (overdue first, then upcoming, then unscheduled)
- Each task: checkbox (complete), text, due date, source note link, project tag
- Completing a task → marks `- [x]` in the source .md file + updates SQLite
- Overdue tasks → highlighted in red
- Tasks due today → highlighted in amber
- Clicking a task → opens the source note

**Edge cases:**
- Task has no due date → shows at bottom under "Unscheduled"
- Task is completed → moves to "Completed" section (not deleted)
- User edits a task in the .md file directly → file watcher detects change → updates DB
- Duplicate tasks (same text) → deduplicate on creation, keep the earliest

### Feature 8: Daily Timeline
**What it does:** A timeline view showing everything you captured, organized by day.

**Exact behavior:**
- Default view: today's captures in reverse chronological order
- Scroll up → previous days
- Each entry: time, type icon (💡 idea, ✅ task, 📎 reference, ? question, 📝 journal), title, first line
- Click → opens note in viewer
- Filter by type (show only tasks, only ideas, etc.)
- Filter by tag

**Edge cases:**
- No captures today → "Nothing captured today. Press Ctrl+Shift+B to capture a thought."
- Very long day (>50 captures) → virtual scroll for performance
- Captures from Wispr Flow dictation mode → NOT shown (only brain captures)

### Feature 9: Note Viewer/Editor
**What it does:** View and edit markdown notes with live rendering.

**Exact behavior:**
- Split view: editor (left) / preview (right) — toggleable
- Markdown rendered with: headings, bold/italic, code blocks, wikilinks, tags, task lists, callouts
- Wikilinks `[[note]]` are clickable → navigate to that note
- Tags `#tag` are clickable → filter by that tag
- Backlinks panel (right sidebar): shows all notes that link to this one
- Edit → autosave to .md file (debounced 1s) → file watcher updates DB

**Edge cases:**
- Wikilink target doesn't exist → show as red/dashed, clicking creates it
- User edits frontmatter → validate YAML on save, show error if invalid
- File deleted externally → show "This note was deleted" → remove from DB
- Large note (>10k chars) → virtualized editor for performance

### Feature 10: Wispr Flow Integration (Dictation Mode)
**What it does:** Wispr Flow's existing dictation (Ctrl+Shift+Space) still works for pasting text into apps. But now, every dictation is ALSO optionally logged to the second brain.

**Exact behavior:**
- Dictation mode (Ctrl+Shift+Space): speak → text pasted into focused app (unchanged)
- After paste → subtle prompt: "Log to brain?" (yes/no) — auto-dismisses after 5s
- If yes → creates a lightweight note (type: `dictation`, tag: `#dictation-log`) with the text + app context
- If no or timeout → nothing stored
- This is separate from Capture mode (Ctrl+Shift+B) which always stores

**Edge cases:**
- User is dictating a password or sensitive text → "Log to brain?" defaults to No
- User disables dictation logging in settings → never prompt
- User says "yes" but the brain is full (disk space) → show error, don't crash

---

## Hotkey Map

| Hotkey | Mode | What it does |
|--------|------|-------------|
| Ctrl+Shift+Space | Dictation | Speak → text pasted into focused app (Wispr Flow, unchanged) |
| Ctrl+Shift+B | Brain Capture | Speak → thought stored in second brain with auto-tagging |
| Ctrl+Shift+F | Brain Search | Open search bar (type or speak a query) |
| Ctrl+Shift+T | Transform | Select text → speak instruction → text rewritten (Wispr Flow, unchanged) |

All hotkeys are user-configurable in settings.

---

## Failure Modes & Mitigations

| What could fail | Impact | Mitigation |
|----------------|--------|------------|
| Whisper model not found | Can't capture | Show setup wizard on first run; download model automatically |
| Qwen LLM not found | Can't classify | Fall back to `journal` + `#inbox`; prompt user to download model |
| SQLite DB corrupted | Can't search | .md files are source of truth; DB is rebuildable. Add "Rebuild index" button in settings |
| Disk full | Can't write notes | Check disk space before capture; show warning at 95% full |
| Hotkey conflict with another app | Hotkey doesn't fire | Settings page with "rebind hotkey" + conflict detection |
| Python sidecar crashes | App stops working | Tauri restarts sidecar automatically; show "reconnecting..." in UI |
| User deletes vault folder externally | All notes "disappear" | Detect missing folder on startup; offer to recreate or point to new location |
| Embedding model missing | Can't auto-link | Skip linking silently; notes still work, just no semantic search |
| Very long capture (>5 min) | Memory pressure | Auto-chunk audio; process segments; stitch transcript |
| User speaks in non-English | Wrong classification | Whisper auto-detects language; LLM prompt includes detected language |
| Two captures happen simultaneously | Race condition | Capture queue (one at a time); second capture waits for first to finish |
| User upgrades and schema changes | DB migration | Prisma migrations; version field in config.json; auto-migrate on startup |

---

## Future-Proofing

| Future need | How the architecture supports it |
|-------------|-------------------------------|
| Mobile app | .md files + SQLite sync via Syncthing or custom sync. The brain is just a folder. |
| Multiple vaults | Config file points to vault path; switchable in settings |
| Plugin system | Tauri commands are the plugin API; Python sidecar can load plugins |
| Cloud sync (optional, user opt-in) | .md files sync to S3/Dropbox; SQLite rebuilt from files on other devices |
| Voice commands ("create a project called X") | Tier-2 LLM (Qwen-3B) with tool-calling parses intent → executes Tauri command |
| Meeting transcription | Same pipeline, longer audio; auto-chunk; speaker diarization via whisper.cpp |
| Web clipper | Browser extension writes .md to vault folder; file watcher indexes it |
| Email integration | IMAP poller writes .md notes; same pipeline |
| Collaborative brains | SQLite + CRDT (Yjs/Automerge) for conflict-free multi-user editing |
| AI agent mode | Tier-2 LLM with tool-calling can create notes, link them, search, and answer questions autonomously |

---

## Build Order (phases)

### Phase 1: Foundation (Week 1-2)
- Tauri shell with system tray + global hotkey (Ctrl+Shift+B)
- Python sidecar: Whisper transcription → Qwen-0.5B classification
- Write .md files to `~/SecondBrain/` with frontmatter
- SQLite index + FTS5 search
- Minimal UI: timeline view + search bar

### Phase 2: Brain Features (Week 3-4)
- Auto-tagging with GBNF grammar
- Task extraction + task list view
- Embeddings (all-MiniLM-L6-v2) + sqlite-vec
- Auto-linking + proactive surfacing
- Note viewer/editor with markdown rendering + backlinks

### Phase 3: Integration (Week 5-6)
- Wispr Flow dictation mode integration (Ctrl+Shift+Space still works, optional brain logging)
- "Ask your brain" RAG chat
- Daily timeline with filters
- Settings page (hotkey config, model paths, vault location)

### Phase 4: Polish (Week 7-8)
- Onboarding wizard (first-run model download)
- Theme system (light/dark)
- Notification system (native OS notifications)
- Performance optimization (virtual scrolling, lazy loading)
- Packaging (Tauri installer for Windows)

---

## What I Need From You Before Coding

1. **Vault location**: `~/SecondBrain/` is my default suggestion. OK, or do you want it elsewhere?

2. **Model budget**: Your machine has 8GB RAM. The full stack is:
   - Tauri app: ~80MB
   - Whisper (base): ~500MB
   - Qwen-0.5B (Tier 1, always-on): ~700MB
   - Qwen-3B (Tier 2, on-demand): ~2.5GB (loaded only when needed)
   - Total: ~3.8GB (fits in 8GB with room for OS + browser)
   
   Is 8GB your actual RAM, or do you have more? This determines whether we use Whisper-base or Whisper-small.

3. **First language**: The classification LLM prompt and tag vocabulary should match your primary language. English? Or do you want multi-language support from day 1?

4. **Wispr Flow integration**: Do you want the dictation-logging prompt ("Log to brain?") after every Wispr Flow paste, or should that be opt-in only?

5. **Priority**: If we can only build ONE feature first to prove the concept, which matters most to you — the voice capture + auto-classification, or the "ask your brain" search?
