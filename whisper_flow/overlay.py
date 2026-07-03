"""Minimal floating overlay for dictation state indication.

Shows a tiny, borderless, always-on-top, semi-transparent window with:
  - A listening indicator (mic icon / "Listening...")
  - A small level meter bar
  - Status text (processing, inserting, etc.)

Appears when dictation starts, disappears when text is inserted.
Designed to feel like Wispr Flow's invisible floating bubble.
"""

from __future__ import annotations

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


class OverlayNotifier:
    """A minimal floating-bubble notifier that replaces the full TkNotifier.

    This is the Wispr Flow-style "invisible" UI: a small indicator that appears
    when you hold the hotkey and vanishes after text is inserted.
    """

    def __init__(self, *, opacity: float = 0.85):
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
        pass  # Overlay doesn't show stages

    def progress(self, percent: int, detail: str = "") -> None:
        if percent >= 100:
            self._q.put((_MSG_STATUS, "Done"))

    def segment(self, text: str, ts: str = "") -> None:
        pass  # No transcript pane in overlay

    def preview(self, text: str) -> None:
        if text:
            # Truncate to last ~60 chars for the tiny overlay
            display = text if len(text) <= 60 else "..." + text[-57:]
            self._q.put((_MSG_STATUS, display))

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
        pass  # Start is driven by hotkey, not a button

    def result(self, kind: str, text: str) -> None:
        pass  # Results are handled by the daemon

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
        # Auto-hide after 3 seconds on error
        threading.Timer(3.0, self.hide).start()

    def run(self, work: Callable[[], object]) -> object:
        """Run work synchronously — overlay lifecycle is managed externally by daemon."""
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
        self._q.put((_MSG_PROCESSING, "Processing..."))

    def set_mode(self, mode: str) -> None:
        self._selected_mode = mode

    def set_mic(self, mic: str) -> None:
        self._selected_mic = mic

    def set_writing_style(self, style: str) -> None:
        self._selected_writing_style = style

    def start_ui_thread(self) -> None:
        """Start the Tk event loop in a background thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_tk, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)

    def stop_ui_thread(self) -> None:
        """Destroy the Tk root and stop the UI thread."""
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
        root.withdraw()  # Start hidden
        root.overrideredirect(True)  # Borderless
        root.attributes("-topmost", True)  # Always on top
        try:
            root.attributes("-alpha", self._opacity)
        except Exception:  # noqa: BLE001
            pass

        # Dark rounded-look frame
        root.configure(bg="#1a1a2e")

        frame = tk.Frame(root, bg="#1a1a2e", padx=16, pady=10)
        frame.pack(fill=tk.BOTH, expand=True)

        # Status label
        status_var = tk.StringVar(value="Listening...")
        status_lbl = tk.Label(
            frame,
            textvariable=status_var,
            fg="#e0e0e0",
            bg="#1a1a2e",
            font=("Segoe UI", 11, "bold"),
        )
        status_lbl.pack(side=tk.LEFT, padx=(0, 12))

        # Mic indicator dot
        dot = tk.Canvas(frame, width=14, height=14, bg="#1a1a2e", highlightthickness=0)
        dot.pack(side=tk.LEFT, padx=(0, 8))
        dot_id = dot.create_oval(2, 2, 12, 12, fill="#ff4444", outline="")

        # Level meter bar
        meter_canvas = tk.Canvas(frame, width=80, height=14, bg="#1a1a2e", highlightthickness=0)
        meter_canvas.pack(side=tk.LEFT)

        self._root = root
        self._ready.set()

        # Position at bottom-center of screen
        def _position():
            sw = root.winfo_screenwidth()
            w = 420
            h = 44
            x = (sw - w) // 2
            y = root.winfo_screenheight() - h - 80  # 80px from bottom
            root.geometry(f"{w}x{h}+{x}+{y}")

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
                    # Pop scale expansion (scale up to 18px dot)
                    dot.delete("all")
                    dot.create_oval(0, 0, 14, 14, fill="#22c55e", outline="#4ade80", width=2)
                else:
                    # Pop finished -> transition to processing phase
                    phase[0] = "processing"
                    dot.delete("all")
                    dot.create_oval(2, 2, 12, 12, fill="#a855f7", outline="")

            # --- 2. RECORDING PHASE: Equalizer Waveform & Pulse Dot ---
            elif current_phase == "recording":
                # Pulsing red recording dot
                pulse_color = "#ef4444" if (step // 8) % 2 == 0 else "#f87171"
                dot.itemconfig(dot_id, fill=pulse_color)

                # Render 5-bar animated audio equalizer waveform
                meter_canvas.delete("all")
                amp = min(1.0, meter_level[0] * 10.0)
                import math
                num_bars = 5
                bar_width = 8
                spacing = 5
                start_x = 8
                canvas_h = 14

                for i in range(num_bars):
                    # Combine audio amplitude with smooth harmonic wave
                    sine_wave = math.sin((step * 0.25) + (i * 0.8)) * 0.25 + 0.25
                    bar_h = int(max(3.0, (amp * 11.0 * (0.6 + sine_wave)) + 2.0))
                    bar_h = min(canvas_h, bar_h)

                    x0 = start_x + i * (bar_width + spacing)
                    y1 = (canvas_h + bar_h) // 2
                    y0 = y1 - bar_h

                    # Color gradient per bar
                    colors = ["#38bdf8", "#818cf8", "#a855f7", "#c084fc", "#4ade80"]
                    color = colors[i % len(colors)] if amp > 0.05 else "#4b5563"
                    meter_canvas.create_rectangle(x0, y0, x0 + bar_width, y1, fill=color, outline="")

            # --- 3. PROCESSING PHASE: Bouncing Purple/Cyan Wave Shimmer ---
            elif current_phase == "processing":
                dot.itemconfig(dot_id, fill="#a855f7")
                meter_canvas.delete("all")
                import math
                num_dots = 4
                canvas_h = 14
                start_x = 10
                spacing = 16

                for i in range(num_dots):
                    # Smooth harmonic bouncing wave
                    offset = math.sin((step * 0.2) + (i * 0.7)) * 4.0
                    cy = (canvas_h // 2) + offset
                    r = 3.5

                    # Soft violet to cyan gradient
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

            # Schedule next animation frame (~30ms for 33 FPS)
            root.after(30, _render_frame)

        def _poll():
            try:
                while True:
                    msg_type, data = self._q.get_nowait()
                    if msg_type == _MSG_SHOW:
                        status_var.set(str(data) or "Listening...")
                        phase[0] = "recording"
                        dot.delete("all")
                        dot.create_oval(2, 2, 12, 12, fill="#ef4444", outline="")
                        _position()
                        root.deiconify()
                    elif msg_type == _MSG_HIDE:
                        phase[0] = "idle"
                        root.withdraw()
                        meter_level[0] = 0.0
                    elif msg_type == _MSG_STATUS:
                        status_var.set(str(data))
                    elif msg_type == _MSG_PROCESSING:
                        status_var.set(str(data) or "Processing...")
                        # Trigger Pop animation transition
                        phase[0] = "pop"
                        pop_time[0] = time.monotonic()
                    elif msg_type == _MSG_AMPLITUDE:
                        rms = float(data)
                        meter_level[0] = max(rms, meter_level[0] * 0.85)
            except queue.Empty:
                pass
            root.after(40, _poll)

        root.after(30, _render_frame)
        root.after(40, _poll)
        root.mainloop()
