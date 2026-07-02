#!/usr/bin/env bash
# One-shot setup: build both backends + download default models.
# Usage:
#   ./scripts/setup.sh
#   GPU_BACKEND=cuda ./scripts/setup.sh
set -euo pipefail

log() { printf '\033[1;34m[setup]\033[0m %s\n' "$*"; }

log "step 1/2: building whisper.cpp + llama.cpp"
"$(dirname "$0")/build.sh"

log "step 2/2: downloading default models"
"$(dirname "$0")/download_models.sh"

log "done. Run a preflight check:"
log "  export PATH=\"$(pwd)/build/whisper/bin:$(pwd)/build/llama/bin:\$PATH\""
log "  python -m whisper_flow check"
