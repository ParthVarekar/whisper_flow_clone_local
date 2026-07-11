#!/usr/bin/env python3
"""
Second Brain — Whisper Sidecar

A small HTTP server that wraps WhisperFlow's Moonshine ASR backend for use
as a Tauri sidecar. The Tauri Rust shell sends audio to this server, which
transcribes it locally (no cloud) and returns the text.

This is the bridge between the WhisperFlow Python voice pipeline and the
Second Brain Next.js app — the integration point described in the build plan.

Usage:
    python whisper-sidecar.py [--port 5001]

The server listens on 127.0.0.1:5001 by default and exposes:
    POST /transcribe  (multipart: audio=<file>)  → {"transcript": "..."}
    GET  /health                                    → {"ok": true}

It imports the Moonshine backend from whisper_flow/backends/moonshine.py.
If whisper_flow is not on the path, it falls back to faster-whisper.
"""
import argparse
import io
import json
import sys
import os
from http.server import HTTPServer, BaseHTTPRequestHandler

# Add the parent directory to the path so we can import whisper_flow
# This assumes the sidecar is run from the repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Lazy-load the ASR backend (it's ~27MB, load on first request)
_asr_backend = None

def get_asr_backend():
    global _asr_backend
    if _asr_backend is not None:
        return _asr_backend

    try:
        # Try Moonshine first (WhisperFlow's preferred backend)
        from whisper_flow.backends.moonshine import MoonshineBackend
        _asr_backend = MoonshineBackend()
        print("[sidecar] Using Moonshine ASR backend", file=sys.stderr)
    except ImportError:
        try:
            # Fall back to faster-whisper if available
            from faster_whisper import WhisperModel
            _asr_backend = WhisperModel("base", device="cpu", compute_type="int8")
            print("[sidecar] Using faster-whisper backend", file=sys.stderr)
        except ImportError:
            print("[sidecar] No ASR backend available. Install moonshine-voice or faster-whisper.", file=sys.stderr)
            raise

    return _asr_backend


def transcribe(audio_bytes: bytes, language: str = "en") -> str:
    """Transcribe audio bytes to text."""
    backend = get_asr_backend()

    # Moonshine backend
    if hasattr(backend, 'transcribe'):
        try:
            # MoonshineBackend.transcribe takes a path or bytes
            result = backend.transcribe(io.BytesIO(audio_bytes))
            # Extract text from the result
            if hasattr(result, 'text'):
                return result.text.strip()
            elif hasattr(result, 'lines'):
                return " ".join(line.text for line in result.lines).strip()
            elif isinstance(result, str):
                return result.strip()
        except Exception as e:
            print(f"[sidecar] Moonshine error: {e}", file=sys.stderr)

    # faster-whisper backend
    if hasattr(backend, 'transcribe'):
        try:
            segments, _info = backend.transcribe(io.BytesIO(audio_bytes), language=language)
            return " ".join(seg.text for seg in segments).strip()
        except Exception as e:
            print(f"[sidecar] faster-whisper error: {e}", file=sys.stderr)

    return ""


class SidecarHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != "/transcribe":
            self.send_response(404)
            self.end_headers()
            return

        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "expected multipart/form-data"}).encode())
            return

        # Parse multipart (simple parser — for production use the `cgi` or
        # `python-multipart` library, but we keep this stdlib-only to match
        # WhisperFlow's design)
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        # Extract the audio file from multipart
        audio_bytes = self._extract_audio(body, content_type)
        if not audio_bytes:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "no audio file in request"}).encode())
            return

        try:
            transcript = transcribe(audio_bytes)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"transcript": transcript}).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def _extract_audio(self, body: bytes, content_type: str) -> bytes:
        """Simple multipart parser to extract the 'audio' field."""
        # Get boundary
        boundary = None
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part[len("boundary="):].strip('"')
                break

        if not boundary:
            return b""

        boundary_bytes = ("--" + boundary).encode()
        parts = body.split(boundary_bytes)

        for part in parts:
            if b'name="audio"' not in part:
                continue
            # Split headers from body
            header_end = part.find(b"\r\n\r\n")
            if header_end == -1:
                continue
            audio_data = part[header_end + 4:]
            # Strip trailing \r\n
            if audio_data.endswith(b"\r\n"):
                audio_data = audio_data[:-2]
            return audio_data

        return b""

    def log_message(self, format, *args):
        print(f"[sidecar] {args[0]}")


def main():
    parser = argparse.ArgumentParser(description="Second Brain Whisper Sidecar")
    parser.add_argument("--port", type=int, default=5001, help="Port to listen on")
    args = parser.parse_args()

    server = HTTPServer(("127.0.0.1", args.port), SidecarHandler)
    print(f"[sidecar] Whisper sidecar listening on http://127.0.0.1:{args.port}", file=sys.stderr)
    print(f"[sidecar] POST /transcribe  — transcribe audio", file=sys.stderr)
    print(f"[sidecar] GET  /health       — health check", file=sys.stderr)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[sidecar] Shutting down...", file=sys.stderr)
        server.shutdown()


if __name__ == "__main__":
    main()
