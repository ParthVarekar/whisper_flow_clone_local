"""Minimal local HTTP server wrapping the pipeline (stdlib only).

Endpoints:
  GET  /health            -> {"ok": true, "version": ...}
  POST /transcribe        -> multipart "audio" file  -> {"transcript", "segments", ...}
  POST /process           -> multipart "audio" file + form "mode" -> {..., "processed"}
  POST /transcribe/text   -> JSON {"text": "..."} + ?mode=summarize -> {"processed"}

This is intentionally tiny: stdlib http.server, no framework. Multipart parsing
is done with a small stdlib parser (the `cgi` module is deprecated in 3.13 and
will be removed; we avoid it). For production use, put it behind a reverse
proxy and add auth/rate limiting.

Run:
  python -m whisper_flow serve --port 8090
"""

from __future__ import annotations

import json
import os
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from .config import Config
from .errors import WhisperFlowError, render_error
from .pipeline import Pipeline

# ---------------------------------------------------------------------------
# Stdlib multipart/form-data parser (replaces deprecated cgi.FieldStorage).
# Handles a single file upload + optional text fields. Sufficient for the
# /transcribe and /process endpoints; not a general-purpose multipart lib.
# ---------------------------------------------------------------------------

def _parse_multipart(body: bytes, content_type: str) -> tuple[dict, list[dict]]:
    """Parse multipart/form-data. Returns (fields, files).

    files is a list of {"name": str, "filename": str, "data": bytes}.
    fields is a dict of name -> str value.
    """
    # extract boundary
    if "boundary=" not in content_type:
        return {}, []
    boundary = content_type.split("boundary=", 1)[1].strip()
    # strip surrounding quotes if present
    if boundary.startswith('"') and boundary.endswith('"'):
        boundary = boundary[1:-1]
    delim = ("--" + boundary).encode("latin-1")
    end_delim = delim + b"--"

    parts = body.split(delim)
    fields: dict = {}
    files: list[dict] = []
    for part in parts:
        # strip leading CRLF and trailing CRLF
        part = part.strip(b"\r\n")
        if not part or part == end_delim.strip(b"\r\n"):
            continue
        # split headers from body
        if b"\r\n\r\n" in part:
            header_blob, _, data = part.partition(b"\r\n\r\n")
        else:
            continue
        # parse Content-Disposition
        headers = header_blob.decode("latin-1", "replace")
        name = ""
        filename = None
        for line in headers.split("\r\n"):
            if line.lower().startswith("content-disposition:"):
                # name="audio"; filename="x.wav"
                disp = line.split(":", 1)[1]
                for kv in disp.split(";"):
                    kv = kv.strip()
                    if kv.startswith("name="):
                        name = kv[5:].strip('"')
                    elif kv.startswith("filename="):
                        filename = kv[9:].strip('"')
        if filename is not None:
            files.append({"name": name, "filename": filename, "data": data})
        else:
            fields[name] = data.decode("utf-8", "replace")
    return fields, files


def _save_upload(fileinfo: dict, dest_dir: str) -> str:
    name = os.path.basename(fileinfo.get("filename") or "upload.wav") or "upload.wav"
    path = os.path.join(dest_dir, name)
    with open(path, "wb") as fh:
        fh.write(fileinfo["data"])
    return path


class Handler(BaseHTTPRequestHandler):
    server_version = f"whisper-flow/{__version__}"

    # quiet logging
    def log_message(self, fmt, *args):  # noqa: A003
        if self.server.verbose:  # type: ignore[attr-defined]
            BaseHTTPRequestHandler.log_message(self, fmt, *args)

    # -- helpers -------------------------------------------------------------
    def _send_json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or 0)
        return self.rfile.read(length) if length else b""

    def _parse_form(self) -> tuple[dict, list[dict]]:
        ct = self.headers.get("Content-Type", "")
        body = self._read_body()
        if "multipart/form-data" in ct:
            return _parse_multipart(body, ct)
        # fallback: application/x-www-form-urlencoded
        if "application/x-www-form-urlencoded" in ct:
            from urllib.parse import parse_qs
            qs = parse_qs(body.decode("utf-8", "replace"))
            return {k: v[0] for k, v in qs.items()}, []
        return {}, []

    # -- routes --------------------------------------------------------------
    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self._send_json(200, {"ok": True, "version": __version__})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        cfg: Config = self.server.config  # type: ignore[attr-defined]
        path = self.path.split("?", 1)[0]
        try:
            if path in ("/transcribe", "/process"):
                self._handle_audio(cfg, path)
            elif path == "/transcribe/text":
                self._handle_text(cfg)
            else:
                self._send_json(404, {"error": "not found"})
        except WhisperFlowError as exc:
            self._send_json(400, {"error": render_error(exc)})
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"error": render_error(exc)})

    # -- handlers ------------------------------------------------------------
    def _handle_audio(self, cfg: Config, path: str) -> None:
        fields, files = self._parse_form()
        if not files:
            self._send_json(400, {"error": "no 'audio' file uploaded"})
            return
        with tempfile.TemporaryDirectory(prefix="wf_srv_") as tmp:
            upath = _save_upload(files[0], tmp)
            pipe = Pipeline(cfg)
            if path == "/transcribe":
                result = pipe.run_file(upath)
                result.pop("source", None)
                self._send_json(200, result)
            else:  # /process
                mode = fields.get("mode") or cfg.mode
                if mode not in ("summarize", "correct", "polish", "command", "assistant", "raw"):
                    self._send_json(400, {"error": f"invalid mode: {mode!r}"})
                    return
                cfg.mode = mode
                result = pipe.run_file(upath)
                result.pop("source", None)
                self._send_json(200, result)

    def _handle_text(self, cfg: Config) -> None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid JSON body"})
            return
        text = str(payload.get("text", "")).strip()
        if not text:
            self._send_json(400, {"error": "missing 'text' field"})
            return
        mode = payload.get("mode") or cfg.mode
        if mode not in ("summarize", "correct", "polish", "command", "assistant", "raw"):
            self._send_json(400, {"error": f"invalid mode: {mode!r}"})
            return
        cfg.mode = mode
        try:
            processed = Pipeline(cfg).process(text)
        except WhisperFlowError as exc:
            self._send_json(400, {"error": render_error(exc)})
            return
        self._send_json(200, {"transcript": text, "mode": mode, "processed": processed})


def run_server(cfg: Config, *, host: str = "127.0.0.1", port: int = 8090,
               verbose: bool = False) -> int:
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.config = cfg  # type: ignore[attr-defined]
    httpd.verbose = verbose  # type: ignore[attr-defined]
    print(f"whisper-flow HTTP server listening on http://{host}:{port}", flush=True)
    print("  POST /transcribe        (multipart 'audio')", flush=True)
    print("  POST /process           (multipart 'audio', form 'mode')", flush=True)
    print("  POST /transcribe/text   (JSON {text, mode})", flush=True)
    print("  GET  /health", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[server stopped]", flush=True)
    finally:
        httpd.server_close()
    return 0
