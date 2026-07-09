# whisper-flow Dockerfile (multi-stage, CPU-only by default)
#
# Builds whisper.cpp + llama.cpp from source in a builder stage, then copies
# the binaries into a slim runtime image with whisper-flow installed.
#
# Build:
#   docker build -t whisper-flow:latest .
# Run (HTTP server, mount models + audio):
#   docker run --rm -p 8090:8090 \
#     -v "$HOME/.whisper-flow/models:/models" \
#     -v "$PWD/audio:/audio" \
#     whisper-flow:latest serve --port 8090 --no-gui \
#       --whisper-model /models/ggml-base.en.bin \
#       --llm-model /models/gemma-3-1b-it-Q4_K_M.gguf
#
# For GPU: add a target stage with -DGGML_CUDA=1 and use nvidia/cuda base.
# This CPU image is ~1.2 GB and takes 10-30 min to build (cmake from source).

# ---------- builder stage ----------
FROM debian:stable-slim AS builder

ARG WHISPER_TAG=v1.9.1
ARG LLAMA_REF=master

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential cmake git ca-certificates curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# whisper.cpp (CPU build, with server example)
RUN git clone --depth 1 --branch ${WHISPER_TAG} \
      https://github.com/ggml-org/whisper.cpp /whisper.cpp \
    && cmake -S /whisper.cpp -B /whisper.cpp/build \
         -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_SERVER=ON \
    && cmake --build /whisper.cpp/build -j"$(nproc)" \
    && cmake --install /whisper.cpp/build --prefix /opt/whisper

# llama.cpp (CPU build, with curl for HF auto-download)
RUN git clone --depth 1 --branch ${LLAMA_REF} \
      https://github.com/ggml-org/llama.cpp /llama.cpp \
    && cmake -S /llama.cpp -B /llama.cpp/build \
         -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=ON \
    && cmake --build /llama.cpp/build -j"$(nproc)" \
    && cmake --install /llama.cpp/build --prefix /opt/llama

# ---------- runtime stage ----------
FROM debian:stable-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip ffmpeg alsa-utils libportaudio2 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/whisper /opt/whisper
COPY --from=builder /opt/llama  /opt/llama
ENV PATH="/opt/whisper/bin:/opt/llama/bin:${PATH}"

WORKDIR /app
COPY . /app/whisper-flow
RUN pip install --no-cache-dir /app/whisper-flow

# Default model locations (mount at runtime: -v $PWD/models:/models)
ENV WHISPER_FLOW_TRANSCRIPTION__MODEL=/models/ggml-base.en.bin
ENV WHISPER_FLOW_LLM__MODEL=/models/gemma-3-1b-it-Q4_K_M.gguf

EXPOSE 8090
# Headless by default (no GUI in container)
CMD ["whisper-flow", "serve", "--host", "0.0.0.0", "--port", "8090", "--no-gui"]
