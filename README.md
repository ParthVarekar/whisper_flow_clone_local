<div align="center">
  <br />
  <h1>🌊 WhisperFlow</h1>
  <p><b>Intelligent, zero-latency voice dictation & contextual AI text transformation—100% offline.</b></p>

  [![License: MIT](https://img.shields.io/badge/License-MIT-007ACC.svg)](https://opensource.org/licenses/MIT)
  [![Platform: Windows](https://img.shields.io/badge/Platform-Windows-blue.svg)](#)
  [![Architecture: Local Dual Engine](https://img.shields.io/badge/Architecture-Qwen3%20%2B%20Gemma%202B-8A2BE2.svg)](#)
  [![Privacy: 100% On--Device](https://img.shields.io/badge/Privacy-100%25%20On--Device-success.svg)](#)

  <br />
</div>

---

**WhisperFlow** is an open-source, local-first voice productivity engine designed to bridge raw speech recognition with intelligent Large Language Model (LLM) post-processing. 

Instead of dumping unformatted speech-to-text transcripts into your application, WhisperFlow captures audio, analyzes intent, removes verbal stutters, executes spoken formatting cues, and pastes polished text directly into any focused window—**with zero cloud dependencies.**

```
┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│   Audio Input   │ ────> │  Qwen3-ASR STT  │ ────> │ Gemma-4 2B LLM  │ ────> │  Active App    │
│  (Mic / File)   │       │ (High Accuracy) │       │ (Context Polish)│       │ (Auto-Pasted)   │
└─────────────────┘       └─────────────────┘       └─────────────────┘       └─────────────────┘
```

---

## 💡 Why WhisperFlow?

Most speech-to-text utilities output raw, unpunctuated ASR streams filled with filler words (*"um"*, *"uh"*), misrecognized homophones, and run-on sentences. 

WhisperFlow fixes this by decoupling **acoustic decoding** from **semantic editing**:

| Feature | Standard Cloud STT APIs | Desktop Voice Apps | WhisperFlow |
|---|---|---|---|
| **Privacy Guarantee** | ❌ Audio sent to external servers | ⚠️ Cloud transcription required | **100% On-Device / Offline** |
| **Subscription Fees** | ❌ Monthly per-minute billing | ❌ $10–$15 / month | **Free & Open Source** |
| **Contextual Formatting** | ❌ Raw text output only | ⚠️ Basic capitalization | **LLM Structuring (Lists, Bold, Polishing)** |
| **Contextual Voice Commands** | ❌ Not supported | ⚠️ Limited pre-built rules | **Full AI In-Place Text Editing** |
| **Custom Dictionary / Snippets** | ⚠️ Expensive custom models | ⚠️ Rigid replacement rules | **Phonetic Vocab + Auto-Expanding Text** |

---

## ✨ Core Capabilities

### 1. Global Dictation (`Ctrl+Shift+Space`)
Hold the hotkey, speak naturally, and release. WhisperFlow automatically removes verbal stumbles and formats spoken structures:
- **Spoken**: *"I will list a few items bold this word that is groceries bananas and milk"*
- **Output**: 
  - **Groceries**
  - Bananas
  - Milk

### 2. Contextual Voice Commands (`Ctrl+Shift+T`)
Highlight text in any application (VS Code, Outlook, Slack, Browser), hold `Ctrl+Shift+T`, and speak an instruction:
- **Selected Text**: `"Hey team, we might delay the release by two days due to some open bugs."`
- **Spoken Command**: `"Make this sound professional and action-oriented."`
- **Replaced Result**: `"Team, we are adjusting our release schedule by 48 hours to resolve remaining critical issues."`

### 3. Real-Time UI Overlay
A lightweight, non-intrusive floating status widget gives immediate feedback during recording, normalization, and LLM processing phases without taking focus away from your work.

---

## 🚀 Quick Start (Windows)

WhisperFlow comes with an automated background service launcher.

### Prerequisites
- **OS**: Windows 10 or 11 (x64)
- **Python**: 3.10 or higher
- **Hardware**: Dedicated NVIDIA GPU recommended for sub-second LLM inference (CPU fallback supported).

### Installation & Launch

1. Clone the repository:
   ```bash
   git clone https://github.com/ParthVarekar/whisper_flow_clone_local.git
   cd whisper_flow_clone_local
   ```

2. Run the one-click launcher:
   ```text
   start.bat
   ```

The launcher will automatically verify Python dependencies, start the lightweight background `llama-server` (Gemma 2B), and initialize the WhisperFlow tray icon daemon.

---

## ⚙️ Configuration & Customization

All settings can be customized in `config.llama4.toml`:

```toml
mode = "auto"
writing_style = "default"
smart_formatting = true
dictation_hotkey = "ctrl+shift+space"
command_hotkey = "ctrl+shift+t"

# Custom vocabulary & phonetic corrections
dictionary = [
  "WhisperFlow", 
  "whisper.cpp", 
  "llama.cpp", 
  "GGUF", 
  "Qwen3-ASR (kwen three)"
]

[snippets]
"my email" = "your.email@domain.com"
"my signature" = "Best regards,\nYour Name"
```

---

## 📱 Architecture & Mobile Roadmap

WhisperFlow is built around a decoupled backend architecture (`whisper_flow/backends/base.py`). 

To explore our technical roadmap for expanding WhisperFlow to mobile devices (using zero-battery native OS speech recognition combined with on-device LLM cleanup), see our detailed architecture document:
- 📄 [Mobile Implementation Plan](docs/MOBILE_IMPLEMENTATION_PLAN.md)
- 📄 [Architecture Specification](ARCHITECTURE.md)

---

## 📄 License

WhisperFlow is released under the [MIT License](LICENSE). 

<div align="center">
  <br />
  <sub>Built for speed, precision, and privacy.</sub>
</div>
