"""GUI / headless progress notifier for whisper-flow."""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable, Optional, Protocol

_TK = None


class Notifier(Protocol):
    def stage(self, name: str, detail: str = "") -> None: ...
    def progress(self, percent: int, detail: str = "") -> None: ...
    def segment(self, text: str, ts: str = "") -> None: ...
    def preview(self, text: str) -> None: ...
    def amplitude(self, rms: float) -> None: ...
    def audio_info(self, duration_sec: float, model_name: str) -> None: ...
    def register_cancel(self, cb: Callable[[], None]) -> None: ...
    def register_start(self, cb: Callable[[], None]) -> None: ...
    def result(self, kind: str, text: str) -> None: ...
    def get_selected_mode(self) -> str: ...
    def get_selected_mic(self) -> str: ...
    def get_selected_writing_style(self) -> str: ...
    def done(self, message: str = "") -> None: ...
    def error(self, message: str) -> None: ...
    def run(self, work: Callable[[], object]) -> object: ...


def desktop_notify(title: str, body: str = "", *, icon: str = "audio-input-microphone",
                   urgency: str = "normal") -> None:
    bin_path = shutil.which("notify-send")
    if bin_path is None:
        return
    try:
        subprocess.run(
            [bin_path, "-a", "whisper-flow", "-u", urgency, "-i", icon, title, body],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
    except Exception:  # noqa: BLE001
        pass


class NullNotifier:
    def __init__(self, *, notify: bool = False, verbose: bool = False,
                 initial_mode: str = "summarize"):
        self.notify = notify
        self.verbose = verbose
        self.initial_mode = initial_mode
        self._cancel_cb: Optional[Callable[[], None]] = None
        self._start_cb: Optional[Callable[[], None]] = None

    def _emit(self, tag: str, msg: str) -> None:
        sys.stderr.write(f"[whisper-flow] {tag}: {msg}\n")
        sys.stderr.flush()

    def stage(self, name: str, detail: str = "") -> None:
        self._emit("stage", f"{name}{(' - ' + detail) if detail else ''}")
        if self.notify:
            desktop_notify("whisper-flow", name)

    def progress(self, percent: int, detail: str = "") -> None:
        if self.verbose or percent % 10 == 0 or percent >= 100:
            self._emit("progress", f"{percent}%{(' - ' + detail) if detail else ''}")

    def segment(self, text: str, ts: str = "") -> None:
        if self.verbose:
            self._emit("segment", text if not ts else f"[{ts}] {text}")

    def preview(self, text: str) -> None:
        if self.verbose and text:
            self._emit("preview", text)

    def amplitude(self, rms: float) -> None:
        pass

    def audio_info(self, duration_sec: float, model_name: str) -> None:
        if self.verbose and (duration_sec or model_name):
            self._emit("audio", f"dur={duration_sec:.1f}s model={model_name}")

    def register_cancel(self, cb: Callable[[], None]) -> None:
        self._cancel_cb = cb

    def register_start(self, cb: Callable[[], None]) -> None:
        self._start_cb = cb
        cb()

    def result(self, kind: str, text: str) -> None:
        if self.verbose and text:
            self._emit(kind, text)

    def get_selected_mode(self) -> str:
        return ""

    def get_selected_mic(self) -> str:
        return ""

    def get_selected_writing_style(self) -> str:
        return ""

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


_MSG_STAGE = "stage"
_MSG_PROGRESS = "progress"
_MSG_SEGMENT = "segment"
_MSG_PREVIEW = "preview"
_MSG_AMPLITUDE = "amplitude"
_MSG_AUDIO_INFO = "audio_info"
_MSG_RESULT = "result"
_MSG_DONE = "done"
_MSG_ERROR = "error"


class _Msg:
    __slots__ = ("kind", "name", "percent", "detail", "text", "ts", "message",
                 "rms", "duration", "model", "result_kind")

    def __init__(self, kind: str, **kw):
        self.kind = kind
        self.name = kw.get("name", "")
        self.percent = int(kw.get("percent", 0) or 0)
        self.detail = kw.get("detail", "")
        self.text = kw.get("text", "")
        self.ts = kw.get("ts", "")
        self.message = kw.get("message", "")
        self.rms = float(kw.get("rms", 0.0) or 0.0)
        self.duration = float(kw.get("duration", 0.0) or 0.0)
        self.model = kw.get("model", "")
        self.result_kind = kw.get("result_kind", "")


class _LevelMeter:
    def __init__(self, canvas, *, width: int = 380, height: int = 28,
                 bar_width: int = 2, bar_margin: int = 1):
        self.canvas = canvas
        self.width = width
        self.height = height
        self.bar_width = bar_width
        self.bar_margin = bar_margin
        self.current = 0.0
        self._decay = 0.95
        self._scale = 10.0
        self._min_amp = 0.00005

    def update(self, rms: float) -> None:
        self.current = max(rms, self.current * self._decay)
        self._redraw()

    def _redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        amp = max(self.current, self._min_amp) * self._scale
        amp = min(amp, 1.0)
        n_bars_half = (self.width // 2) // (self.bar_width + self.bar_margin)
        active_half = int(n_bars_half * amp)
        cx = self.width // 2
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
    def __init__(self, *, title: str = "whisper-flow", notify: bool = False,
                 auto_close_ms: int = 0, initial_mode: str = "summarize"):
        self.title = title
        self.notify = notify
        self.auto_close_ms = auto_close_ms
        self.initial_mode = initial_mode
        self._selected_mode = initial_mode
        self._q: queue.Queue[_Msg] = queue.Queue()
        self._exc: Optional[BaseException] = None
        self._result: object = None
        self._cancel_cb: Optional[Callable[[], None]] = None
        self._start_cb: Optional[Callable[[], None]] = None

    def stage(self, name: str, detail: str = "") -> None:
        self._q.put(_Msg(_MSG_STAGE, name=name, detail=detail))

    def progress(self, percent: int, detail: str = "") -> None:
        self._q.put(_Msg(_MSG_PROGRESS, percent=int(percent), detail=detail))

    def segment(self, text: str, ts: str = "") -> None:
        self._q.put(_Msg(_MSG_SEGMENT, text=text, ts=ts))

    def preview(self, text: str) -> None:
        self._q.put(_Msg(_MSG_PREVIEW, text=text))

    def amplitude(self, rms: float) -> None:
        self._q.put(_Msg(_MSG_AMPLITUDE, rms=float(rms)))

    def audio_info(self, duration_sec: float, model_name: str) -> None:
        self._q.put(_Msg(_MSG_AUDIO_INFO, duration=float(duration_sec or 0.0),
                         model=model_name or ""))

    def register_cancel(self, cb: Callable[[], None]) -> None:
        self._cancel_cb = cb

    def register_start(self, cb: Callable[[], None]) -> None:
        self._start_cb = cb

    def result(self, kind: str, text: str) -> None:
        self._q.put(_Msg(_MSG_RESULT, result_kind=kind, text=text))

    def get_selected_mode(self) -> str:
        return self._selected_mode

    def get_selected_mic(self) -> str:
        return getattr(self, "_selected_mic", "default")

    def get_selected_writing_style(self) -> str:
        return getattr(self, "_selected_writing_style", "default")

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

    def run(self, work: Callable[[], object]) -> object:
        try:
            import tkinter as tk
            from tkinter import ttk
        except Exception:  # noqa: BLE001
            null = NullNotifier(notify=self.notify, initial_mode=self.initial_mode)
            null.stage("whisper-flow", "Tkinter unavailable - using console output")
            return null.run(work)
        global _TK
        _TK = tk
        from .audio import list_sounddevice_input_devices

        worker = threading.Thread(target=self._worker, args=(work,), daemon=True)
        worker.start()

        root = tk.Tk()
        root.title(self.title)
        try:
            ttk.Style().theme_use("clam")
        except Exception:  # noqa: BLE001
            pass
        root.minsize(860, 620)
        try:
            root.geometry("960x720")
        except Exception:  # noqa: BLE001
            pass

        mic_options = list_sounddevice_input_devices()
        self._selected_mic = mic_options[0][0] if mic_options else "default"
        self._selected_writing_style = "default"

        stage_var = tk.StringVar(value="Starting...")
        pct_var = tk.StringVar(value="")
        seg_count_var = tk.StringVar(value="0 segments")
        elapsed_var = tk.StringVar(value="00:00")
        speed_var = tk.StringVar(value="")
        model_var = tk.StringVar(value="")
        preview_var = tk.StringVar(value="")
        mode_var = tk.StringVar(value=self.initial_mode)
        status_var = tk.StringVar(value="Preparing the session.")

        header = ttk.Frame(root, padding=(16, 14, 16, 8))
        header.pack(fill=tk.X)
        top_row = ttk.Frame(header)
        top_row.pack(fill=tk.X)
        rec_lbl = tk.Label(top_row, text="REC", fg="#b00020",
                           font=("TkDefaultFont", 10, "bold"))
        stage_label = ttk.Label(top_row, textvariable=stage_var,
                                font=("TkDefaultFont", 12, "bold"))
        stage_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(top_row, textvariable=elapsed_var).pack(side=tk.RIGHT)
        ttk.Label(top_row, textvariable=model_var, foreground="#666666").pack(
            side=tk.RIGHT, padx=(0, 12)
        )
        ttk.Label(header, textvariable=status_var, foreground="#666666").pack(fill=tk.X, pady=(6, 0))

        controls = ttk.Frame(root, padding=(16, 0, 16, 8))
        controls.pack(fill=tk.X)
        ttk.Label(controls, text="Mode").pack(side=tk.LEFT)
        mode_combo = ttk.Combobox(
            controls,
            width=14,
            state="readonly",
            textvariable=mode_var,
            values=("none", "light", "medium", "high", "summarize", "command", "assistant"),
        )
        mode_combo.pack(side=tk.LEFT, padx=(8, 16))
        ttk.Label(controls, text="Mic").pack(side=tk.LEFT)
        mic_labels = [label for _spec, label in mic_options]
        mic_by_label = {label: spec for spec, label in mic_options}
        mic_var = tk.StringVar(value=mic_labels[0] if mic_labels else "System default")
        mic_combo = ttk.Combobox(
            controls,
            width=36,
            state="readonly",
            textvariable=mic_var,
            values=mic_labels,
        )
        mic_combo.pack(side=tk.LEFT, padx=(8, 16))
        ttk.Label(controls, text="Style").pack(side=tk.LEFT)
        style_var = tk.StringVar(value="default")
        style_combo = ttk.Combobox(
            controls,
            width=12,
            state="readonly",
            textvariable=style_var,
            values=("default", "casual", "very_casual", "formal"),
        )
        style_combo.pack(side=tk.LEFT, padx=(8, 16))
        ttk.Label(controls, textvariable=seg_count_var, foreground="#666666").pack(side=tk.LEFT)
        ttk.Label(controls, textvariable=speed_var, foreground="#666666").pack(side=tk.RIGHT)
        ttk.Label(controls, textvariable=pct_var, foreground="#666666").pack(side=tk.RIGHT, padx=(0, 10))

        meter_frame = ttk.Frame(root, padding=(16, 0, 16, 8))
        meter_frame.pack(fill=tk.X)
        meter_canvas = tk.Canvas(meter_frame, width=820, height=22,
                                 highlightthickness=0, bg=root.cget("bg"))
        meter_canvas.pack(fill=tk.X, expand=True)
        meter = _LevelMeter(meter_canvas, width=820, height=22)

        prog_frame = ttk.Frame(root, padding=(16, 0, 16, 10))
        prog_frame.pack(fill=tk.X)
        bar = ttk.Progressbar(prog_frame, orient=tk.HORIZONTAL, mode="determinate",
                              maximum=100, value=0)
        bar.pack(fill=tk.X, expand=True)

        body = ttk.PanedWindow(root, orient=tk.VERTICAL)
        body.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 10))

        transcript_frame = ttk.Frame(body)
        transcript_header = ttk.Frame(transcript_frame)
        transcript_header.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(transcript_header, text="Live Transcript",
                  font=("TkDefaultFont", 10, "bold")).pack(side=tk.LEFT)
        ttk.Label(transcript_header, textvariable=preview_var,
                  foreground="#666666").pack(side=tk.RIGHT)
        transcript = tk.Text(
            transcript_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            relief="flat",
            background="#f6f6f6",
            font=("TkDefaultFont", 10),
        )
        transcript.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        transcript_sb = ttk.Scrollbar(transcript_frame, command=transcript.yview)
        transcript_sb.pack(side=tk.RIGHT, fill=tk.Y)
        transcript.configure(yscrollcommand=transcript_sb.set)
        transcript.tag_configure("error", foreground="#b00020")
        transcript.tag_configure("info", foreground="#666666")

        output_frame = ttk.Frame(body)
        output_header = ttk.Frame(output_frame)
        output_header.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(output_header, text="Output",
                  font=("TkDefaultFont", 10, "bold")).pack(side=tk.LEFT)
        output = tk.Text(
            output_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            relief="flat",
            background="#f6f6f6",
            font=("TkDefaultFont", 10),
        )
        output.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        output_sb = ttk.Scrollbar(output_frame, command=output.yview)
        output_sb.pack(side=tk.RIGHT, fill=tk.Y)
        output.configure(yscrollcommand=output_sb.set)

        body.add(transcript_frame, weight=3)
        body.add(output_frame, weight=2)

        footer = ttk.Frame(root, padding=(16, 0, 16, 14))
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
        copy_btn = ttk.Button(
            footer,
            text="Copy",
            command=lambda: self._on_copy(root, transcript, output, copy_btn),
        )
        copy_btn.pack(side=tk.LEFT, padx=(8, 0))
        save_btn = ttk.Button(footer, text="Save...",
                              command=lambda: self._on_save(root, transcript, output))
        save_btn.pack(side=tk.LEFT, padx=(8, 0))
        close_btn = ttk.Button(footer, text="Close", state=tk.DISABLED,
                               command=root.destroy)
        close_btn.pack(side=tk.RIGHT)

        st = {
            "segments": 0,
            "finished": False,
            "recording": False,
            "ready_to_record": False,
            "stage_t0": time.monotonic(),
            "audio_dur": 0.0,
        }

        def _append_transcript(line: str, *, tag: str = "") -> None:
            transcript.configure(state=tk.NORMAL)
            if tag:
                transcript.insert(tk.END, line + "\n", tag)
            else:
                transcript.insert(tk.END, line + "\n")
            transcript.see(tk.END)
            transcript.configure(state=tk.DISABLED)

        def _replace_text(widget, text: str) -> None:
            widget.configure(state=tk.NORMAL)
            widget.delete("1.0", tk.END)
            widget.insert(tk.END, text.strip())
            widget.configure(state=tk.DISABLED)

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
            if st["audio_dur"] > 0:
                pct = bar["value"]
                if pct > 0 and elapsed > 0:
                    speed_var.set(f"{(st['audio_dur'] * (pct / 100.0)) / elapsed:.1f}x realtime")
                elif st["recording"]:
                    speed_var.set(f"{st['audio_dur']:.1f}s captured")
            root.after(1000, _tick)

        def _poll() -> None:
            try:
                while True:
                    _handle(self._q.get_nowait())
            except queue.Empty:
                pass
            if not st["finished"]:
                root.after(100, _poll)

        def _handle(msg: _Msg) -> None:
            self._selected_mode = mode_var.get()
            self._selected_mic = mic_by_label.get(mic_var.get(), "default")
            self._selected_writing_style = style_var.get()
            if msg.kind == _MSG_STAGE:
                stage_var.set(msg.name + (f" - {msg.detail}" if msg.detail else ""))
                bar["value"] = 0
                pct_var.set("")
                st["stage_t0"] = time.monotonic()
                if msg.name == "Ready to record":
                    st["ready_to_record"] = True
                    st["recording"] = False
                    rec_lbl.pack_forget()
                    start_btn.configure(state=tk.NORMAL)
                    stop_btn.configure(state=tk.DISABLED)
                    cancel_btn.configure(state=tk.NORMAL)
                    close_btn.configure(state=tk.DISABLED)
                    mode_combo.configure(state="readonly")
                    mic_combo.configure(state="readonly")
                    style_combo.configure(state="readonly")
                    status_var.set("Click Start, speak normally, then click Stop when you are done.")
                    preview_var.set("")
                    _append_transcript("Ready. Click Start when you want to begin dictating.", tag="info")
                    return
                if msg.name.lower().startswith("record"):
                    st["recording"] = True
                    st["ready_to_record"] = False
                    if not rec_lbl.winfo_ismapped():
                        rec_lbl.pack(side=tk.LEFT, padx=(0, 10), before=stage_label)
                    start_btn.configure(state=tk.DISABLED)
                    stop_btn.configure(state=tk.NORMAL)
                    cancel_btn.configure(state=tk.DISABLED)
                    mode_combo.configure(state=tk.DISABLED)
                    mic_combo.configure(state=tk.DISABLED)
                    style_combo.configure(state=tk.DISABLED)
                    status_var.set(f"Listening live on {mic_var.get()}.")
                else:
                    st["recording"] = False
                    st["ready_to_record"] = False
                    rec_lbl.pack_forget()
                    start_btn.configure(state=tk.DISABLED)
                    stop_btn.configure(state=tk.DISABLED)
                    cancel_btn.configure(state=tk.NORMAL)
                    mode_combo.configure(state=tk.DISABLED)
                    mic_combo.configure(state=tk.DISABLED)
                    style_combo.configure(state=tk.DISABLED)
                    if msg.name.lower().startswith("transcrib"):
                        status_var.set("Finalizing the transcript.")
                    elif msg.name.lower().startswith("llm"):
                        status_var.set(f"Applying {mode_var.get()} mode.")
                    else:
                        status_var.set(msg.detail or msg.name)
            elif msg.kind == _MSG_PROGRESS:
                bar["value"] = max(0, min(100, msg.percent))
                pct_var.set(f"{msg.percent}%")
            elif msg.kind == _MSG_SEGMENT:
                st["segments"] += 1
                seg_count_var.set(f"{st['segments']} segment" + ("" if st["segments"] == 1 else "s"))
                _append_transcript(msg.text)
            elif msg.kind == _MSG_PREVIEW:
                preview_var.set(msg.text)
            elif msg.kind == _MSG_AMPLITUDE:
                meter.update(msg.rms)
            elif msg.kind == _MSG_AUDIO_INFO:
                st["audio_dur"] = msg.duration
                if msg.model:
                    model_var.set(f"Model: {msg.model}")
            elif msg.kind == _MSG_RESULT:
                if msg.result_kind == "transcript" and msg.text.strip():
                    _replace_text(transcript, msg.text)
                elif msg.result_kind == "processed":
                    _replace_text(output, msg.text)
            elif msg.kind == _MSG_DONE:
                st["finished"] = True
                bar["value"] = 100
                pct_var.set("100%")
                stage_var.set("Done")
                status_var.set(msg.message or "Complete.")
                preview_var.set("")
                rec_lbl.pack_forget()
                start_btn.configure(state=tk.DISABLED)
                stop_btn.configure(state=tk.DISABLED)
                cancel_btn.configure(state=tk.DISABLED)
                close_btn.configure(state=tk.NORMAL)
                mode_combo.configure(state=tk.DISABLED)
                mic_combo.configure(state=tk.DISABLED)
                style_combo.configure(state=tk.DISABLED)
                if self.auto_close_ms > 0:
                    root.after(self.auto_close_ms, root.destroy)
            elif msg.kind == _MSG_ERROR:
                st["finished"] = True
                stage_var.set("Error")
                status_var.set(msg.message)
                preview_var.set("")
                rec_lbl.pack_forget()
                _append_transcript(f"ERROR: {msg.message}", tag="error")
                start_btn.configure(state=tk.DISABLED)
                stop_btn.configure(state=tk.DISABLED)
                cancel_btn.configure(state=tk.DISABLED)
                close_btn.configure(state=tk.NORMAL)
                mode_combo.configure(state=tk.DISABLED)
                mic_combo.configure(state=tk.DISABLED)
                style_combo.configure(state=tk.DISABLED)

        def _on_window_close() -> None:
            if not st["finished"]:
                self._on_cancel()
            root.destroy()

        root.protocol("WM_DELETE_WINDOW", _on_window_close)

        if self.notify:
            desktop_notify("whisper-flow", "working...", icon="audio-input-microphone")

        root.after(100, _poll)
        root.after(1000, _tick)
        root.mainloop()

        worker.join(timeout=1.0)
        if self._exc is not None:
            raise self._exc
        return self._result

    def _worker(self, work: Callable[[], object]) -> None:
        try:
            self._result = work()
        except BaseException as exc:  # noqa: BLE001
            self._exc = exc
            try:
                from .errors import CancelledError
                if not isinstance(exc, CancelledError):
                    self.error(str(exc))
            except Exception:  # noqa: BLE001
                pass

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

    def _on_copy(self, root, transcript, output, btn) -> None:
        text = output.get("1.0", _TK.END).strip() or transcript.get("1.0", _TK.END).strip()
        if not text:
            btn.configure(text="Nothing to copy")
            root.after(1500, lambda: btn.configure(text="Copy"))
            return
        root.clipboard_clear()
        root.clipboard_append(text)
        btn.configure(text="Copied")
        root.after(2000, lambda: btn.configure(text="Copy"))

    def _on_save(self, root, transcript, output) -> None:
        from tkinter import filedialog

        transcript_text = transcript.get("1.0", _TK.END).strip()
        output_text = output.get("1.0", _TK.END).strip()
        text = transcript_text
        if output_text:
            text = f"Transcript\n\n{transcript_text}\n\nOutput\n\n{output_text}".strip()
        if not text:
            return
        path = filedialog.asksaveasfilename(
            title="Save transcript",
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("JSON", "*.json")],
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


def _tk_available() -> bool:
    if not os.environ.get("DISPLAY") and sys.platform.startswith("linux"):
        return False
    try:
        import tkinter  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def make_notifier(*, gui: bool = True, notify: bool = False,
                  title: str = "whisper-flow", verbose: bool = False,
                  initial_mode: str = "summarize") -> Notifier:
    if gui and _tk_available():
        return TkNotifier(title=title, notify=notify, initial_mode=initial_mode)
    return NullNotifier(notify=notify, verbose=verbose, initial_mode=initial_mode)
