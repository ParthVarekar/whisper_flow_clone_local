#!/usr/bin/env bash
# Build whisper.cpp and llama.cpp from source (official build systems).
# Usage:
#   ./scripts/build.sh                # build both, CPU + auto GPU
#   ./scripts/build.sh --whisper-only
#   ./scripts/build.sh --llama-only
#   GPU_BACKEND=cuda ./scripts/build.sh
#
# GPU_BACKEND options: cpu | cuda | vulkan | rocm | metal (metal is auto on Apple)
set -euo pipefail

GPU_BACKEND="${GPU_BACKEND:-cpu}"
WHISPER_DIR="${WHISPER_DIR:-$(pwd)/third_party/whisper.cpp}"
LLAMA_DIR="${LLAMA_DIR:-$(pwd)/third_party/llama.cpp}"
BUILD_DIR="${BUILD_DIR:-$(pwd)/build}"
MODELS_DIR="${MODELS_DIR:-$(pwd)/models}"

BUILD_WHISPER=1
BUILD_LLAMA=1
if [[ "${1:-}" == "--whisper-only" ]]; then BUILD_LLAMA=0; fi
if [[ "${1:-}" == "--llama-only" ]]; then BUILD_WHISPER=0; fi

log() { printf '\033[1;34m[build]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[build:error]\033[0m %s\n' "$*" >&2; }

require() {
  command -v "$1" >/dev/null 2>&1 || { err "missing required command: $1"; exit 1; }
}

require cmake
require gcc

mkdir -p "$BUILD_DIR" "$MODELS_DIR" "$(dirname "$WHISPER_DIR")" "$(dirname "$LLAMA_DIR")"

# ---------------------------------------------------------------------------
# Clone sources if missing
# ---------------------------------------------------------------------------
clone_whisper() {
  if [[ ! -d "$WHISPER_DIR/.git" ]]; then
    log "cloning whisper.cpp -> $WHISPER_DIR"
    git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git "$WHISPER_DIR"
  else
    log "whisper.cpp already present at $WHISPER_DIR (pulling latest)"
    git -C "$WHISPER_DIR" pull --ff-only || true
  fi
}

clone_llama() {
  if [[ ! -d "$LLAMA_DIR/.git" ]]; then
    log "cloning llama.cpp -> $LLAMA_DIR"
    git clone --depth 1 https://github.com/ggml-org/llama.cpp.git "$LLAMA_DIR"
  else
    log "llama.cpp already present at $LLAMA_DIR (pulling latest)"
    git -C "$LLAMA_DIR" pull --ff-only || true
  fi
}

# ---------------------------------------------------------------------------
# whisper.cpp
# ---------------------------------------------------------------------------
build_whisper() {
  clone_whisper
  local bdir="$BUILD_DIR/whisper"
  log "configuring whisper.cpp (GPU_BACKEND=$GPU_BACKEND)"
  local cmake_args=(-B "$bdir" -S "$WHISPER_DIR" -DWHISPER_BUILD_TESTS=OFF -DBUILD_SHARED_LIBS=OFF)
  case "$GPU_BACKEND" in
    cuda)   cmake_args+=(-DGGML_CUDA=ON) ;;
    vulkan) cmake_args+=(-DGGML_VULKAN=ON) ;;
    rocm)   cmake_args+=(-DGGML_HIP=ON) ;;
    metal)  cmake_args+=(-DWHISPER_COREML=ON) ;; # Metal is auto on Apple Silicon
    cpu)    ;;
    *) err "unknown GPU_BACKEND=$GPU_BACKEND"; exit 1 ;;
  esac
  cmake "${cmake_args[@]}"
  log "building whisper.cpp"
  cmake --build "$bdir" -j --config Release
  log "whisper.cpp built. Binary: $bdir/bin/whisper-cli"
}

# ---------------------------------------------------------------------------
# llama.cpp
# ---------------------------------------------------------------------------
build_llama() {
  clone_llama
  local bdir="$BUILD_DIR/llama"
  log "configuring llama.cpp (GPU_BACKEND=$GPU_BACKEND)"
  local cmake_args=(-B "$bdir" -S "$LLAMA_DIR" -DLLAMA_BUILD_TESTS=OFF)
  case "$GPU_BACKEND" in
    cuda)   cmake_args+=(-DGGML_CUDA=ON) ;;
    vulkan) cmake_args+=(-DGGML_VULKAN=ON) ;;
    rocm)   cmake_args+=(-DGGML_HIP=ON) ;;
    metal)  ;; # Metal is automatic on Apple Silicon in llama.cpp
    cpu)    ;;
    *) err "unknown GPU_BACKEND=$GPU_BACKEND"; exit 1 ;;
  esac
  cmake "${cmake_args[@]}"
  log "building llama.cpp"
  cmake --build "$bdir" -j --config Release
  log "llama.cpp built. Binaries: $bdir/bin/llama-server, $bdir/bin/llama-cli"
}

# ---------------------------------------------------------------------------
log "whisper-flow build (GPU_BACKEND=$GPU_BACKEND)"
if [[ "$BUILD_WHISPER" == "1" ]]; then build_whisper; fi
if [[ "$BUILD_LLAMA" == "1" ]];   then build_llama;   fi

cat <<EOF

============================================================
Build complete.

Add the binaries to your PATH (or point config at them):

  export PATH="$BUILD_DIR/whisper/bin:$BUILD_DIR/llama/bin:\$PATH"

Verify:
  whisper-cli --version
  llama-server --version
  llama-cli --version

Next: download models with:
  ./scripts/download_models.sh
============================================================
EOF
