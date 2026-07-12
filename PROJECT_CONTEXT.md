# PROJECT CONTEXT — Full Handoff Document

**Repository:** `github.com/ParthVarekar/whisper_flow_clone_local`
**Last updated:** July 11, 2026
**Purpose:** This document gives a complete picture of the repository so a new agent can pick up either sub-project.

---

## 1. Repository Overview

The repo contains **two independent sub-projects** that share a folder but have separate tech stacks, dependencies, and runtimes:

| Sub-project | Path | Stack | Purpose |
|---|---|---|---|
| **WhisperFlow** | `/` (root) + `whisper_flow/` | Python 3.10+ | Voice dictation daemon — speak → transcribe → clean → paste at cursor |
| **Second Brain** | `second-brain/` | Next.js 16 + Prisma + SQLite | Knowledge management app — capture thoughts, auto-classify, RAG search |

**They are decoupled.** WhisperFlow is a standalone Python daemon. Second Brain is a standalone Next.js web app. They can be developed, tested, and deployed independently. The only conceptual link is that Second Brain's build plan envisions eventually using WhisperFlow's ASR pipeline as a Python sidecar inside a Tauri desktop wrapper — but that integration has NOT been built yet.

---

## 2. WhisperFlow (Python Voice Dictation)

### 2.1 What it is
A Wispr-Flow-style voice dictation daemon for Windows (cross-platform code, tested on Windows). Hold `Ctrl+Shift+Space` → speak → release → cleaned text is pasted at your cursor. Runs as a system tray app.

### 2.2 Architecture (Phase 1 — shipped)

```
start.bat
  → activates .qa-venv or .venv
  → auto-installs pip deps
  → python -m whisper_flow daemon --config config.llama4.toml

Daemon (whisper_flow/daemon.py):
  Ctrl+Shift+Space held
    → LiveMicCapture (sounddevice) records rolling + full audio
    → live preview loop transcribes every ~0.6s for overlay display
  Ctrl+Shift+Space released
    → MoonshineBackend.transcribe(full_audio)
    → apply_smart_formatting() — rule-based cleanup
    → insert_text() — clipboard + SendInput Ctrl+V
    → save_dictation() — history log

  Ctrl+Shift+T (command mode)
    → copies selected text
    → records voice instruction
    → LLM transforms selected text per instruction (graceful fallback if no LLM)
```

### 2.3 Key Configuration (`config.llama4.toml`)

```toml
mode = "raw"              # "raw" = rule-based only (NO LLM, sub-100ms)
                          # "auto" = LLM cleanup (needs llama-server on port 8081)

[transcription]
backend = "moonshine"     # Moonshine Tiny (27M, English-only, ONNX, in-process)
language = "en"           # Moonshine Tiny is English-only

[audio]
mic_backend = "sounddevice"
stream = true
stream_chunk_s = 1
stream_max_s = 12

[llm]
# Only used when mode = "auto"
mode = "server"
port = 8081
model = '...gemma-4-E4B-it-UD-Q4_K_XL.gguf'
```

### 2.4 Dependencies (`requirements.txt`)

```
moonshine-voice    # ASR backend (27M Tiny, ONNX Runtime, in-process)
sounddevice        # Mic capture (PortAudio binding)
pynput             # Global hotkeys (Ctrl+Shift+Space) — CRITICAL
pystray            # System tray icon
Pillow             # Image support for tray icon
numpy              # Audio arrays
pyperclip          # Clipboard fallback
tomli              # TOML config (Python <3.11 only)
```

`start.bat` auto-installs any missing package on first run.

### 2.5 The 13-Stage Rule-Based Cleanup Pipeline (`whisper_flow/formatting.py`)

This is the Phase 1 cleanup that runs at ~11ms on CPU (no LLM needed):

1. Spoken punctuation words → symbols (`"period"` → `"."`)
2. Spoken newline words → newlines (`"new paragraph"` → `"\n\n"`)
3. Filler-word removal (`"um"`, `"uh"`, `"you know"`, `"basically"`, `"literally"`, etc.)
4. Backtrack correction (`"...store. Actually ...market."` → `"...market."`)
5. Repeated-word/stutter collapse (`"the the"` → `"the"`, `"I I I"` → `"I"`)
6. ITN: time (`"three thirty pm"` → `"3:30 PM"`, `"nine o clock"` → `"9:00"`)
7. ITN: dates/ordinals (`"march fifth"` → `"March 5th"`, `"monday"` → `"Monday"`)
8. ITN: currency (`"twenty dollars"` → `"$20"`, `"five cents"` → `"5¢"`)
9. ITN: numbers (`"twenty five"` → `25`, `"two million three hundred thousand"` → `2300000`)
10. Capitalization (sentence starts, standalone `"i"` → `"I"`, days, months)
11. Spacing normalization (double spaces, space before punct, time/decimal protection)
12. Trailing punctuation (add `.` if missing, collapse duplicates like `". ."` → `"."`)
13. Writing style (formal / casual / enthusiastic / very_casual with contractions)

**66 unit tests** in `tests/test_formatting.py` + `tests/test_formatting_phase1.py` — all pass.

### 2.6 Key Modules

| File | Purpose |
|---|---|
| `whisper_flow/daemon.py` | Main orchestrator — hotkeys, recording, transcription, cleanup, insertion |
| `whisper_flow/backends/moonshine.py` | Moonshine Tiny ASR backend (ONNX, in-process) |
| `whisper_flow/formatting.py` | 13-stage rule-based cleanup pipeline (Phase 1) |
| `whisper_flow/hotkeys.py` | Global hotkey listener via `pynput` (Ctrl+Shift+Space, Ctrl+Shift+T) |
| `whisper_flow/audio.py` | `LiveMicCapture` — sounddevice streaming mic capture with rolling window |
| `whisper_flow/inserter.py` | Windows ctypes clipboard + SendInput text insertion |
| `whisper_flow/overlay.py` | Tkinter floating HUD (listening/processing/result states) |
| `whisper_flow/tray.py` | `pystray` system tray icon with mode/style menu |
| `whisper_flow/app_detect.py` | Windows ctypes foreground window detection → app category |
| `whisper_flow/config.py` | Config loading (JSON/TOML) with env var + CLI override layers |
| `whisper_flow/pipeline.py` | STT + LLM pipeline orchestration |
| `whisper_flow/prompts.py` | LLM prompt templates + mode resolution (`resolve_mode`) |
| `whisper_flow/backends/llama_cpp.py` | LLM backend (HTTP to llama-server or CLI subprocess) |

### 2.7 Phase 2 — Fine-Tuning Scripts (`training/`)

Prepared but NOT yet run (requires GPU + training data):

| File | Lines | Purpose |
|---|---|---|
| `training/README.md` | 455 | Phase 2 recipe (FormalASR methodology, arXiv:2605.19266v3) |
| `training/prepare_data.py` | 1,043 | Teacher pipeline (Whisper Large v3 + LLM cleanup → JSONL pairs) |
| `training/sft_train.py` | 557 | LoRA/PEFT fine-tuning with CER/WER eval |
| `training/export_onnx.py` | 383 | ONNX export + verification + install to moonshine-voice assets |

**Goal:** Fine-tune Moonshine Tiny to emit cleaned text directly, closing the remaining 10-20% quality gap vs LLM cleanup at the same sub-100ms latency.

### 2.8 How to Run

```powershell
cd C:\Users\Parth\Desktop\whisper
git pull origin main
start.bat
```

Then hold `Ctrl+Shift+Space` to dictate. Right-click tray icon to change mode/style or quit.

### 2.9 Known State (as of July 11, 2026)

**Working:**
- Moonshine ASR (fixed: was returning empty transcripts due to API misunderstanding)
- Rule-based cleanup (13 stages, 66 tests)
- Windows text insertion (ctypes SendInput)
- Global hotkeys (pynput)
- System tray with mode/style selection
- Graceful LLM fallback (if mode="auto" but no llama-server)
- Config defaults to `mode="raw"` (no LLM needed)

**Known issues / future work:**
- Phase 2 fine-tuning scripts are ready but untested (need GPU + data)
- Moonshine Tiny is English-only (by design — for multilingual, switch backend to `whisper_cpp` or `qwen3_asr`)
- No mobile support yet (Phase 3 — sherpa-onnx for Android, CoreML for iOS)

### 2.10 Recent Git History (WhisperFlow)

```
f4d0918 fix(ux): tray menu missing 'raw' mode option
ee48017 fix(critical): missing pynput dep + overlay mode sync bug
f653250 feat(phase2): fine-tuning preparation scripts for Moonshine Tiny SFT
4f92b51 feat(phase1): comprehensive rule-based cleanup pipeline for sub-100ms dictation
899df96 fix(critical): Moonshine empty transcription bug — extract text from Transcript.lines
e2d23a8 fix: route start.bat + daemon to Moonshine backend, no llama-server needed
```

---

## 3. Second Brain (Next.js Knowledge Management)

### 3.1 What it is
A local-first knowledge management app. Capture spoken/typed thoughts → AI auto-classifies, tags, and extracts tasks → search and ask questions over your knowledge base (RAG).

### 3.2 Tech Stack

- **Framework:** Next.js 16 (App Router) + React 19 + TypeScript 5
- **Database:** Prisma ORM + SQLite
- **AI:** `z-ai-web-dev-sdk` (cloud ASR for transcription + cloud LLM for classification/RAG)
- **Styling:** Tailwind CSS 4 + shadcn/ui (New York style)
- **Icons:** Lucide React
- **Planned:** Tauri v2 desktop wrapper (not yet built)

### 3.3 Architecture

```
User speaks → /api/transcribe (z-ai ASR) → transcript
            → /api/brain/capture (z-ai LLM) → classify + tag + extract tasks
            → SQLite (Prisma) → stored as Note + Tasks
            → keyword matching → auto-linked to related notes

User asks → /api/brain/ask (z-ai LLM + RAG) → answer from notes + tasks
```

### 3.4 Database Schema (`second-brain/prisma/schema.prisma`)

**Models:**
- **Note** — `id, title, body, rawTranscript, type, tags, vaultPath, source, appContext, confidence, status, reviewedAt, createdAt, updatedAt`
  - `type`: idea | task | reference | question | journal | dictation | uncategorized
  - `status`: inbox | processed | archived
  - `source`: voice | text | dictation | import
- **Task** — `id, noteId, text, done, due, createdAt` (linked to Note)
- **Link** — `id, sourceId, targetId, similarity, type` (note-to-note relationships)
  - `type`: keyword | semantic | wikilink | tag

### 3.5 API Routes (`second-brain/src/app/api/`)

| Route | Method | Purpose |
|---|---|---|
| `/api/transcribe` | POST | Cloud ASR via z-ai-web-dev-sdk (audio → text) |
| `/api/polish` | POST | LLM text cleanup/polish |
| `/api/status` | GET | Health check |
| `/api/brain/capture` | POST | Capture a thought (voice/text) → classify + tag + extract tasks |
| `/api/brain/ask` | POST | RAG — ask a question, get answer from your notes |
| `/api/brain/search` | POST | Keyword search across notes |
| `/api/brain/inbox` | GET | Get inbox notes (status="inbox") for review |
| `/api/brain/notes` | GET, POST | List/create notes |
| `/api/brain/notes/[id]` | GET, PATCH, DELETE | CRUD on single note |
| `/api/brain/tasks` | GET, PATCH | Task management |
| `/api/brain/dictation` | POST | Dictation-specific capture |
| `/api/brain/export` | POST | Export notes (markdown, etc.) |

### 3.6 Lib Modules (`second-brain/src/lib/`)

| File | Purpose |
|---|---|
| `llm.ts` | Shared LLM helpers — caches z-ai-web-dev-sdk client, enforces `system` role, timeout handling |
| `db.ts` | Prisma client singleton |
| `intent.ts` | Search intent routing (ask vs search) — heuristic-based, avoids misrouting |
| `matching.ts` | Keyword matching for note-to-note linking |
| `dates.ts` | Date utilities (task due dates, etc.) |
| `api.ts` | Client-side API fetch helpers |
| `env.ts` | Environment variable validation |
| `utils.ts` | General utilities (cn for classnames, etc.) |

### 3.7 Frontend (`second-brain/src/app/page.tsx`)

- **1,247 lines** — single-page app with:
  - Timeline of notes (filtered by type/status)
  - Search bar with intent routing (ask vs search)
  - Note detail view (edit, review, export)
  - Task sidebar (overdue/today/unscheduled)
  - Inbox review screen (Phase B)
  - Voice capture button
  - Text capture input
  - "Ask your brain" RAG interface

### 3.8 Environment (`second-brain/.env.example`)

```bash
# SQLite database location (Prisma resolves relative paths from prisma/ dir)
DATABASE_URL="file:/home/z/whisper_flow_clone/second-brain/db/second-brain.db"
```

**Note:** The `DATABASE_URL` path in `.env.example` points to `/home/z/whisper_flow_clone/...` which is a **sandbox path**. For local development on Windows, this should be updated to a Windows path like `file:C:\Users\Parth\Desktop\whisper\second-brain\db\second-brain.db`.

### 3.9 How to Run

```bash
cd second-brain
bun install
bun run db:push    # create/migrate SQLite database
bun run dev        # start Next.js dev server (port 3000)
```

### 3.10 Build Plan & Documentation

The `second-brain/` folder contains detailed planning docs:

| Doc | Purpose |
|---|---|
| `SECOND_BRAIN_BUILD_PLAN.md` | Full product spec — features, architecture, Tauri desktop plan, data flow |
| `SECOND_BRAIN_UNDERSTANDING.md` | Independent analysis of the codebase + known issues |
| `README.md` | Quick start + feature overview |

**Key architectural decisions from the build plan:**
- **Desktop framework:** Tauri v2 (planned, not built) — 3-10MB binary, native hotkeys, Python sidecar
- **Storage:** Markdown files on disk + SQLite index (Obsidian-compatible)
- **Search:** SQLite FTS5 (keyword) + sqlite-vec (semantic) — planned
- **Vault location:** `~/SecondBrain/` (user-configurable)
- **LLM tiers:** Tier 1 = Qwen2.5-0.5B (always-on classification), Tier 2 = Qwen2.5-3B (on-demand reasoning) — planned but currently using z-ai-web-dev-sdk cloud LLM

### 3.11 Current State (Phase A + B done, C partial)

**Done (Phase A — foundation):**
- Prisma schema (Note, Task, Link models)
- API routes for capture, search, ask (RAG), notes CRUD, tasks
- z-ai-web-dev-sdk integration (cloud ASR + LLM)
- Intent routing (ask vs search heuristic)
- Keyword matching for note links

**Done (Phase B — review loop):**
- Inbox status workflow (inbox → processed → archived)
- Note editing (title, body, tags, type)
- Export functionality
- Dictation-specific capture route
- Task CRUD

**Done (Phase C — UI rewrite):**
- Single-page app with timeline, search, note detail, task sidebar
- shadcn/ui component library integration
- Review screen for inbox notes

**Not yet done:**
- Tauri v2 desktop wrapper
- Markdown file storage (currently SQLite only)
- sqlite-vec semantic search (currently keyword only)
- Local LLM tiers (currently cloud z-ai-web-dev-sdk)
- Global hotkey integration (Ctrl+Shift+B) — needs Tauri
- File watcher for `~/SecondBrain/` vault

---

## 4. Git & Collaboration

### 4.1 Remote
- **URL:** `https://github.com/ParthVarekar/whisper_flow_clone_local.git`
- **Branch:** `main`
- **Auth:** PAT embedded in remote URL (under `ParthVarekar` account)
- **Contributors:** All commits are now under `Parth Varekar <parthvarekar@users.noreply.github.com>` (history was rewritten to remove `zai-code` and `ParthKCCollege` contributors)

### 4.2 Git Config
```
user.name  = Parth Varekar
user.email = parthvarekar@users.noreply.github.com
```

### 4.3 Commit Conventions
- `feat(scope):` — new feature
- `fix(scope):` — bug fix
- `fix(critical):` — critical bug fix
- `docs(scope):` — documentation
- Scopes: `phase1`, `phase2`, `ux`, `second-brain`, or module name

---

## 5. How the Two Projects Relate

**Currently:** They don't. They're separate apps in the same repo.

**Planned (from Second Brain build plan):**
The Second Brain's Tauri v2 desktop wrapper would eventually use WhisperFlow's Python ASR pipeline as a "sidecar" process. The flow would be:
1. User presses `Ctrl+Shift+B` (brain capture hotkey)
2. Tauri Rust backend detects hotkey → starts Python sidecar
3. Python sidecar uses WhisperFlow's Moonshine ASR to transcribe
4. Transcript sent to Second Brain's `/api/brain/capture` endpoint
5. Second Brain's LLM classifies + tags + extracts tasks

**This integration has NOT been built.** Both apps currently run independently.

---

## 6. Environment Details

### 6.1 Development Machine (Sandbox)
- **OS:** Linux (Ubuntu/Debian)
- **Python:** 3.12 (system) + 3.10+ (for WhisperFlow)
- **Node/Bun:** Bun runtime for Second Brain
- **Path:** `/home/z/my-project/archive/whisper_flow_clone_local/`

### 6.2 User's Machine (Windows)
- **OS:** Windows (PowerShell)
- **Path:** `C:\Users\Parth\Desktop\whisper`
- **Python venv:** `.qa-venv` (pre-existing)
- **LLM models:** `D:\llama4\llama-server.exe` + gemma-4-E4B-it GGUF (only needed if mode="auto")

### 6.3 Testing
- WhisperFlow: `python -m pytest tests/` (66 formatting tests pass)
- Second Brain: `bun run lint` + manual browser testing

---

## 7. Known Issues & Next Steps

### 7.1 WhisperFlow (owned by THIS agent)
- **User is currently testing** on Windows — may report bugs
- Phase 2 fine-tuning scripts ready but need GPU + data
- Mobile support (Phase 3) not started

### 7.2 Second Brain (to be owned by the OTHER agent)
- `DATABASE_URL` in `.env.example` has a sandbox path — needs Windows path for local dev
- Tauri v2 desktop wrapper not built
- Local LLM tiers not implemented (using cloud z-ai-web-dev-sdk)
- sqlite-vec semantic search not implemented (keyword only)
- Markdown vault storage not implemented (SQLite only)
- Global hotkey (Ctrl+Shift+B) needs Tauri

---

## 8. Quick Reference

### WhisperFlow
- **Run:** `start.bat` (from repo root)
- **Config:** `config.llama4.toml`
- **Tests:** `python -m pytest tests/`
- **Key file:** `whisper_flow/daemon.py`

### Second Brain
- **Run:** `cd second-brain && bun run dev`
- **Config:** `second-brain/.env` (copy from `.env.example`, fix `DATABASE_URL`)
- **DB:** `cd second-brain && bun run db:push`
- **Key file:** `second-brain/src/app/page.tsx` (1,247-line single-page app)
- **Build plan:** `second-brain/SECOND_BRAIN_BUILD_PLAN.md`
