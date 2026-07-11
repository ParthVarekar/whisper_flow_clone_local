# Second Brain

A local-first desktop app that captures your spoken thoughts, auto-organizes them with AI, and proactively surfaces relevant knowledge — a spare brain that remembers what you forget.

## Features

- **Voice capture** — speak a thought, AI transcribes + classifies + tags it automatically
- **Text capture** — type a thought, same AI classification
- **Auto-classification** — every note is classified as idea / task / reference / question / journal
- **Auto-tagging** — 1-5 topic tags extracted by LLM
- **Task extraction** — "remind me to draft the spec by Friday" → task with due date
- **Ask your brain** — ask questions in natural language, get answers from your notes (RAG)
- **Search** — keyword search across all notes
- **Task management** — extracted tasks in sidebar, grouped by overdue/today/unscheduled
- **Related notes** — auto-links new notes to existing ones by keyword overlap

## Tech Stack

- **Next.js 16** + React 19 + TypeScript
- **Prisma** + SQLite (Note, Task, Link models)
- **z-ai-web-dev-sdk** — cloud ASR (transcription) + cloud LLM (classification + RAG)
- **Tailwind CSS 4** + shadcn/ui
- **Tauri v2** (planned) — desktop wrapper with global hotkeys + Python sidecar

## Architecture

```
User speaks → /api/transcribe (z-ai ASR) → transcript
            → /api/brain/capture (z-ai LLM) → classify + tag + extract tasks
            → SQLite (Prisma) → stored as Note + Tasks
            → keyword matching → auto-linked to related notes

User asks → /api/brain/ask (z-ai LLM + RAG) → answer from notes + tasks
```

## Getting Started

```bash
bun install
bun run db:push
bun run dev
```

Open http://localhost:3000

## Future Roadmap

See [SECOND_BRAIN_BUILD_PLAN.md](SECOND_BRAIN_BUILD_PLAN.md) for the full plan including:
- Tauri desktop shell with global hotkeys (Ctrl+Shift+B)
- Local Whisper + Qwen models (offline mode)
- sqlite-vec for semantic vector search
- Markdown vault (~/SecondBrain/) — Obsidian compatible
- Proactive surfacing notifications
- Wispr Flow integration (dictation → brain logging)
