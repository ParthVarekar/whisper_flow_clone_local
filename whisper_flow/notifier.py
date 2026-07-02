"""GUI / headless progress notifier for whisper-flow.

Mirrors whisper.cpp's own live feedback model (see RESEARCH.md, Task ID 3):
  * progress percentage  -> whisper_print_progress_callback: progress = NN%  (stderr)
  * segment streaming     -> [HH:MM:SS.mmm --> HH:MM:SS.mmm]  text            (stdout)

We surface both in a small Tkinter window that updates in real time as the
subprocess streams output. The window is the "the tool is working" indicator.

Design:
  * A `Notifier` is a thread-safe sink. The pipeline/backend call
    `.stage() / .progress() / .segment() / .done() / .error()` from a worker
    thread. Each method posts to an internal queue.
  * `TkNotifier.run(work)` spawns the worker thread, runs the Tk mainloop on
    the *calling* (main) thread, drains the queue via `after()`, and updates
    widgets. Only the main thread ever touches Tk widgets (Tk is not
    thread-safe).
  * `NullNotifier` (headless fallback) just prints to stderr — used when there
    is no display, Tkinter is unavailable, or the user passed --no-gui.
  * Optional desktop notifications via `notify-send` (libnotify) on
    start/done/error, enabled with --notify.

No third-party deps: Tkinter is Python stdlib. If it's missing or there's no
$DISPLAY, we silently fall back to NullNotifier so the tool never breaks in a
headless / minimal environment.
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable, Optional, Protocol

# tkinter is imported lazily inside TkNotifier.run so the module loads on
# headless systems where Tkinter may be absent. tk.END is referenced in
# TkNotifier methods via a lazy module-level import below; the alias is set
# after the class definition to avoid importing tkinter at module load time.
_TK = None  # set on first TkNotifier.run(); module-level so button handlers can use _TK.END


# ---------------------------------------------------------------------------
# Notifier protocol
# ---------------------------------------------------------------------------

class Notifier(Protocol):
    """Progress sink implemented by both Tk and Null notifiers.

    Methods are callable from any thread (TkNotifier posts to a queue;
    NullNotifier writes to stderr). `run()` must be called from the main thread
    when using Tk (Tk's mainloop is main-thread-only).
    """

    def stage(self, name: str, detail: str = "") -> None: ...
    def progress(self, percent: int, detail: str = "") -> None: ...
    def segment(self, text: str, ts: str = "") -> None: ...
    def amplitude(self, rms: float) -> None: ...
    def audio_info(self, duration_sec: float, model_name: str) -> None: ...
    def register_cancel(self, cb: Callable[[], None]) -> None: ...
    def register_start(self, cb: Callable[[], None]) -> None: ...
    def done(self, message: str = "") -> None: ...
    def error(self, message: str) -> None: ...

    def run(self, work: Callable[[], object]) -> object:
        """Run `work` (in a worker thread for Tk; inline for Null) and block
        until it finishes. Returns work()'s return value; re-raises its
        exception after dismissing the UI."""
        ...


# ---------------------------------------------------------------------------
# Desktop notification (libnotify / notify-send), best-effort
# ---------------------------------------------------------------------------

def desktop_notify(title: str, body: str = "", *, icon: str = "audio-input-microphone",
                   urgency: str = "normal") -> None:
    """Fire a desktop notification if notify-send is available; no-op otherwise."""
    bin_path = shutil.which("notify-send")
    if bin_path is None:
        return
    try:
        subprocess.run(
            [bin_path, "-a", "whisper-flow", "-u", urgency, "-i", icon, title, body],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            timeout=5,
        )
    except Exception:  # noqa: BLE001 — notifications are best-effort
        pass


# ---------------------------------------------------------------------------
# Null notifier (headless fallback)
# ---------------------------------------------------------------------------

class NullNotifier:
    """Prints progress to stderr. Used when no GUI is available/requested.

    Output mirrors whisper.cpp's own lines so logs stay familiar:
      [whisper-flow] stage: Transcribing ...
      [whisper-flow] progress: 45%
      [whisper-flow] segment: [00:00:05.234 --> 00:00:08.120]  hello world
    """

    def __init__(self, *, notify: bool = False, verbose: bool = False):
        self.notify = notify
        self.verbose = verbose
        self._cancel_cb: Optional[Callable[[], None]] = None
        self._start_cb: Optional[Callable[[], None]] = None

    def _emit(self, tag: str, msg: str) -> None:
        sys.stderr.write(f"[whisper-flow] {tag}: {msg}\n")
        sys.stderr.flush()

    def stage(self, name: str, detail: str = "") -> None:
        self._emit("stage", f"{name}{(' — ' + detail) if detail else ''}")
        if self.notify:
            desktop_notify("whisper-flow", name, icon="audio-input-microphone")

    def progress(self, percent: int, detail: str = "") -> None:
        if self.verbose or percent % 10 == 0 or percent >= 100:
            self._emit("progress", f"{percent}%{(' — ' + detail) if detail else ''}")

    def segment(self, text: str, ts: str = "") -> None:
        if self.verbose:
            line = f"[{ts}]  {text}" if ts else text
            self._emit("segment", line)

    def amplitude(self, rms: float) -> None:
        # level meter is GUI-only; no-op in headless mode (avoid log spam)
        pass

    def audio_info(self, duration_sec: float, model_name: str) -> None:
        if self.verbose and (duration_sec or model_name):
            self._emit("audio", f"dur={duration_sec:.1f}s model={model_name}")

    def register_cancel(self, cb: Callable[[], None]) -> None:
        # no UI in headless mode; Ctrl+C in the terminal is the cancel path
        self._cancel_cb = cb

    def register_start(self, cb: Callable[[], None]) -> None:
        # headless mode auto-starts immediately
        self._start_cb = cb
        cb()

    def done(self, message: str = "") -> None:
        self._emit("done", message or "complete")
        if self.notify:
            desktop_notify("whisper-flow: done", message or "transcription complete",
                           icon="dialog-information")

    def error(self, message: str) -> None:
        self._emit("error", message)
        if self.notify:
            desktop_notify("whisper-flow: error", message, icon="dialog-error",
                           urgency="critical")

    def run(self, work: Callable[[], object]) -> object:
        return work()


# ---------------------------------------------------------------------------
# Tk notifier (live GUI window)
# ---------------------------------------------------------------------------

# Queue message types
_MSG_STAGE = "stage"
_MSG_PROGRESS = "progress"
_MSG_SEGMENT = "segment"
_MSG_AMPLITUDE = "amplitude"
_MSG_AUDIO_INFO = "audio_info"
_MSG_DONE = "done"
_MSG_ERROR = "error"
_MSG_CANCEL = "cancel"


class _Msg:
    __slots__ = ("kind", "name", "percent", "detail", "text", "ts", "message",
                 "rms", "duration", "model")

    def __init__(self, kind: str, **kw):
        self.kind = kind
        for k in ("name", "percent", "detail", "text", "ts", "message"):
            setattr(self, k, kw.get(k, ""))
        self.rms = float(kw.get("rms", 0.0) or 0.0)
        self.duration = float(kw.get("duration", 0.0) or 0.0)
        self.model = str(kw.get("model", "") or "")


class _LevelMeter:
    """Symmetric RMS bar meter on a tk.Canvas, ported from Buzz's AudioMeterWidget.

    Only the main thread draws on the canvas (Tk is not thread-safe). The worker
    thread feeds RMS values via the notifier queue; the main-thread poller calls
    `update_amplitude(rms)` which applies peak-hold decay and redraws.
    """

    def __init__(self, canvas, *, width: int = 380, height: int = 28,
                 bar_width: int = 2, bar_margin: int = 1):
        self.canvas = canvas
        self.width = width
        self.height = height
        self.bar_width = bar_width
        self.bar_margin = bar_margin
        self.current = 0.0   # peak-hold with decay
        self.average = 0.0   # smoothed RMS
        self._decay = 0.95   # per-frame decay factor
        self._scale = 10.0   # amplitude scale (input RMS is tiny)
        self._min_amp = 0.00005
        self._bar_ids: list[int] = []

    def update(self, rms: float) -> None:
        # peak-hold decay
        self.current = max(rms, self.current * self._decay)
        self.average = self.average * 0.9 + rms * 0.1
        self._redraw()

    def _redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        amp = max(self.current, self._min_amp) * self._scale
        amp = min(amp, 1.0)
        n_bars_half = (self.width // 2) // (self.bar_width + self.bar_margin)
        active_half = int(n_bars_half * amp)
        cx = self.width // 2
        # draw symmetric bars from center outward
        color_active = "#4a4a4a"
        color_inactive = "#cccccc"
        for i in range(n_bars_half):
            x_right = cx + i * (self.bar_width + self.bar_margin)
            x_left = cx - (i + 1) * (self.bar_width + self.bar_margin)
            col = color_active if i < active_half else color_inactive
            c.create_rectangle(x_right, 0, x_right + self.bar_width, self.height,
                               fill=col, outline="", width=0)
            c.create_rectangle(x_left, 0, x_left + self.bar_width, self.height,
                               fill=col, outline="", width=0)


class TkNotifier:
    """Production transcription GUI window (Tkinter).

    Widgets (per Task 6 research — Buzz/WhisperDesktop patterns, not invented):
      * recording indicator (red ● REC label, shown during Recording stage)
      * stage label (bold, current phase)
      * audio level meter (symmetric RMS bars on Canvas, peak-hold decay)
      * elapsed timer (MM:SS, ticks every 1 s, resets on stage change)
      * model name label
      * progress bar + % label (driven by whisper.cpp progress callback)
      * speed (×realtime = audio_duration/elapsed, updated on progress)
      * segment count
      * live transcript log (auto-scroll, mirrors whisper.cpp stdout)
      * Cancel / Copy / Save… / Close buttons

    Thread-safe: worker thread calls stage/progress/segment/amplitude/... which
    only put() on a queue. The Tk mainloop (main thread) polls the queue via
    after() and updates widgets.
    """

    def __init__(self, *, title: str = "whisper-flow", notify: bool = False,
                 auto_close_ms: int = 0):
        self.title = title
        self.notify = notify
        self.auto_close_ms = auto_close_ms
        self._q: queue.Queue[_Msg] = queue.Queue()
        self._exc: Optional[BaseException] = None
        self._result: object = None
        self._cancel_cb: Optional[Callable[[], None]] = None
        self._start_cb: Optional[Callable[[], None]] = None

    # -- worker-thread API (only touches the queue) -------------------------

    def stage(self, name: str, detail: str = "") -> None:
        self._q.put(_Msg(_MSG_STAGE, name=name, detail=detail))

    def progress(self, percent: int, detail: str = "") -> None:
        self._q.put(_Msg(_MSG_PROGRESS, percent=int(percent), detail=detail))

    def segment(self, text: str, ts: str = "") -> None:
        self._q.put(_Msg(_MSG_SEGMENT, text=text, ts=ts))

    def amplitude(self, rms: float) -> None:
        self._q.put(_Msg(_MSG_AMPLITUDE, rms=float(rms)))

    def audio_info(self, duration_sec: float, model_name: str) -> None:
        self._q.put(_Msg(_MSG_AUDIO_INFO, duration=float(duration_sec or 0.0),
                         model=model_name or ""))

    def register_cancel(self, cb: Callable[[], None]) -> None:
        self._cancel_cb = cb

    def register_start(self, cb: Callable[[], None]) -> None:
        self._start_cb = cb

    def done(self, message: str = "") -> None:
        self._q.put(_Msg(_MSG_DONE, message=message or "complete"))
        if self.notify:
            desktop_notify("whisper-flow: done", message or "transcription complete",
                           icon="dialog-information")

    def error(self, message: str) -> None:
        self._q.put(_Msg(_MSG_ERROR, message=message))
        if self.notify:
            desktop_notify("whisper-flow: error", message, icon="dialog-error",
                           urgency="critical")

    # -- main-thread API ----------------------------------------------------

    def run(self, work: Callable[[], object]) -> object:
        """Spawn `work` in a worker thread, run the Tk mainloop, return work()'s result.

        Re-raises any exception thrown by `work` after the window closes.
        CancelledError is re-raised as-is so the CLI exits with code 130.
        """
        # Lazy import so the module loads even where Tkinter is absent.
        try:
            import tkinter as tk
            from tkinter import ttk
        except Exception:  # noqa: BLE001
            null = NullNotifier(notify=self.notify)
            null.stage("whisper-flow", "Tkinter unavailable — using console output")
            return null.run(work)
        global _TK
        _TK = tk

        worker = threading.Thread(target=self._worker, args=(work,), daemon=True)
        worker.start()

        root = tk.Tk()
        root.title(self.title)
        try:
            ttk.Style().theme_use("clam")
        except Exception:  # noqa: BLE001
            pass
        root.minsize(680, 480)
        try:
            root.geometry("720x540")
        except Exception:  # noqa: BLE001
            pass

        # --- state vars ---
        stage_var = tk.StringVar(value="Starting…")
        pct_var = tk.StringVar(value="")
        seg_count_var = tk.StringVar(value="0 segments")
        elapsed_var = tk.StringVar(value="00:00")
        speed_var = tk.StringVar(value="")
        model_var = tk.StringVar(value="")

        # --- header row: ● REC | stage | elapsed | model ---
        header = ttk.Frame(root, padding=(14, 12, 14, 4))
        header.pack(fill=tk.X)
        rec_lbl = tk.Label(header, text="● REC", fg="#b00020",
                           font=("TkDefaultFont", 10, "bold"))
        # packed/unpacked dynamically by _handle on stage change
        stage_label = ttk.Label(header, textvariable=stage_var,
                                font=("TkDefaultFont", 11, "bold"))
        stage_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(header, textvariable=elapsed_var,
                  font=("TkDefaultFont", 10)).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(header, textvariable=model_var, foreground="#666666",
                  font=("TkDefaultFont", 9)).pack(side=tk.LEFT, padx=(12, 0))

        # --- level meter row (Canvas) ---
        meter_frame = ttk.Frame(root, padding=(14, 2, 14, 6))
        meter_frame.pack(fill=tk.X)
        meter_canvas = tk.Canvas(meter_frame, width=660, height=22,
                                 highlightthickness=0, bg=root.cget("bg"))
        meter_canvas.pack(side=tk.LEFT, fill=tk.X, expand=True)
        meter = _LevelMeter(meter_canvas, width=660, height=22)

        # --- progress row: bar + % + speed + seg count ---
        prog_frame = ttk.Frame(root, padding=(14, 0, 14, 8))
        prog_frame.pack(fill=tk.X)
        bar = ttk.Progressbar(prog_frame, orient=tk.HORIZONTAL, mode="determinate",
                              maximum=100, value=0)
        bar.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(prog_frame, textvariable=pct_var, width=6).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(prog_frame, textvariable=speed_var, foreground="#666666",
                  width=10).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(prog_frame, textvariable=seg_count_var, foreground="#666666").pack(side=tk.LEFT)

        # --- live transcript log ---
        body = ttk.Frame(root, padding=(14, 2, 14, 8))
        body.pack(fill=tk.BOTH, expand=True)
        log = tk.Text(body, height=10, wrap=tk.WORD, state=tk.DISABLED,
                      relief="flat", background="#f6f6f6", font=("TkFixedFont", 9))
        log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(body, command=log.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        log.configure(yscrollcommand=sb.set)
        log.tag_configure("error", foreground="#b00020")
        log.tag_configure("info", foreground="#666666")

        # --- footer: Start | Stop | Cancel | Copy | Save… | Close ---
        footer = ttk.Frame(root, padding=(14, 0, 14, 10))
        footer.pack(fill=tk.X)
        start_btn = ttk.Button(footer, text="Start", state=tk.DISABLED,
                               command=lambda: self._on_start())
        start_btn.pack(side=tk.LEFT)
        stop_btn = tk.Button(footer, text="Stop", fg="#b00020", state=tk.DISABLED,
                             command=lambda: self._on_stop())
        stop_btn.pack(side=tk.LEFT, padx=(8, 0))
        cancel_btn = tk.Button(footer, text="Cancel", fg="#b00020",
                               command=lambda: self._on_cancel())
        cancel_btn.pack(side=tk.LEFT, padx=(8, 0))
        copy_btn = ttk.Button(footer, text="Copy", command=lambda: self._on_copy(root, log, copy_btn))
        copy_btn.pack(side=tk.LEFT, padx=(8, 0))
        save_btn = ttk.Button(footer, text="Save…",
                              command=lambda: self._on_save(root, log))
        save_btn.pack(side=tk.LEFT, padx=(8, 0))
        close_btn = ttk.Button(footer, text="Close", state=tk.DISABLED,
                               command=root.destroy)
        close_btn.pack(side=tk.RIGHT)

        # --- internal state ---
        st = {
            "segments": 0,
            "finished": False,
            "recording": False,
            "ready_to_record": False,
            "stage_t0": time.monotonic(),
            "audio_dur": 0.0,
            "transcript_lines": [],  # for copy/save
        }

        def _append_log(line: str, *, tag: str = "") -> None:
            log.configure(state=tk.NORMAL)
            if tag:
                log.insert(tk.END, line + "\n", tag)
            else:
                log.insert(tk.END, line + "\n")
            log.see(tk.END)
            log.configure(state=tk.DISABLED)
            st["transcript_lines"].append(line)

        def _fmt_elapsed(secs: float) -> str:
            secs = int(secs)
            if secs < 3600:
                return f"{secs // 60:02d}:{secs % 60:02d}"
            return f"{secs // 3600:02d}:{(secs % 3600) // 60:02d}:{secs % 60:02d}"

        def _tick() -> None:
            if st["finished"]:
                return
            elapsed = time.monotonic() - st["stage_t0"]
            elapsed_var.set(_fmt_elapsed(elapsed))
            # update speed (×realtime) if we have audio duration + progress
            if st["audio_dur"] > 0:
                pct = bar["value"]
                if pct > 0 and elapsed > 0:
                    processed = st["audio_dur"] * (pct / 100.0)
                    rtf = processed / elapsed
                    speed_var.set(f"{rtf:.1f}×")
            root.after(1000, _tick)

        def _poll() -> None:
            try:
                while True:
                    msg = self._q.get_nowait()
                    _handle(msg)
            except queue.Empty:
                pass
            if not st["finished"]:
                root.after(100, _poll)

        def _handle(msg: _Msg) -> None:
            if msg.kind == _MSG_STAGE:
                stage_var.set(msg.name + (f" — {msg.detail}" if msg.detail else ""))
                bar["value"] = 0
                pct_var.set("")
                st["stage_t0"] = time.monotonic()
                if msg.name == "Ready to record":
                    st["ready_to_record"] = True
                    st["recording"] = False
                    rec_lbl.pack_forget()
                    start_btn.configure(state=tk.NORMAL)
                    stop_btn.configure(state=tk.DISABLED)
                    cancel_btn.configure(state=tk.DISABLED)
                    _append_log("Ready. Click Start, speak freely, then click Stop to end and transcribe.", tag="info")
                    return
                # show ● REC only during Recording stage; hide otherwise
                if msg.name.lower().startswith("record"):
                    st["recording"] = True
                    st["ready_to_record"] = False
                    if not rec_lbl.winfo_ismapped():
                        rec_lbl.pack(side=tk.LEFT, padx=(0, 10), before=stage_label)
                    start_btn.configure(state=tk.DISABLED)
                    stop_btn.configure(state=tk.NORMAL)
                    cancel_btn.configure(state=tk.DISABLED)
                    _append_log("Recording... click Stop to finish and begin transcription.", tag="info")
                else:
                    st["recording"] = False
                    st["ready_to_record"] = False
                    rec_lbl.pack_forget()
                    start_btn.configure(state=tk.DISABLED)
                    stop_btn.configure(state=tk.DISABLED)
                    cancel_btn.configure(state=tk.NORMAL)
            elif msg.kind == _MSG_PROGRESS:
                p = msg.percent
                bar["value"] = max(0, min(100, p))
                pct_var.set(f"{p}%")
            elif msg.kind == _MSG_SEGMENT:
                st["segments"] += 1
                seg_count_var.set(f"{st['segments']} segment"
                                  + ("" if st["segments"] == 1 else "s"))
                line = f"[{msg.ts}]  {msg.text}" if msg.ts else msg.text
                _append_log(line)
            elif msg.kind == _MSG_AMPLITUDE:
                meter.update(msg.rms)
            elif msg.kind == _MSG_AUDIO_INFO:
                st["audio_dur"] = msg.duration
                if msg.model:
                    model_var.set(f"Model: {msg.model}")
            elif msg.kind == _MSG_DONE:
                st["finished"] = True
                bar["value"] = 100
                pct_var.set("100%")
                stage_var.set("Done ✓")
                seg_count_var.set(msg.message or seg_count_var.get())
                st["recording"] = False
                st["ready_to_record"] = False
                rec_lbl.pack_forget()
                start_btn.configure(state=tk.DISABLED)
                stop_btn.configure(state=tk.DISABLED)
                cancel_btn.configure(state=tk.DISABLED)
                close_btn.configure(state=tk.NORMAL)
                if self.auto_close_ms > 0:
                    root.after(self.auto_close_ms, root.destroy)
            elif msg.kind == _MSG_ERROR:
                st["finished"] = True
                stage_var.set("Error")
                st["recording"] = False
                st["ready_to_record"] = False
                rec_lbl.pack_forget()
                _append_log(f"ERROR: {msg.message}", tag="error")
                start_btn.configure(state=tk.DISABLED)
                stop_btn.configure(state=tk.DISABLED)
                cancel_btn.configure(state=tk.DISABLED)
                close_btn.configure(state=tk.NORMAL)

        def _on_window_close() -> None:
            # if work is still running, treat the X as a cancel
            if not st["finished"]:
                self._on_cancel()
            root.destroy()

        root.protocol("WM_DELETE_WINDOW", _on_window_close)

        if self.notify:
            desktop_notify("whisper-flow", "working…", icon="audio-input-microphone")

        root.after(100, _poll)
        root.after(1000, _tick)
        try:
            root.mainloop()
        finally:
            pass  # window closed; worker already joined below

        worker.join(timeout=1.0)
        if self._exc is not None:
            raise self._exc
        return self._result

    def _worker(self, work: Callable[[], object]) -> None:
        try:
            self._result = work()
        except BaseException as exc:  # noqa: BLE001 — capture for re-raise on main thread
            # KeyboardInterrupt and CancelledError propagate to the caller
            self._exc = exc
            try:
                from .errors import CancelledError
                if not isinstance(exc, CancelledError):
                    self.error(str(exc))
            except Exception:  # noqa: BLE001
                pass

    # -- button handlers (main thread) --------------------------------------

    def _on_cancel(self) -> None:
        if self._cancel_cb is not None:
            try:
                self._cancel_cb()
            except Exception:  # noqa: BLE001
                pass

    def _on_start(self) -> None:
        if self._start_cb is not None:
            try:
                self._start_cb()
            except Exception:  # noqa: BLE001
                pass

    def _on_stop(self) -> None:
        self._on_cancel()

    def _on_copy(self, root, log, btn) -> None:
        text = log.get("1.0", _TK.END)
        if not text.strip():
            btn.configure(text="Nothing to copy!")
            root.after(1500, lambda: btn.configure(text="Copy"))
            return
        root.clipboard_clear()
        root.clipboard_append(text)
        btn.configure(text="Copied!")
        root.after(2000, lambda: btn.configure(text="Copy"))

    def _on_save(self, root, log) -> None:
        from tkinter import filedialog
        text = log.get("1.0", _TK.END)
        if not text.strip():
            return
        path = filedialog.asksaveasfilename(
            title="Save transcript",
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("SRT", "*.srt"), ("VTT", "*.vtt"), ("JSON", "*.json")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text + ("\n" if not text.endswith("\n") else ""))
        except OSError as exc:
            try:
                self.error(f"save failed: {exc}")
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def _tk_available() -> bool:
    """True if a display is present AND tkinter imports cleanly."""
    if not os.environ.get("DISPLAY") and sys.platform.startswith("linux"):
        return False
    try:
        import tkinter  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def make_notifier(*, gui: bool = True, notify: bool = False,
                  title: str = "whisper-flow", verbose: bool = False) -> Notifier:
    """Build the best available notifier.

    gui=True (default) prefers a Tk window; falls back to NullNotifier when no
    display / Tkinter is available. gui=False forces NullNotifier (--no-gui).
    """
    if gui and _tk_available():
        return TkNotifier(title=title, notify=notify)
    return NullNotifier(notify=notify, verbose=verbose)
