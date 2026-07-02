"""llama.cpp LLM backend.

Two modes (both go through llama.cpp, both fully local):

  * "server" (recommended, default): HTTP POST to a running `llama-server`
    instance's OpenAI-compatible /v1/chat/completions endpoint. Robust parsing,
    no stdout scraping, easy to swap models without restarting the orchestrator.

  * "cli" (fallback): subprocess `llama-cli -m model.gguf ...`. Best-effort;
    flag names vary slightly across llama.cpp builds, so server mode is
    strongly preferred.

See RESEARCH.md for why this is the correct LLM stage (llama.cpp has no native
Whisper transcription; it IS the right choice for the text LLM stage).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request

from ..config import LLMConfig
from ..errors import BinaryNotFoundError, LLMError, ModelNotFoundError
from .base import LLMBackend


class LlamaCppBackend(LLMBackend):
    name = "llama.cpp"

    def __init__(self, cfg: LLMConfig, *, verbose: bool = False):
        self.cfg = cfg
        self.verbose = verbose

    # -- checks --------------------------------------------------------------

    def check(self) -> None:
        if self.cfg.mode == "server":
            self._check_server_reachable()
        elif self.cfg.mode == "cli":
            self._check_cli()
        else:
            raise LLMError(f"unknown llm.mode: {self.cfg.mode!r} (expected 'server' or 'cli')")

    def _check_cli(self) -> None:
        if shutil.which(self.cfg.llama_cli_bin) is None:
            raise BinaryNotFoundError(
                self.cfg.llama_cli_bin,
                "build llama.cpp (scripts/build.sh) and add build/bin to PATH, "
                "or set llm.llama_cli_bin in config",
            )
        if not self.cfg.model or not os.path.isfile(self.cfg.model):
            raise ModelNotFoundError(self.cfg.model, kind="GGUF LLM model")

    def _check_server_reachable(self) -> None:
        url = self._base_url() + "/health"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status >= 500:
                    raise LLMError(f"llama-server unhealthy (status {resp.status}) at {url}")
        except urllib.error.URLError as exc:
            raise LLMError(
                f"cannot reach llama-server at {self._base_url()} ({exc.reason}). "
                f"Start it with:\n"
                f"  {self.cfg.llama_server_bin} -m <model.gguf> --host {self.cfg.host} "
                f"--port {self.cfg.port} -c {self.cfg.n_ctx}"
            ) from exc

    # -- public API ----------------------------------------------------------

    def process(self, prompt: str, *, system: str = "", max_tokens: int = 512,
                temperature: float = 0.3) -> str:
        self.check()
        if self.cfg.mode == "server":
            return self._process_server(prompt, system, max_tokens, temperature)
        return self._process_cli(prompt, system, max_tokens, temperature)

    # -- server mode ---------------------------------------------------------

    def _base_url(self) -> str:
        return f"http://{self.cfg.host}:{self.cfg.port}"

    def _process_server(self, prompt: str, system: str, max_tokens: int,
                        temperature: float) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body = {
            "model": os.path.basename(self.cfg.model) or "local",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": self.cfg.top_p,
            "stream": False,
        }
        url = self._base_url() + "/v1/chat/completions"
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        self._log(f"POST {url} (prompt {len(prompt)} chars, max_tokens={max_tokens})")
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace") if hasattr(exc, "read") else ""
            raise LLMError(f"llama-server HTTP {exc.code}: {detail.strip()[:500]}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"llama-server request failed: {exc.reason}") from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMError(f"llama-server returned non-JSON: {raw[:200]}") from exc

        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected llama-server response: {raw[:500]}") from exc

        return str(content).strip()

    # -- cli mode (best-effort fallback) -------------------------------------

    def _process_cli(self, prompt: str, system: str, max_tokens: int,
                     temperature: float) -> str:
        full_prompt = (system + "\n\n" + prompt) if system else prompt
        cmd = [
            self.cfg.llama_cli_bin,
            "-m", self.cfg.model,
            "-p", full_prompt,
            "-n", str(max_tokens),
            "--temp", str(temperature),
            "--top-p", str(self.cfg.top_p),
            "-t", str(self.cfg.threads),
            "-no-cnv",              # non-conversation / completion mode
            "--no-display-prompt",  # don't echo the prompt
        ]
        if self.cfg.gpu_layers:
            cmd += ["-ngl", str(self.cfg.gpu_layers)]
        if self.cfg.mmproj:
            cmd += ["--mmproj", self.cfg.mmproj]

        self._log(f"running: {' '.join(cmd[:1])} ... -p <prompt> ({len(full_prompt)} chars)")
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except FileNotFoundError as exc:
            raise BinaryNotFoundError(self.cfg.llama_cli_bin, str(exc)) from exc

        stdout = proc.stdout.decode("utf-8", "replace")
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", "replace")
            # Common cause: older llama.cpp without -no-cnv / --no-display-prompt.
            hint = ""
            if "unknown" in stderr.lower() and "flag" in stderr.lower():
                hint = (
                    "\n  hint: your llama.cpp build may not support -no-cnv / "
                    "--no-display-prompt. Prefer llm.mode='server'."
                )
            raise LLMError(
                f"llama-cli exited with code {proc.returncode}\n{stderr.strip()}{hint}"
            )

        # With -no-cnv + --no-display-prompt, stdout is the generated text.
        text = stdout.strip()
        if not text:
            # Fallback: if the build echoed the prompt, strip a known prefix.
            if full_prompt and stdout.startswith(full_prompt):
                text = stdout[len(full_prompt):].strip()
        return text

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[llama.cpp:{self.cfg.mode}] {msg}", flush=True)
