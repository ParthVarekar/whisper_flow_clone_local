#!/usr/bin/env bash
# Download Whisper ggml models and a small GGUF LLM for whisper-flow.
#
# Usage:
#   ./scripts/download_models.sh                          # defaults
#   WHISPER_MODEL=medium ./scripts/download_models.sh     # pick a Whisper size
#   LLM_MODEL=ggml-org/gemma-3-1b-it-GGUF ./scripts/download_models.sh
#   ./scripts/download_models.sh --whisper-only
#   ./scripts/download_models.sh --llama-only
set -euo pipefail

MODELS_DIR="${MODELS_DIR:-$(pwd)/models}"
WHISPER_DIR="${WHISPER_DIR:-$(pwd)/third_party/whisper.cpp}"

# Defaults: small CPU/GPU-friendly options. Override via env vars.
WHISPER_MODEL="${WHISPER_MODEL:-base.en}"      # tiny|tiny.en|base|base.en|small|small.en|medium|large-v3|large-v3-turbo
LLM_REPO="${LLM_REPO:-ggml-org/gemma-3-1b-it-GGUF}"
LLM_FILE="${LLM_FILE:-gemma-3-1b-it-Q4_K_M.gguf}"

DL_WHISPER=1
DL_LLAMA=1
DL_VAD=0
for arg in "$@"; do
  case "$arg" in
    --whisper-only) DL_LLAMA=0; DL_VAD=0 ;;
    --llama-only)   DL_WHISPER=0; DL_VAD=0 ;;
    --vad-only)     DL_WHISPER=0; DL_LLAMA=0; DL_VAD=1 ;;
    --vad)          DL_VAD=1 ;;
    --all)          DL_WHISPER=1; DL_LLAMA=1; DL_VAD=1 ;;
  esac
done

log() { printf '\033[1;34m[models]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[models:error]\033[0m %s\n' "$*" >&2; }

require() {
  command -v "$1" >/dev/null 2>&1 || { err "missing required command: $1"; exit 1; }
}

mkdir -p "$MODELS_DIR"

# ---------------------------------------------------------------------------
# Whisper ggml .bin  (NOT GGUF — whisper.cpp uses ggml .bin)
# ---------------------------------------------------------------------------
download_whisper() {
  require curl
  if [[ ! -d "$WHISPER_DIR/.git" ]]; then
    log "cloning whisper.cpp (for its official download script)"
    mkdir -p "$(dirname "$WHISPER_DIR")"
    git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git "$WHISPER_DIR"
  fi
  local out="$MODELS_DIR/ggml-${WHISPER_MODEL}.bin"
  if [[ -f "$out" ]]; then
    log "Whisper model already present: $out"
    return
  fi
  log "downloading Whisper model: $WHISPER_MODEL -> $out"
  # Use the official download script, which writes to models/ inside whisper.cpp,
  # then move it into our MODELS_DIR.
  ( cd "$WHISPER_DIR" && bash ./models/download-ggml-model.sh "$WHISPER_MODEL" )
  mv "$WHISPER_DIR/models/ggml-${WHISPER_MODEL}.bin" "$out"
  log "Whisper model ready: $out"
}

# ---------------------------------------------------------------------------
# GGUF LLM  (from Hugging Face)
# ---------------------------------------------------------------------------
download_llama() {
  require curl
  local out="$MODELS_DIR/$(basename "$LLM_FILE")"
  if [[ -f "$out" ]]; then
    log "LLM model already present: $out"
    return
  fi
  local url="https://huggingface.co/${LLM_REPO}/resolve/main/${LLM_FILE}"
  log "downloading GGUF LLM: $LLM_REPO / $LLM_FILE"
  log "  -> $out"
  log "  url: $url"
  curl -L --fail -o "$out" "$url"
  log "LLM model ready: $out"
}

# ---------------------------------------------------------------------------
# Silero VAD ggml .bin (for whisper-cli --vad -vm)
# ---------------------------------------------------------------------------
download_vad() {
  require curl
  if [[ ! -d "$WHISPER_DIR/.git" ]]; then
    log "cloning whisper.cpp (for its official VAD download script)"
    mkdir -p "$(dirname "$WHISPER_DIR")"
    git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git "$WHISPER_DIR"
  fi
  local out="$MODELS_DIR/ggml-silero-v6.2.0.bin"
  if [[ -f "$out" ]]; then
    log "VAD model already present: $out"
    return
  fi
  log "downloading Silero VAD model -> $out"
  # Use the official whisper.cpp VAD download script
  ( cd "$WHISPER_DIR" && bash ./models/download-vad-model.sh silero-v6.2.0 )
  mv "$WHISPER_DIR/models/ggml-silero-v6.2.0.bin" "$out"
  log "VAD model ready: $out"
}

# ---------------------------------------------------------------------------
if [[ "$DL_WHISPER" == "1" ]]; then download_whisper; fi
if [[ "$DL_LLAMA" == "1" ]];   then download_llama;   fi
if [[ "$DL_VAD" == "1" ]];     then download_vad;     fi

cat <<EOF

============================================================
Models downloaded to: $MODELS_DIR

Suggested config (or use CLI flags):
{
  "transcription": { "model": "$MODELS_DIR/ggml-${WHISPER_MODEL}.bin" },
  "llm":           { "model": "$MODELS_DIR/$(basename "$LLM_FILE")" }
}

Or via CLI:
  python -m whisper_flow check \\
    --whisper-model "$MODELS_DIR/ggml-${WHISPER_MODEL}.bin" \\
    --llm-model "$MODELS_DIR/$(basename "$LLM_FILE")"
============================================================
EOF
