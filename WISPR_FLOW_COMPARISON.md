# Wispr Flow vs WhisperFlow — Comparison & Adopted Practices

**Research date:** July 12, 2026
**Purpose:** Document what we learned from Wispr Flow and what we adopted.

---

## Wispr Flow's Key Philosophy

From their design blog: **"Streaming vs. understanding"**

> Traditional voice interfaces use ASR. They transcribe and stream real-time.
> Sure it's fast and immediate, but dumb. Words arrive as they're spoken,
> often wrong and overstimulating and jarring. More noise than signal.
>
> Flow does something different. We use LLMs to post-process your speech.
> Which means Flow can wait, understand, and then write what you MEANT.
> Not just what you said.

This is the core insight: **don't just transcribe — understand and refine.**

---

## Feature Comparison

| Feature | Wispr Flow | WhisperFlow (us) | Status |
|---|---|---|---|
| LLM post-processing | ✅ Cloud LLM | ✅ Local gemma-4 | ✅ Adopted |
| Background chunk processing | ✅ Streaming | ✅ 4s chunks during recording | ✅ Adopted |
| Filler word removal | ✅ Real-time | ✅ formatting.py + LLM | ✅ Adopted |
| Backtrack detection | ✅ "actually", "I mean" | ✅ formatting.py + LLM prompt | ✅ Adopted |
| List formatting | ✅ Auto-detect | ✅ LLM prompt with list rules | ✅ Adopted |
| Context awareness | ✅ App detection | ✅ app_detect.py + per-app styles | ✅ Adopted |
| Hold-and-speak | ✅ Hold hotkey | ✅ Ctrl+Shift+Space | ✅ Adopted |
| Progressive text reveal | ✅ Typewriter | ✅ Word-by-word with cursor | ✅ Adopted |
| Refined preview (not raw ASR) | ✅ Shows cleaned text | ✅ Apply formatting to chunks | ✅ Adopted |
| Voice commands | ✅ "new line", "delete" | ✅ LLM prompt handles these | ✅ Adopted |
| Dynamic popup sizing | ✅ Grows with content | ✅ 50% screen height max | ✅ Adopted |
| Dynamic auto-hide | ✅ Based on content | ✅ 5-30s based on word count | ✅ Adopted |
| Cloud processing | ✅ Cloud-only | ❌ Local-only (privacy) | Different approach |
| 200ms latency | ✅ Cloud GPU | ❌ ~1-2s (local GPU) | Hardware limit |
| Multi-language | ✅ 100+ languages | ❌ English-only | Future work |
| Cross-platform | ✅ Mac/Win/iOS | ⚠️ Windows-focused | Future work |
| Custom vocabulary | ✅ Personalized | ✅ Dictionary + learned vocab | ✅ Adopted |
| Per-app formatting styles | ✅ Detects app | ✅ app_styles in config | ✅ Adopted |
| Auto-start LLM server | ❌ Cloud (N/A) | ✅ start.bat auto-starts | Our addition |
| Graceful LLM fallback | ❌ Cloud-only | ✅ Falls back to raw | Our addition |
| Local privacy | ❌ Cloud processing | ✅ 100% local | Our advantage |

---

## Practices We Adopted from Wispr Flow

### 1. "Write what you meant, not just what you said"
Updated the LLM prompt to explicitly instruct the model to understand intent,
not just transcribe verbatim. The LLM now handles self-corrections, context
inference, and natural language understanding.

### 2. Refined preview during recording
Wispr Flow shows cleaned text while speaking, not raw ASR. We now apply
lightweight rule-based formatting (filler removal, capitalization, backtrack
correction) to background chunks before displaying them in the popup.

### 3. Background streaming processing
Wispr Flow processes audio in real-time during recording. We implemented
background chunk processing — 4-second chunks are transcribed during
recording, so by the time the user releases the hotkey, transcription is
already done.

### 4. Responsiveness over visible feedback
Wispr Flow prioritizes responsiveness. We adopted this by:
- Background chunk processing (0ms ASR after release)
- Typewriter reveal for progressive text appearance
- Dynamic popup that grows with content

### 5. Voice commands
Wispr Flow handles "new line", "new paragraph", "delete that". We added
these to the LLM prompt so the model interprets them as formatting commands.

### 6. Dynamic UI
Wispr Flow's popup adapts to content. We implemented:
- Dynamic height (up to 50% of screen)
- Dynamic auto-hide (5-30s based on word count)
- Progressive text reveal (typewriter effect)

---

## Our Advantages Over Wispr Flow

1. **100% Local** — No cloud, no privacy concerns, works offline
2. **Graceful degradation** — Falls back to raw transcript if LLM is down
3. **Customizable** — Open source, configurable models and prompts
4. **No subscription** — Free, uses your own hardware
5. **Transparent** — You can see and modify the entire pipeline

---

## Practices NOT Adopted (and why)

1. **Cloud processing** — We're local-first by design (privacy advantage)
2. **200ms latency** — Not possible with local 1.7B ASR + 4B LLM on 8GB GPU
3. **Multi-language** — Qwen3-ASR supports it, but we force English for now
   (user requested, prevents Hindi leakage)
4. **WebSocket streaming** — Our subprocess approach works well enough;
   WebSocket would add complexity without significant benefit for local use
