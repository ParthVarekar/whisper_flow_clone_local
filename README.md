<div align="center">
  <h1>🌊 WhisperFlow</h1>
  <p><b>The ultimate privacy-first, offline voice dictation & AI assistant.</b></p>
  
  [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
  [![Platform: Windows](https://img.shields.io/badge/Platform-Windows-lightgray.svg)](#)
  [![100% Offline](https://img.shields.io/badge/Privacy-100%25_Offline-success.svg)](#)
</div>

---

**WhisperFlow** is a blazingly fast, entirely local voice-to-text pipeline that doesn't just transcribe your voice—it understands, formats, and cleans it up using cutting-edge Large Language Models (LLMs). 

Unlike cloud-based services, WhisperFlow runs **100% on your machine**. No subscriptions, no internet connection required, and zero data leaves your device. 

## ✨ Key Features

- 🚀 **Blazingly Fast ASR**: Powered by **Qwen3-ASR**, capturing every nuance of your voice with remarkable accuracy.
- 🧠 **Smart LLM Post-Processing**: Uses **Gemma 2B (E2B)** to seamlessly correct grammar, fix homophones, and automatically format your text (e.g., turning spoken lists into markdown lists).
- 🎙️ **Global Hotkeys**: Dictate anywhere, in any application. Hold `Ctrl+Shift+Space` to talk, release to transcribe.
- 🪄 **Voice Commands**: Select text in any app, hold `Ctrl+Shift+T`, and speak a command (e.g., *"Make this sound more professional"*, *"Translate this to Spanish"*).
- 🖥️ **Beautiful UI Overlay**: A sleek, non-intrusive floating widget gives you real-time feedback on recording status, audio normalization, and transcription progress.
- 🔒 **Absolute Privacy**: Zero cloud dependency. No data collection.

---

## ⚡ How It Works

WhisperFlow orchestrates a highly optimized two-step pipeline locally:

1. **Speech-to-Text (STT)**: The moment you release the hotkey, the audio is processed by the Qwen3-ASR engine.
2. **LLM Cleanup**: The raw transcript is passed to a lightweight, highly-capable LLM (Gemma-4 E2B via `llama-server`) which acts as an intelligent editor, applying context-aware corrections and formatting.

## 🛠️ Getting Started (Windows)

We've designed WhisperFlow to be as effortless as possible.

### Prerequisites
- Windows 10/11
- Python 3.10+
- An NVIDIA GPU is recommended for optimal performance.

### One-Click Launch

Everything is bundled in a seamless launcher. Simply double-click:

```text
start.bat
```

**What `start.bat` does automatically:**
- Verifies your Python environment and installs required standard dependencies (`sounddevice`, `pynput`, `pystray`).
- Checks for the Qwen3-ASR backend and Gemma 2B LLM files.
- Quietly boots up the `llama-server` in the background for ultra-fast LLM inference.
- Launches the WhisperFlow daemon and binds your global hotkeys.

A tray icon will appear in your Windows taskbar when the daemon is running. 

## 🎮 Usage

### 1. Global Dictation
Focus any text input field (Notepad, Word, your web browser).
- **Press and Hold** `Ctrl+Shift+Space`
- **Speak** naturally. 
- **Release** the keys.

WhisperFlow will instantly process your speech, clean it up with the LLM, and automatically paste the perfectly formatted text directly into your active window.

### 2. Contextual Voice Commands
Need to rewrite an email or summarize a paragraph?
- **Select** the text in your application.
- **Press and Hold** `Ctrl+Shift+T`
- **Speak** your instruction (e.g., *"Summarize this in three bullet points"*).
- **Release**. The selected text will be replaced with the AI-generated result.

## ⚙️ Configuration

WhisperFlow is highly customizable. Edit `config.llama4.toml` to tweak:
- **Writing Styles**: Choose between casual, formal, or default tones.
- **Hotkeys**: Rebind the default shortcuts to fit your workflow.
- **Snippets & Dictionary**: Add custom phonetic rules or auto-expanding text snippets (e.g., mapping `"my email"` to `your.name@example.com`).

---

<div align="center">
  <i>Built for those who value speed, accuracy, and absolute privacy.</i>
</div>
