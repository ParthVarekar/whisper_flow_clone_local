"""Background daemon that ties everything together into a Wispr Flow experience.

Responsibilities:
  - System tray icon (always running)
  - Global hotkey listener (push-to-talk + command mode)
  - Floating overlay indicator (shows during recording/processing)
  - Push-to-talk dictation flow: hotkey hold → record → transcribe → LLM → insert at cursor
  - Command mode flow: hotkey → read selected text → record instruction → LLM transform → replace
  - Per-app style detection
  - Snippet expansion
  - Dictation history logging

Usage:
    python -m whisper_flow daemon --config config.llama4.toml
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Optional

from .config import Config
from .pipeline import Pipeline
from .overlay import OverlayNotifier
from .hotkeys import HotkeyManager
from .tray import TrayIcon
from .inserter import insert_text, get_selected_text
from .app_detect import detect_app_category, get_active_window_info
from .snippets import expand_snippets
from .transforms import build_transform_prompt
from .history import save_dictation
from .audio import LiveMicCapture
from .formatting import apply_smart_formatting
from .prompts import resolve_mode, build_prompt


def _clean_llm_output(text: str) -> str:
    """Strip common LLM echo artifacts: 'Transcript:' prefix, wrapping quotes."""
    import re
    t = text.strip()
    # Remove leading 'Transcript:' line
    t = re.sub(r'^(?:Transcript|Output|Result)\s*:\s*\n?', '', t, flags=re.IGNORECASE).strip()
    # Remove wrapping triple-quotes
    if t.startswith('"""') and t.endswith('"""'):
        t = t[3:-3].strip()
    # Remove wrapping double-quotes
    elif t.startswith('"') and t.endswith('"') and t.count('"') == 2:
        t = t[1:-1].strip()
    return t


class Daemon:
    """Main daemon class that orchestrates the Wispr Flow-like experience."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._overlay = OverlayNotifier(opacity=0.85)
        # Sync the overlay's initial mode with the config mode.
        # Without this, the overlay defaults to "auto" which would trigger
        # LLM cleanup even when the config says mode="raw".
        self._overlay.set_mode(cfg.mode)
        self._overlay.set_writing_style(cfg.writing_style)
        self._pipeline = Pipeline(cfg, notifier=self._overlay)
        self._tray: Optional[TrayIcon] = None
        self._hotkeys: Optional[HotkeyManager] = None

        # State
        self._recording = False
        self._capture: Optional[LiveMicCapture] = None
        self._record_start_time: float = 0.0
        self._hands_free = False
        self._running = False
        self._lock = threading.Lock()
        self._preview_active = False  # controls live preview loop
        self._preview_accumulated = ""  # accumulated preview transcript

        # Background chunk processing state
        self._chunk_queue = []  # list of (wav_path, timestamp) for chunks to transcribe
        self._chunk_transcribed = ""  # accumulated transcribed text from background chunks
        self._preview_active = False  # controls live preview loop
        self._last_chunk_time = 0.0
        self._chunk_duration = 4.0  # seconds of audio per chunk

        # Per-app style cache
        self._app_styles = getattr(cfg, "app_styles", {})
        self._snippets = getattr(cfg, "snippets", {})
        base_dict = list(getattr(cfg, "dictionary", []))
        from .vocabulary import load_learned_vocabulary
        learned_words = load_learned_vocabulary()
        # Merge preserving order and uniqueness
        self._dictionary = base_dict + [w for w in learned_words if w not in base_dict]

    def _stream_text_to_popup(self, text: str, words_per_chunk: int = 3, delay: float = 0.04) -> None:
        """Stream text word-by-word to the popup overlay.

        This makes the text appear progressively (typewriter effect) so the
        user sees it being "typed" rather than appearing as a sudden block.
        Used for both raw transcription reveal and LLM polishing reveal.
        """
        if not text:
            return
        words = text.split()
        for i in range(0, len(words), words_per_chunk):
            partial = " ".join(words[:i + words_per_chunk])
            if i + words_per_chunk < len(words):
                partial += " ▌"  # cursor block
            self._overlay.preview(partial)
            time.sleep(delay)

    def run(self) -> None:
        """Start the daemon (blocking). Call from main thread."""
        self._running = True
        sys.stderr.write(
            "[whisper-flow] daemon starting...\n"
            "  Dictation: Ctrl+Shift+Space (hold to record, release to insert)\n"
            "  Transform: Ctrl+Shift+T (select text first, then hold + speak instruction)\n"
            "  Hands-free: double-tap Ctrl+Shift+Space\n"
            "  Quit: right-click tray icon → Quit\n"
        )

        # Start overlay UI thread
        self._overlay.start_ui_thread()

        # Start system tray
        self._tray = TrayIcon(
            on_quit=self._on_quit,
            on_mode_change=self._on_mode_change,
            on_style_change=self._on_style_change,
            current_mode=self.cfg.mode,
            current_style=self.cfg.writing_style,
        )
        self._tray.start()

        # Start hotkey listener
        dictation_hotkey = getattr(self.cfg, "dictation_hotkey", "ctrl+shift+space")
        command_hotkey = getattr(self.cfg, "command_hotkey", "ctrl+shift+t")

        self._hotkeys = HotkeyManager(
            dictation_hotkey=dictation_hotkey,
            command_hotkey=command_hotkey,
            on_dictation_start=self._on_dictation_start,
            on_dictation_stop=self._on_dictation_stop,
            on_hands_free_toggle=self._on_hands_free_toggle,
            on_command_start=self._on_command_start,
            on_command_stop=self._on_command_stop,
        )
        self._hotkeys.start()

        sys.stderr.write("[whisper-flow] daemon ready. Listening for hotkeys.\n")

        # Block on the hotkey listener (runs until quit)
        try:
            self._hotkeys.join()
        except KeyboardInterrupt:
            pass
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        self._running = False
        if self._capture is not None:
            self._capture.close()
        if self._hotkeys is not None:
            self._hotkeys.stop()
        if self._tray is not None:
            self._tray.stop()
        self._overlay.stop_ui_thread()
        sys.stderr.write("[whisper-flow] daemon stopped.\n")

    # -- Tray callbacks ------------------------------------------------------

    def _on_quit(self) -> None:
        self._running = False
        if self._hotkeys is not None:
            self._hotkeys.stop()

    def _on_mode_change(self, mode: str) -> None:
        self.cfg.mode = mode
        self._overlay.set_mode(mode)
        sys.stderr.write(f"[whisper-flow] cleanup level: {mode}\n")

    def _on_style_change(self, style: str) -> None:
        self.cfg.writing_style = style
        self._overlay.set_writing_style(style)
        sys.stderr.write(f"[whisper-flow] writing style: {style}\n")

    # -- Dictation (push-to-talk) --------------------------------------------

    def _on_dictation_start(self) -> None:
        with self._lock:
            if self._recording:
                return
            self._recording = True
            self._record_start_time = time.monotonic()

        if sys.platform == "win32":
            import ctypes
            self._target_hwnd = ctypes.windll.user32.GetForegroundWindow()

        sys.stderr.write("[whisper-flow] recording started\n")
        self._overlay.show("Listening...")
        if self._tray:
            self._tray.set_state("recording")
            self._tray.update_tooltip("whisper-flow (recording)")

        # Detect active app before we start (so we know formatting context)
        proc_name, _ = get_active_window_info()
        app_cat = detect_app_category()

        # Apply per-app style if configured
        if app_cat in self._app_styles:
            style_cfg = self._app_styles[app_cat]
            if "mode" in style_cfg:
                self._overlay.set_mode(style_cfg["mode"])
            if "writing_style" in style_cfg:
                self._overlay.set_writing_style(style_cfg["writing_style"])

        # Start mic capture
        # Use a larger rolling window (30s) so we can extract chunks for
        # background processing. The full audio is also captured via snapshot_full().
        try:
            self._capture = LiveMicCapture(
                self.cfg.audio,
                max_window_seconds=30,  # large window so we can extract chunks
                on_amplitude=self._overlay.amplitude,
                verbose=self.cfg.verbose,
            )
            self._capture.start()

            # Reset preview state
            with self._lock:
                self._chunk_transcribed = ""
                # (legacy flag, kept for compatibility)
                self._last_chunk_time = time.monotonic()
                self._preview_accumulated = ""

            # Start a lightweight preview loop — transcribes short rolling windows
            # every 3s for live popup display only. The final transcript always
            # comes from a full-audio transcription after recording stops.
            # Uses a SEPARATE flag (_preview_active) that is NOT unset by the
            # preview loop itself, preventing the race condition where the loop
            # unsets the block flag set by _on_dictation_stop.
            self._preview_active = True
            threading.Thread(
                target=self._live_preview_loop, daemon=True
            ).start()
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[whisper-flow] mic error: {exc}\n")
            self._overlay.error(str(exc))
            with self._lock:
                self._recording = False

    def _live_preview_loop(self) -> None:
        """Lightweight live preview — shows text in popup during recording.

        Transcribes a SHORT rolling window (last ~6s) every 3 seconds for
        live popup display only. The final transcript ALWAYS comes from a
        full-audio transcription after recording stops.

        Uses _preview_active flag (set in _on_dictation_start, cleared in
        _on_dictation_stop) to cleanly stop the loop without race conditions.

        This is NOT used for the final output — purely for user feedback
        during recording. The preview text is displayed but never inserted.
        """
        poll_interval = 3.0
        biasing_words = list(self._dictionary) if self._dictionary else []
        initial_p = ", ".join(biasing_words)

        while True:
            time.sleep(0.5)
            if not self._preview_active:
                break
            if not self._recording:
                break

            capture = self._capture
            if capture is None:
                break

            now = time.monotonic()
            elapsed_since_last = now - self._last_chunk_time
            total_dur = capture.total_duration_sec

            if elapsed_since_last < poll_interval or total_dur < 2.0:
                continue

            if not self._preview_active:
                break

            self._last_chunk_time = now

            try:
                wav_path, _, _ = capture.snapshot_window()
                if not self._preview_active:
                    if wav_path and os.path.exists(wav_path):
                        os.remove(wav_path)
                    break

                res = self._pipeline.stt.transcribe(
                    wav_path,
                    language=self.cfg.transcription.language,
                    initial_prompt=initial_p,
                )

                if wav_path and os.path.exists(wav_path):
                    os.remove(wav_path)

                preview_text = res.text.strip()
                hallucinations = {"[BLANK_AUDIO]", "Thank you for watching", "Thanks for watching.", "Subscribe to my channel", "..."}
                if not preview_text or preview_text in hallucinations:
                    continue

                self._overlay.on_stream_preview(preview_text)
            except Exception:  # noqa: BLE001
                pass
    def _on_dictation_stop(self) -> None:
        with self._lock:
            if not self._recording:
                return
            self._recording = False
            self._preview_active = False  # stop the preview loop cleanly
            # Preview loop is stopped via _preview_active = False above

        duration = time.monotonic() - self._record_start_time
        sys.stderr.write(f"[whisper-flow] recording stopped ({duration:.1f}s)\n")

        self._overlay.show_processing()
        if self._tray:
            self._tray.set_state("processing")
            self._tray.update_tooltip("whisper-flow (processing)")

        # Get active app info for history and context
        proc_name, title = get_active_window_info()
        app_cat = detect_app_category()

        # Process in background thread so hotkey listener isn't blocked
        threading.Thread(
            target=self._process_dictation,
            args=(duration, proc_name, app_cat, title),
            daemon=True,
        ).start()

    def _process_dictation(self, duration: float, app_name: str, app_cat: str, window_title: str = "") -> None:
        """Transcribe, format, cleanup, and insert text.

        ALWAYS transcribes the full audio after recording stops. The background
        chunk processor is only for live preview during recording — its results
        are NOT used for the final transcript. This ensures accuracy: the full
        audio gives the model complete sentence context.
        """
        t_total_start = time.monotonic()
        capture = self._capture
        if capture is None:
            self._overlay.hide()
            return

        try:
            # Stop capture
            capture.stop()
            time.sleep(0.1)

            if capture.total_duration_sec < 0.3:
                sys.stderr.write("[whisper-flow] recording too short, ignoring\n")
                self._overlay.hide()
                capture.close()
                self._capture = None
                if self._tray:
                    self._tray.set_state("idle")
                return

            # Get full audio snapshot
            wav_path, total_dur, _ = capture.snapshot_full()
            capture.close()
            self._capture = None

            # Construct acoustic biasing prompt
            biasing_words = list(self._dictionary) if self._dictionary else []
            if app_name and app_name.strip():
                biasing_words.append(app_name.strip())
            if window_title and window_title.strip():
                title_words = [w.strip() for w in window_title.replace("-", " ").replace("—", " ").split() if len(w.strip()) > 2]
                biasing_words.extend(title_words[:8])
            initial_p = ", ".join(biasing_words)

            # ALWAYS transcribe the full audio — never trust background chunks
            # for the final transcript. Background chunks are for preview only.
            t_stt_start = time.monotonic()
            result = self._pipeline.stt.transcribe(
                wav_path,
                language=self.cfg.transcription.language,
                initial_prompt=initial_p,
            )
            stt_ms = (time.monotonic() - t_stt_start) * 1000
            transcript = result.text.strip()

            if wav_path and os.path.exists(wav_path):
                os.remove(wav_path)

            sys.stderr.write(f"[whisper-flow] ASR: {stt_ms:.0f}ms\n")

            if not transcript:
                sys.stderr.write(
                    f"[whisper-flow] empty transcript, ignoring "
                    f"(audio: {total_dur:.1f}s, ASR took {stt_ms:.0f}ms)\n"
                )
                self._overlay.hide()
                if self._tray:
                    self._tray.set_state("idle")
                return

            sys.stderr.write(f"[whisper-flow] transcript:\n{transcript}\n")

            # Stream the raw transcript word-by-word to the popup so the user
            # sees it appear progressively (typewriter effect).
            self._overlay.show("Transcribed ✏️ Polishing...")
            self._stream_text_to_popup(transcript, words_per_chunk=3, delay=0.035)

            # Brief pause so user can read the raw transcript before polishing
            time.sleep(0.4)

            # Apply smart formatting
            if self.cfg.smart_formatting:
                transcript = apply_smart_formatting(
                    transcript,
                    writing_style=self._overlay.get_selected_writing_style(),
                )

            # Expand snippets
            if self._snippets:
                transcript = expand_snippets(transcript, self._snippets)

            # LLM cleanup & auto-intent routing
            raw_selected_mode = self._overlay.get_selected_mode()
            mode = resolve_mode(raw_selected_mode)
            if mode in ("auto", "mind_reader"):
                from .intents import detect_auto_intent
                mode = detect_auto_intent(transcript, app_category=app_cat, app_name=app_name)
                sys.stderr.write(f"[whisper-flow] auto-detected intent mode: {mode}\n")

            if mode != "raw":
                enriched_ctx = f"{app_name} ({window_title})" if window_title else app_name
                system, user = build_prompt(
                    mode, transcript,
                    context_words=self._dictionary,
                    app_context=enriched_ctx,
                )
                try:
                    t_llm_start = time.monotonic()
                    # Update popup status to show we're polishing
                    self._overlay.show("Polishing ✨...")
                    processed = self._pipeline.llm.process(
                        user,
                        system=system,
                        max_tokens=self.cfg.llm.max_tokens,
                        temperature=self.cfg.llm.temperature,
                    )
                    llm_ms = (time.monotonic() - t_llm_start) * 1000
                    sys.stderr.write(f"[whisper-flow] LLM: {llm_ms:.0f}ms\n")
                except Exception as llm_exc:  # noqa: BLE001
                    # LLM server unreachable / errored — fall back to the raw
                    # rule-formatted transcript so dictation still works.
                    # This keeps the pipeline functional even when llama-server
                    # is not running (e.g. mode="auto" but no LLM started).
                    sys.stderr.write(
                        f"[whisper-flow] LLM cleanup failed ({llm_exc}); "
                        f"falling back to raw transcript.\n"
                    )
                    processed = transcript
            else:
                processed = transcript

            sys.stderr.write(f"[whisper-flow] output:\n{processed}\n")

            # Strip LLM echo artifacts (Transcript: prefix, wrapping quotes)
            processed = _clean_llm_output(processed)

            # Insert at cursor
            insert_text(processed, target_hwnd=getattr(self, "_target_hwnd", 0))

            # Total processing time (from hotkey release to text insertion)
            total_ms = (time.monotonic() - t_total_start) * 1000
            sys.stderr.write(f"[whisper-flow] total: {total_ms:.0f}ms (ASR + format + LLM + insert)\n")

            # Display result in floating overlay HUD
            self._overlay.show_result(processed)

            # Save to history & update dynamic learned vocabulary
            try:
                save_dictation(
                    transcript=transcript,
                    processed=processed,
                    mode=mode,
                    writing_style=self._overlay.get_selected_writing_style(),
                    app_name=app_name,
                    app_category=app_cat,
                    duration_sec=duration,
                )
                from .vocabulary import update_learned_vocabulary
                updated_vocab = update_learned_vocabulary(processed)
                if updated_vocab:
                    for w in updated_vocab:
                        if w not in self._dictionary:
                            self._dictionary.append(w)
            except Exception:  # noqa: BLE001
                pass

        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[whisper-flow] error: {exc}\n")
            self._overlay.error(str(exc))
        finally:
            if self._tray:
                self._tray.set_state("idle")
                self._tray.update_tooltip("whisper-flow (idle) — Ctrl+Shift+Space to dictate")

    # -- Hands-free toggle ---------------------------------------------------

    def _on_hands_free_toggle(self) -> None:
        self._hands_free = not self._hands_free
        if self._hands_free:
            sys.stderr.write("[whisper-flow] hands-free mode ON\n")
            self._on_dictation_start()
        else:
            sys.stderr.write("[whisper-flow] hands-free mode OFF\n")
            self._on_dictation_stop()

    # -- Command mode (transforms) -------------------------------------------

    def _on_command_start(self) -> None:
        """Command mode hotkey pressed: copy selected text and start recording instruction."""
        with self._lock:
            if self._recording:
                return
            self._recording = True
            self._record_start_time = time.monotonic()

        # Grab selected text via Ctrl+C copy
        try:
            # C2 FIX: inserter.py defines get_selected_text, not copy_selected_text
            from .inserter import get_selected_text as copy_selected_text
            self._command_selected_text = copy_selected_text()
        except Exception:  # noqa: BLE001
            self._command_selected_text = ""

        sys.stderr.write(f"[whisper-flow] command recording started (selected {len(self._command_selected_text)} chars)\n")
        self._overlay.show("Command listening...")
        if self._tray:
            self._tray.set_state("recording")

        # Start mic capture for the instruction
        try:
            self._capture = LiveMicCapture(
                self.cfg.audio,
                max_window_seconds=max(
                    self.cfg.audio.stream_max_s,
                    self.cfg.audio.stream_chunk_s * 4,
                ),
                on_amplitude=self._overlay.amplitude,
                verbose=self.cfg.verbose,
            )
            self._capture.start()
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[whisper-flow] mic error: {exc}\n")
            self._overlay.error(str(exc))
            with self._lock:
                self._recording = False

    def _on_command_stop(self) -> None:
        """Command mode hotkey released: transcribe instruction, apply transform."""
        with self._lock:
            if not self._recording:
                return
            self._recording = False

        duration = time.monotonic() - self._record_start_time
        selected = getattr(self, "_command_selected_text", "")
        proc_name, _ = get_active_window_info()
        app_cat = detect_app_category()

        self._overlay.show_processing()
        if self._tray:
            self._tray.set_state("processing")

        threading.Thread(
            target=self._process_command,
            args=(selected, duration, proc_name, app_cat),
            daemon=True,
        ).start()

    def _process_command(self, selected: str, duration: float, app_name: str, app_cat: str) -> None:
        """Process a command mode recording."""
        capture = self._capture
        if capture is None:
            self._overlay.hide()
            return

        try:
            capture.stop()
            time.sleep(0.1)

            if capture.total_duration_sec < 0.3:
                self._overlay.hide()
                capture.close()
                self._capture = None
                if self._tray:
                    self._tray.set_state("idle")
                return

            wav_path, total_dur, _ = capture.snapshot_full()
            capture.close()
            self._capture = None

            # Transcribe the voice instruction with acoustic biasing
            biasing_words = list(self._dictionary) if self._dictionary else []
            if app_name and app_name.strip():
                biasing_words.append(app_name.strip())
            initial_p = ", ".join(biasing_words)

            result = self._pipeline.stt.transcribe(
                wav_path,
                language=self.cfg.transcription.language,
                initial_prompt=initial_p,
            )
            instruction = result.text.strip()

            if wav_path and os.path.exists(wav_path):
                os.remove(wav_path)

            if not instruction:
                self._overlay.hide()
                if self._tray:
                    self._tray.set_state("idle")
                return

            sys.stderr.write(f"[whisper-flow] command: '{instruction}'\n")

            if selected:
                # Transform mode: apply instruction to selected text
                system, user = build_transform_prompt(
                    selected,
                    instruction,
                    custom_transforms=getattr(self.cfg, "custom_transforms", None),
                )
                try:
                    output = self._pipeline.llm.process(
                        user,
                        system=system,
                        max_tokens=self.cfg.llm.max_tokens,
                        temperature=self.cfg.llm.temperature,
                    )
                except Exception as llm_exc:  # noqa: BLE001
                    sys.stderr.write(
                        f"[whisper-flow] LLM transform failed ({llm_exc}); "
                        f"inserting raw instruction.\n"
                    )
                    output = instruction
                sys.stderr.write(f"[whisper-flow] transform result:\n{output}\n")
                insert_text(output)

                try:
                    save_dictation(
                        transcript=instruction,
                        processed=output,
                        mode="transform",
                        app_name=app_name,
                        app_category=app_cat,
                        duration_sec=duration,
                        was_transform=True,
                    )
                except Exception:  # noqa: BLE001
                    pass
            else:
                # No text selected: treat as normal dictation
                if self.cfg.smart_formatting:
                    instruction = apply_smart_formatting(instruction)
                mode = resolve_mode(self._overlay.get_selected_mode())
                if mode != "raw":
                    system, user = build_prompt(mode, instruction)
                    try:
                        processed = self._pipeline.llm.process(
                            user,
                            system=system,
                            max_tokens=self.cfg.llm.max_tokens,
                            temperature=self.cfg.llm.temperature,
                        )
                    except Exception as llm_exc:  # noqa: BLE001
                        sys.stderr.write(
                            f"[whisper-flow] LLM cleanup failed ({llm_exc}); "
                            f"falling back to raw transcript.\n"
                        )
                        processed = instruction
                else:
                    processed = instruction
                insert_text(processed)

        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[whisper-flow] command error: {exc}\n")
            self._overlay.error(str(exc))
        finally:
            self._overlay.hide()
            if self._tray:
                self._tray.set_state("idle")
                self._tray.update_tooltip("whisper-flow (idle)")


def run_daemon(cfg: Config) -> int:
    """Entry point for the daemon command."""
    daemon = Daemon(cfg)
    daemon.run()
    return 0
