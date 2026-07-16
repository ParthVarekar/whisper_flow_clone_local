"""Minimal translucent Apple-style floating overlay HUD for dictation state indication.

Pill-shaped, borderless, always-on-top window with:
  - Transparent rounded corners (Windows transparentcolor style)
  - Color-shifting border glow per phase
  - Phase animations: 5-bar equalizer (recording) -> Pop transition -> 4-dot shimmer (processing)
  - Asynchronous, non-blocking typewriter reveal and auto-hide
  - Satisfying Windows audio chimes on state transitions
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
        self.hide()

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
        """Show final polished output with a non-blocking typewriter reveal effect.

        Unlike the original synchronous implementation, this does NOT block
        the calling thread (daemon thread), so that dictation transitions
        immediately and stays completely responsive.
        """
        if not text:
            return
        display = text if len(text) <= 500 else text[:495] + "..."
        self._q.put((_MSG_RESULT, display))

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

    # -- Chime Helpers -------------------------------------------------------

    def _play_sound(self, sound_type: str) -> None:
        """Play satisfying, non-blocking procedural chimes in a background thread."""
        if sys.platform != "win32":
            return

        def _beep():
            try:
                import winsound
                if sound_type == "start":
                    # Upward notification tone
                    winsound.Beep(880, 80)
                    winsound.Beep(1100, 100)
                elif sound_type == "processing":
                    # Soft low confirmation
                    winsound.Beep(700, 120)
                elif sound_type == "error":
                    # Low warning triple beep
                    winsound.Beep(350, 100)
                    time.sleep(0.05)
                    winsound.Beep(350, 100)
            except Exception:
                pass

        threading.Thread(target=_beep, daemon=True).start()

    # -- Tk implementation ---------------------------------------------------

    def _run_tk(self) -> None:
        try:
            import tkinter as tk
        except ImportError:
            self._ready.set()
            return

        # Setup Windows process-level DPI awareness so the UI is crisp and clean
        if sys.platform == "win32":
            try:
                import ctypes
                shcore = ctypes.windll.shcore
                shcore.SetProcessDpiAwareness(2)  # Process Per Monitor DPI Aware
            except Exception:
                try:
                    ctypes.windll.user32.SetProcessDPIAware()
                except Exception:
                    pass

        root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", self._opacity)
        except Exception:  # noqa: BLE001
            pass

        # Use Windows-specific transparent color trick for rounded corners
        trans_color = "#000001"
        root.configure(bg=trans_color)
        try:
            root.attributes("-transparentcolor", trans_color)
        except Exception:  # noqa: BLE001
            pass

        # Set WS_EX_NOACTIVATE so focus is never stolen from foreground apps
        if sys.platform == "win32":
            try:
                import ctypes
                user32 = ctypes.windll.user32
                root.update()  # Force HWND creation in OS before calling winfo_id
                hwnd = user32.GetAncestor(root.winfo_id(), 2) or root.winfo_id()
                GWL_EXSTYLE = -20
                WS_EX_NOACTIVATE = 0x08000000
                ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                # Apply ONLY WS_EX_NOACTIVATE to avoid corrupting layered/alpha/transparency states managed by Tkinter
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style | WS_EX_NOACTIVATE)
            except Exception:  # noqa: BLE001
                pass

        bg_color = "#0b0d17"
        border_color_var = ["#38bdf8"]  # Mutable reference to update dynamically

        # Setup outer canvas container
        canvas = tk.Canvas(root, bg=trans_color, highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)

        # Place the inner layout frame on top of the capsule background
        inner_frame = tk.Frame(root, bg=bg_color)
        # Center inner frame in canvas
        inner_frame_win = canvas.create_window(0, 0, window=inner_frame, anchor="center")

        # Top status bar row inside the frame
        top_row = tk.Frame(inner_frame, bg=bg_color)
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

        # Mic indicator dot canvas
        dot = tk.Canvas(top_row, width=14, height=14, bg=bg_color, highlightthickness=0)
        dot.pack(side=tk.LEFT, padx=(0, 8))
        dot_id = dot.create_oval(2, 2, 12, 12, fill="#ef4444", outline="")

        # Level meter / wave shimmer canvas
        meter_canvas = tk.Canvas(top_row, width=90, height=14, bg=bg_color, highlightthickness=0)
        meter_canvas.pack(side=tk.RIGHT)

        # Content text label
        content_var = tk.StringVar(value="")
        content_lbl = tk.Label(
            inner_frame,
            textvariable=content_var,
            fg="#e2e8f0",
            bg=bg_color,
            font=("Segoe UI", 10),
            wraplength=496,
            justify=tk.LEFT,
            anchor="nw",
        )
        content_lbl.pack(fill=tk.BOTH, expand=True, side=tk.TOP, pady=(8, 0))

        self._root = root
        self._ready.set()

        # Canvas capsule rendering
        def _draw_capsule_bg(w, h, radius, border_color):
            canvas.delete("bg_shape")
            r = radius
            x0, y0 = 1, 1
            x1, y1 = w - 1, h - 1

            # Filled corner circles
            canvas.create_oval(x0, y0, x0 + r*2, y0 + r*2, fill=bg_color, outline="", tags="bg_shape")
            canvas.create_oval(x1 - r*2, y0, x1, y0 + r*2, fill=bg_color, outline="", tags="bg_shape")
            canvas.create_oval(x0, y1 - r*2, x0 + r*2, y1, fill=bg_color, outline="", tags="bg_shape")
            canvas.create_oval(x1 - r*2, y1 - r*2, x1, y1, fill=bg_color, outline="", tags="bg_shape")

            # Center filled rectangles
            canvas.create_rectangle(x0 + r, y0, x1 - r, y1, fill=bg_color, outline="", tags="bg_shape")
            canvas.create_rectangle(x0, y0 + r, x1, y1 - r, fill=bg_color, outline="", tags="bg_shape")

            # Border arcs
            canvas.create_arc(x0, y0, x0 + r*2, y0 + r*2, start=90, extent=90, style=tk.ARC, outline=border_color, width=1.5, tags="bg_shape")
            canvas.create_arc(x1 - r*2, y0, x1, y0 + r*2, start=0, extent=90, style=tk.ARC, outline=border_color, width=1.5, tags="bg_shape")
            canvas.create_arc(x0, y1 - r*2, x0 + r*2, y1, start=180, extent=90, style=tk.ARC, outline=border_color, width=1.5, tags="bg_shape")
            canvas.create_arc(x1 - r*2, y1 - r*2, x1, y1, start=270, extent=90, style=tk.ARC, outline=border_color, width=1.5, tags="bg_shape")

            # Border lines
            canvas.create_line(x0 + r, y0, x1 - r, y0, fill=border_color, width=1.5, tags="bg_shape")
            canvas.create_line(x0 + r, y1, x1 - r, y1, fill=border_color, width=1.5, tags="bg_shape")
            canvas.create_line(x0, y0 + r, x0, y1 - r, fill=border_color, width=1.5, tags="bg_shape")
            canvas.create_line(x1, y0 + r, x1, y1 - r, fill=border_color, width=1.5, tags="bg_shape")

        # Dynamic resizing of capsule
        def _resize_for_content():
            try:
                root.update_idletasks()
            except Exception:  # noqa: BLE001
                pass
            
            h_req = inner_frame.winfo_reqheight()
            w = 560
            h = h_req + 28  # Add padding for margins and borders
            
            screen_h = root.winfo_screenheight()
            max_h = screen_h // 2
            h = min(max_h, max(110, h))
            
            sw = root.winfo_screenwidth()
            x = (sw - w) // 2
            y = screen_h - h - 70
            
            root.geometry(f"{w}x{h}+{x}+{y}")
            canvas.config(width=w, height=h)
            canvas.coords(inner_frame_win, w // 2, h // 2)
            canvas.itemconfigure(inner_frame_win, width=w-32, height=h-24)
            
            _draw_capsule_bg(w, h, 20, border_color_var[0])

            try:
                root.update_idletasks()
            except Exception:  # noqa: BLE001
                pass

        # Interactive Drag support
        def start_drag(event):
            root.x = event.x
            root.y = event.y
            
        def drag(event):
            deltax = event.x - root.x
            deltay = event.y - root.y
            x = root.winfo_x() + deltax
            y = root.winfo_y() + deltay
            root.geometry(f"+{x}+{y}")

        canvas.bind("<Button-1>", start_drag)
        canvas.bind("<B1-Motion>", drag)

        _resize_for_content()

        # Animation states
        phase = ["idle"]
        pop_time = [0.0]
        anim_step = [0]
        meter_level = [0.0]

        # Scheduled task holders
        typewriter_task = [None]
        auto_hide_task = [None]

        def _cancel_scheduled_tasks():
            if typewriter_task[0] is not None:
                try:
                    root.after_cancel(typewriter_task[0])
                except Exception:  # noqa: BLE001
                    pass
                typewriter_task[0] = None
            if auto_hide_task[0] is not None:
                try:
                    root.after_cancel(auto_hide_task[0])
                except Exception:  # noqa: BLE001
                    pass
                auto_hide_task[0] = None

        def _render_frame():
            nonlocal dot_id
            now = time.monotonic()
            current_phase = phase[0]
            anim_step[0] += 1
            step = anim_step[0]

            # --- POP TRANSITION ---
            if current_phase == "pop":
                elapsed = now - pop_time[0]
                if elapsed < 0.15:
                    dot.delete("all")
                    dot.create_oval(0, 0, 14, 14, fill="#22c55e", outline="#4ade80", width=2)
                else:
                    phase[0] = "processing"
                    dot.delete("all")
                    dot.create_oval(2, 2, 12, 12, fill="#a855f7", outline="")

            # --- RECORDING EQUALIZER WAVE ---
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

            # --- PROCESSING SHIMMER ---
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

        # Async non-blocking typewriter routine
        def _run_async_typewriter(text: str):
            words = text.split(" ")
            chunk_size = 3

            def reveal_chunk(index):
                partial = " ".join(words[:index + chunk_size])
                if index + chunk_size < len(words):
                    partial += " ▌"
                content_var.set(f'"{partial}"')
                _resize_for_content()

                if index + chunk_size < len(words):
                    typewriter_task[0] = root.after(40, reveal_chunk, index + chunk_size)
                else:
                    content_var.set(f'"{text}"')
                    _resize_for_content()
                    typewriter_task[0] = None
                    
                    # Schedule non-blocking auto-hide
                    word_count = len(words)
                    hide_delay_ms = min(30000, int(5000 + word_count * 80))
                    auto_hide_task[0] = root.after(hide_delay_ms, lambda: self.hide() if phase[0] == "done" else None)

            reveal_chunk(0)

        # Queue poller
        def _poll():
            nonlocal dot_id
            try:
                try:
                    while True:
                        msg_type, data = self._q.get_nowait()
                        if msg_type == _MSG_SHOW:
                            _cancel_scheduled_tasks()
                            status_str = str(data) or "Listening..."
                            status_var.set(status_str)
                            content_var.set("")
                            
                            # Adapt UI phase based on status context
                            if "polishing" in status_str.lower() or "transcribed" in status_str.lower():
                                phase[0] = "processing"
                                status_lbl.configure(fg="#c084fc")
                                border_color_var[0] = "#a855f7"
                            else:
                                phase[0] = "recording"
                                status_lbl.configure(fg="#38bdf8")
                                border_color_var[0] = "#38bdf8"
                                dot.delete("all")
                                dot_id = dot.create_oval(2, 2, 12, 12, fill="#ef4444", outline="")
                                
                            if status_str.startswith("Listening"):
                                self._play_sound("start")
                            _resize_for_content()
                            root.deiconify()
                            if sys.platform == "win32":
                                try:
                                    import ctypes
                                    hwnd = ctypes.windll.user32.GetAncestor(root.winfo_id(), 2) or root.winfo_id()
                                    ctypes.windll.user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
                                    ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)
                                except Exception:  # noqa: BLE001
                                    pass
                        elif msg_type == _MSG_HIDE:
                            _cancel_scheduled_tasks()
                            phase[0] = "idle"
                            root.withdraw()
                            meter_level[0] = 0.0
                            content_var.set("")
                            _resize_for_content()
                        elif msg_type == _MSG_PREVIEW:
                            if phase[0] == "recording":
                                content_var.set(f"🎙️ \"{data}\"")
                            else:
                                content_var.set(f"\"{data}\"")
                            _resize_for_content()
                        elif msg_type == _MSG_STATUS:
                            status_var.set(str(data))
                            if str(data).startswith("Error:"):
                                self._play_sound("error")
                                border_color_var[0] = "#ef4444"
                                status_lbl.configure(fg="#ef4444")
                                _resize_for_content()
                        elif msg_type == _MSG_PROCESSING:
                            _cancel_scheduled_tasks()
                            status_var.set(str(data) or "Processing...")
                            status_lbl.configure(fg="#c084fc")
                            border_color_var[0] = "#a855f7"
                            phase[0] = "pop"
                            self._play_sound("processing")
                            pop_time[0] = time.monotonic()
                            content_var.set("")
                            _resize_for_content()
                        elif msg_type == _MSG_RESULT:
                            _cancel_scheduled_tasks()
                            status_var.set("✨ Post-processed Output")
                            status_lbl.configure(fg="#4ade80")
                            border_color_var[0] = "#22c55e"
                            phase[0] = "done"
                            dot.delete("all")
                            dot_id = dot.create_oval(2, 2, 12, 12, fill="#22c55e", outline="")
                            # Trigger async typewriter effect
                            _run_async_typewriter(str(data))
                        elif msg_type == _MSG_AMPLITUDE:
                            rms = float(data)
                            meter_level[0] = max(rms, meter_level[0] * 0.85)
                except queue.Empty:
                    pass
            except Exception as e:
                sys.stderr.write(f"[whisper-flow] UI poll error: {e}\n")
            finally:
                root.after(40, _poll)

        root.after(30, _render_frame)
        root.after(40, _poll)
        root.mainloop()
