"""Minimal translucent Apple-style floating overlay HUD for dictation state indication.

Shows a borderless, always-on-top, semi-transparent glass card with:
  - Phase animations: 5-bar equalizer (recording) -> Pop transition -> 4-dot shimmer (processing)
  - Top status bar (Listening, Processing, ✨ Post-processed Result)
  - Expanded text box section showing full live preview & post-processed text
"""

from __future__ import annotations

import math
import os
import queue
import sys
import threading
import time
from typing import Callable, Optional


_MSG_SHOW = "show"
_MSG_HIDE = "hide"
_MSG_STATUS = "status"
_MSG_AMPLITUDE = "amplitude"
_MSG_PROCESSING = "processing"
_MSG_RESULT = "result"
_MSG_PREVIEW = "preview"


class OverlayNotifier:
    """A translucent Apple-style floating HUD card for WhisperFlow dictation."""

    def __init__(self, *, opacity: float = 0.92):
        self._q: queue.Queue = queue.Queue()
        self._opacity = max(0.2, min(1.0, opacity))
        self._root = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._cancel_cb: Optional[Callable[[], None]] = None
        self._selected_mode = "auto"
        self._selected_mic = "default"
        self._selected_writing_style = "default"

    # -- Notifier Protocol ---------------------------------------------------

    def stage(self, name: str, detail: str = "") -> None:
        pass

    def progress(self, percent: int, detail: str = "") -> None:
        if percent >= 100:
            self._q.put((_MSG_STATUS, "Done"))

    def segment(self, text: str, ts: str = "") -> None:
        pass

    def preview(self, text: str) -> None:
        if text:
            # Send live preview text (up to ~300 chars) to the HUD text box
            display = text if len(text) <= 300 else "..." + text[-295:]
            self._q.put((_MSG_PREVIEW, display))

    def on_stream_preview(self, text: str) -> None:
        """Called by the live preview loop with partial transcription text."""
        self.preview(text)

    def amplitude(self, rms: float) -> None:
        self._q.put((_MSG_AMPLITUDE, rms))

    def audio_info(self, duration_sec: float, model_name: str) -> None:
        pass

    def register_cancel(self, cb: Callable[[], None]) -> None:
        self._cancel_cb = cb

    def register_start(self, cb: Callable[[], None]) -> None:
        pass

    def result(self, kind: str, text: str) -> None:
        pass

    def get_selected_mode(self) -> str:
        return self._selected_mode

    def get_selected_mic(self) -> str:
        return self._selected_mic

    def get_selected_writing_style(self) -> str:
        return self._selected_writing_style

    def done(self, message: str = "") -> None:
        self.hide()

    def error(self, message: str) -> None:
        self._q.put((_MSG_STATUS, f"Error: {message}"))
        threading.Timer(3.0, self.hide).start()

    def run(self, work: Callable[[], object]) -> object:
        return work()

    # -- Overlay-specific API ------------------------------------------------

    def show(self, status: str = "Listening...") -> None:
        """Show the floating overlay."""
        self._q.put((_MSG_SHOW, status))

    def hide(self) -> None:
        """Hide the floating overlay."""
        self._q.put((_MSG_HIDE, ""))

    def show_processing(self) -> None:
        """Update overlay to show processing state."""
        self._q.put((_MSG_PROCESSING, "Processing & Polishing..."))

    def show_result(self, text: str) -> None:
        """Show final polished output with a typewriter reveal effect.

        SYNCHRONOUS — blocks until the typewriter reveal is complete, then
        sends the final RESULT (which triggers dynamic auto-hide). This
        prevents the daemon's finally block from hiding the popup before
        the typewriter animation finishes.

        The text appears progressively (word-by-word) so the user sees
        the polished output appear, making the polishing step feel responsive.
        """
        if not text:
            return
        display = text if len(text) <= 500 else text[:495] + "..."

        # Synchronous typewriter reveal — blocks the calling thread
        import time as _time
        words = display.split(" ")
        chunk_size = 3  # 3 words per chunk for natural typing speed
        for i in range(0, len(words), chunk_size):
            partial = " ".join(words[:i + chunk_size])
            if i + chunk_size < len(words):
                partial += " ▌"  # cursor block
            # Use PREVIEW during typing (doesn't trigger auto-hide)
            self._q.put((_MSG_PREVIEW, partial))
            _time.sleep(0.04)  # 40ms between chunks

        # Send the final RESULT (switches to "done" state with green dot
        # and triggers dynamic auto-hide based on text length)
        self._q.put((_MSG_RESULT, display))

        # Wait for the dynamic auto-hide duration before returning, so the
        # daemon's finally block (which calls hide()) doesn't kill the popup
        # before the user has time to read the result.
        word_count = len(display.split())
        stay_ms = min(30000, int(5000 + word_count * 80))
        _time.sleep(stay_ms / 1000.0)

    def set_mode(self, mode: str) -> None:
        self._selected_mode = mode

    def set_mic(self, mic: str) -> None:
        self._selected_mic = mic

    def set_writing_style(self, style: str) -> None:
        self._selected_writing_style = style

    def start_ui_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_tk, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)

    def stop_ui_thread(self) -> None:
        if self._root is not None:
            try:
                self._root.after(0, self._root.destroy)
            except Exception:  # noqa: BLE001
                pass

    # -- Tk implementation ---------------------------------------------------

    def _run_tk(self) -> None:
        try:
            import tkinter as tk
        except ImportError:
            self._ready.set()
            return

        root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", self._opacity)
        except Exception:  # noqa: BLE001
            pass

        # On Windows, set WS_EX_NOACTIVATE so the floating HUD never steals focus from active apps/browser tabs
        if sys.platform == "win32":
            try:
                import ctypes
                user32 = ctypes.windll.user32
                hwnd = user32.GetAncestor(root.winfo_id(), 2) or root.winfo_id()
                GWL_EXSTYLE = -20
                WS_EX_NOACTIVATE = 0x08000000
                WS_EX_TOPMOST = 0x00000008
                ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style | WS_EX_NOACTIVATE | WS_EX_TOPMOST)
            except Exception:  # noqa: BLE001
                pass

        # Dark macOS Glass aesthetic
        bg_color = "#0b0d17"
        border_color = "#2a2e45"
        root.configure(bg=border_color)

        outer_frame = tk.Frame(root, bg=border_color, padx=1, pady=1)
        outer_frame.pack(fill=tk.BOTH, expand=True)

        main_frame = tk.Frame(outer_frame, bg=bg_color, padx=16, pady=12)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Top status bar row
        top_row = tk.Frame(main_frame, bg=bg_color)
        top_row.pack(fill=tk.X, expand=False, side=tk.TOP)

        # Status label
        status_var = tk.StringVar(value="Listening...")
        status_lbl = tk.Label(
            top_row,
            textvariable=status_var,
            fg="#38bdf8",
            bg=bg_color,
            font=("Segoe UI", 11, "bold"),
            anchor="w",
        )
        status_lbl.pack(side=tk.LEFT, padx=(0, 10))

        # Mic indicator dot
        dot = tk.Canvas(top_row, width=14, height=14, bg=bg_color, highlightthickness=0)
        dot.pack(side=tk.LEFT, padx=(0, 8))
        dot_id = dot.create_oval(2, 2, 12, 12, fill="#ef4444", outline="")

        # Level meter / wave shimmer canvas
        meter_canvas = tk.Canvas(top_row, width=90, height=14, bg=bg_color, highlightthickness=0)
        meter_canvas.pack(side=tk.RIGHT)

        # Preview / Result text section (multi-line wrapped text box)
        content_var = tk.StringVar(value="")
        content_lbl = tk.Label(
            main_frame,
            textvariable=content_var,
            fg="#e2e8f0",
            bg=bg_color,
            font=("Segoe UI", 10),
            wraplength=530,
            justify=tk.LEFT,
            anchor="nw",
        )
        content_lbl.pack(fill=tk.BOTH, expand=True, side=tk.TOP, pady=(8, 0))

        self._root = root
        self._ready.set()

        # Dynamic popup sizing — resize based on content length
        def _resize_for_content(text: str):
            """Resize the popup to fit the content dynamically.

            Width: 560px (stable)
            Height: 130px base, +20px per line, up to 50% of screen height
            Called on every PREVIEW and RESULT message so the popup grows
            as text is added during the typewriter reveal.
            """
            screen_h = root.winfo_screenheight()
            max_h = screen_h // 2  # 50% of screen height
            if not text:
                w, h = 560, 130
            else:
                # Estimate lines based on wraplength and text length
                wrap = 530
                chars_per_line = wrap // 8  # ~8px per char at 10pt
                # Count explicit newlines (list items) + wrapped lines
                lines = 0
                for line in text.split("\n"):
                    line_len = max(1, len(line))
                    lines += max(1, (line_len + chars_per_line - 1) // chars_per_line)
                lines = max(1, lines)
                w = 560  # keep width stable
                h = min(max_h, max(130, 70 + lines * 20))
            sw = root.winfo_screenwidth()
            x = (sw - w) // 2
            y = screen_h - h - 70
            root.geometry(f"{w}x{h}+{x}+{y}")
            # Force the window manager to apply the resize immediately
            try:
                root.update_idletasks()
            except Exception:  # noqa: BLE001
                pass

        def _position():
            _resize_for_content("")

        _position()

        # State variables for animation loop
        phase = ["idle"]  # "recording", "pop", "processing", "done"
        pop_time = [0.0]
        anim_step = [0]
        meter_level = [0.0]

        def _render_frame():
            now = time.monotonic()
            current_phase = phase[0]
            anim_step[0] += 1
            step = anim_step[0]

            # --- 1. POP TRANSITION ANIMATION ---
            if current_phase == "pop":
                elapsed = now - pop_time[0]
                if elapsed < 0.15:
                    dot.delete("all")
                    dot.create_oval(0, 0, 14, 14, fill="#22c55e", outline="#4ade80", width=2)
                else:
                    phase[0] = "processing"
                    dot.delete("all")
                    dot.create_oval(2, 2, 12, 12, fill="#a855f7", outline="")

            # --- 2. RECORDING PHASE: Equalizer Waveform & Pulse Dot ---
            elif current_phase == "recording":
                pulse_color = "#ef4444" if (step // 8) % 2 == 0 else "#f87171"
                dot.itemconfig(dot_id, fill=pulse_color)

                meter_canvas.delete("all")
                amp = min(1.0, meter_level[0] * 10.0)
                num_bars = 5
                bar_width = 8
                spacing = 5
                start_x = 8
                canvas_h = 14

                for i in range(num_bars):
                    sine_wave = math.sin((step * 0.25) + (i * 0.8)) * 0.25 + 0.25
                    bar_h = int(max(3.0, (amp * 11.0 * (0.6 + sine_wave)) + 2.0))
                    bar_h = min(canvas_h, bar_h)

                    x0 = start_x + i * (bar_width + spacing)
                    y1 = (canvas_h + bar_h) // 2
                    y0 = y1 - bar_h

                    colors = ["#38bdf8", "#818cf8", "#a855f7", "#c084fc", "#4ade80"]
                    color = colors[i % len(colors)] if amp > 0.05 else "#4b5563"
                    meter_canvas.create_rectangle(x0, y0, x0 + bar_width, y1, fill=color, outline="")

            # --- 3. PROCESSING PHASE: Bouncing Purple/Cyan Wave Shimmer ---
            elif current_phase == "processing":
                dot.itemconfig(dot_id, fill="#a855f7")
                meter_canvas.delete("all")
                num_dots = 4
                canvas_h = 14
                start_x = 10
                spacing = 16

                for i in range(num_dots):
                    offset = math.sin((step * 0.2) + (i * 0.7)) * 4.0
                    cy = (canvas_h // 2) + offset
                    r = 3.5

                    colors = ["#a855f7", "#818cf8", "#38bdf8", "#34d399"]
                    color = colors[i % len(colors)]
                    meter_canvas.create_oval(
                        start_x + i * spacing - r,
                        cy - r,
                        start_x + i * spacing + r,
                        cy + r,
                        fill=color,
                        outline=""
                    )

            root.after(30, _render_frame)

        def _poll():
            try:
                while True:
                    msg_type, data = self._q.get_nowait()
                    if msg_type == _MSG_SHOW:
                        status_var.set(str(data) or "Listening...")
                        status_lbl.configure(fg="#38bdf8")
                        content_var.set("")
                        phase[0] = "recording"
                        dot.delete("all")
                        dot.create_oval(2, 2, 12, 12, fill="#ef4444", outline="")
                        _position()
                        root.deiconify()
                        if sys.platform == "win32":
                            try:
                                import ctypes
                                user32 = ctypes.windll.user32
                                hwnd = user32.GetAncestor(root.winfo_id(), 2) or root.winfo_id()
                                user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
                                user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)
                            except Exception:  # noqa: BLE001
                                pass
                    elif msg_type == _MSG_HIDE:
                        phase[0] = "idle"
                        root.withdraw()
                        meter_level[0] = 0.0
                        content_var.set("")
                        _resize_for_content("")
                    elif msg_type == _MSG_PREVIEW:
                        content_var.set(f"🎙️ \"{data}\"")
                        _resize_for_content(str(data))
                    elif msg_type == _MSG_STATUS:
                        status_var.set(str(data))
                    elif msg_type == _MSG_PROCESSING:
                        status_var.set(str(data) or "Processing...")
                        status_lbl.configure(fg="#c084fc")
                        phase[0] = "pop"
                        pop_time[0] = time.monotonic()
                    elif msg_type == _MSG_RESULT:
                        status_var.set("✨ Post-processed Output")
                        status_lbl.configure(fg="#4ade80")
                        content_var.set(f"\"{data}\"")
                        _resize_for_content(str(data))
                        phase[0] = "done"
                        dot.delete("all")
                        dot.create_oval(2, 2, 12, 12, fill="#22c55e", outline="")
                        # Dynamic auto-hide: longer text stays longer.
                        # Formula: 5s base + 0.08s per word, capped at 30s max.
                        # Examples:
                        #   10 words → 5.8s
                        #   30 words → 7.4s
                        #   50 words → 9s
                        #   100 words → 13s
                        #   200+ words → 21s
                        #   300+ words → 30s (max)
                        word_count = len(str(data).split())
                        hide_delay_ms = min(30000, int(5000 + word_count * 80))
                        root.after(hide_delay_ms, lambda: self.hide() if phase[0] == "done" else None)
                    elif msg_type == _MSG_AMPLITUDE:
                        rms = float(data)
                        meter_level[0] = max(rms, meter_level[0] * 0.85)
            except queue.Empty:
                pass
            root.after(40, _poll)

        root.after(30, _render_frame)
        root.after(40, _poll)
        root.mainloop()
