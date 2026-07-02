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
        self._selected_mode = "high"
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
            self._q.put((_MSG_STATUS, "Listening..."))

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
            w = 260
            h = 44
            x = (sw - w) // 2
            y = root.winfo_screenheight() - h - 80  # 80px from bottom
            root.geometry(f"{w}x{h}+{x}+{y}")

        _position()

        meter_level = [0.0]

        def _update_meter():
            meter_canvas.delete("all")
            amp = min(1.0, meter_level[0] * 10.0)
            bar_w = int(amp * 76)
            if bar_w > 0:
                color = "#4ade80" if amp < 0.6 else "#facc15" if amp < 0.85 else "#ef4444"
                meter_canvas.create_rectangle(2, 3, 2 + bar_w, 11, fill=color, outline="")
            else:
                meter_canvas.create_rectangle(2, 3, 4, 11, fill="#444", outline="")

        def _poll():
            try:
                while True:
                    msg_type, data = self._q.get_nowait()
                    if msg_type == _MSG_SHOW:
                        status_var.set(str(data) or "Listening...")
                        dot.itemconfig(dot_id, fill="#ff4444")
                        _position()
                        root.deiconify()
                    elif msg_type == _MSG_HIDE:
                        root.withdraw()
                        meter_level[0] = 0.0
                    elif msg_type == _MSG_STATUS:
                        status_var.set(str(data))
                    elif msg_type == _MSG_PROCESSING:
                        status_var.set(str(data) or "Processing...")
                        dot.itemconfig(dot_id, fill="#facc15")
                    elif msg_type == _MSG_AMPLITUDE:
                        rms = float(data)
                        meter_level[0] = max(rms, meter_level[0] * 0.9)
                        _update_meter()
            except queue.Empty:
                pass
            root.after(50, _poll)

        root.after(50, _poll)
        root.mainloop()
