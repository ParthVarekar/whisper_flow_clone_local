"""System tray icon for whisper-flow daemon.

Uses `pystray` for a cross-platform system tray icon with right-click menu.
"""

from __future__ import annotations

import sys
import threading
from typing import Callable, Optional


def _create_icon_image(color: str = "#4ade80"):
    """Create a simple microphone-style icon using Pillow."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Simple mic shape: rounded rectangle body + stand
    # Parse color
    if color == "#4ade80":
        fill = (74, 222, 128, 255)  # green = idle
    elif color == "#ef4444":
        fill = (239, 68, 68, 255)   # red = recording
    elif color == "#facc15":
        fill = (250, 204, 21, 255)  # yellow = processing
    else:
        fill = (74, 222, 128, 255)

    # Mic body
    draw.rounded_rectangle([20, 8, 44, 38], radius=8, fill=fill)
    # Stand arc
    draw.arc([14, 24, 50, 48], start=0, end=180, fill=fill, width=3)
    # Stand stem
    draw.line([32, 48, 32, 56], fill=fill, width=3)
    # Stand base
    draw.line([22, 56, 42, 56], fill=fill, width=3)

    return img


class TrayIcon:
    """System tray icon for the whisper-flow daemon."""

    def __init__(
        self,
        *,
        on_quit: Optional[Callable[[], None]] = None,
        on_mode_change: Optional[Callable[[str], None]] = None,
        on_style_change: Optional[Callable[[str], None]] = None,
        on_open_settings: Optional[Callable[[], None]] = None,
        current_mode: str = "auto",
        current_style: str = "default",
    ):
        self._on_quit = on_quit
        self._on_mode_change = on_mode_change
        self._on_style_change = on_style_change
        self._on_open_settings = on_open_settings
        self._current_mode = current_mode
        self._current_style = current_style
        self._icon = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the tray icon in a background thread."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop and remove the tray icon."""
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:  # noqa: BLE001
                pass

    def set_state(self, state: str) -> None:
        """Update the tray icon color: 'idle', 'recording', 'processing'."""
        color_map = {
            "idle": "#4ade80",
            "recording": "#ef4444",
            "processing": "#facc15",
        }
        color = color_map.get(state, "#4ade80")
        img = _create_icon_image(color)
        if img is not None and self._icon is not None:
            try:
                self._icon.icon = img
            except Exception:  # noqa: BLE001
                pass

    def update_tooltip(self, text: str) -> None:
        if self._icon is not None:
            try:
                self._icon.title = text
            except Exception:  # noqa: BLE001
                pass

    def _run(self) -> None:
        try:
            import pystray
            from PIL import Image as PILImage
        except ImportError:
            sys.stderr.write(
                "[whisper-flow] pystray not installed; tray icon disabled. "
                "Install with: pip install pystray Pillow\n"
            )
            return

        img = _create_icon_image()
        if img is None:
            sys.stderr.write("[whisper-flow] Pillow not installed; tray icon disabled.\n")
            return

        modes = [
            "auto", "none", "light", "medium", "high",
            "smart_list", "email", "coding", "meeting_notes", "social",
        ]
        styles = [
            "default", "casual", "very_casual", "formal",
            "concise", "academic", "storytelling", "enthusiastic",
        ]

        def _make_mode_handler(m):
            def handler(icon, item):
                self._current_mode = m
                if self._on_mode_change:
                    self._on_mode_change(m)
            return handler

        def _make_style_handler(s):
            def handler(icon, item):
                self._current_style = s
                if self._on_style_change:
                    self._on_style_change(s)
            return handler

        def _mode_checked(m):
            def check(item):
                return self._current_mode == m
            return check

        def _style_checked(s):
            def check(item):
                return self._current_style == s
            return check

        mode_items = [
            pystray.MenuItem(
                m.replace("_", " ").title(),
                _make_mode_handler(m),
                checked=_mode_checked(m),
                radio=True,
            )
            for m in modes
        ]

        style_items = [
            pystray.MenuItem(
                s.replace("_", " ").title(),
                _make_style_handler(s),
                checked=_style_checked(s),
                radio=True,
            )
            for s in styles
        ]

        menu = pystray.Menu(
            pystray.MenuItem("Cleanup Level", pystray.Menu(*mode_items)),
            pystray.MenuItem("Writing Style", pystray.Menu(*style_items)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Hotkeys: Ctrl+Shift+Space (dictate) | Ctrl+Shift+T (transform)",
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._quit),
        )

        self._icon = pystray.Icon(
            "whisper-flow",
            img,
            title="whisper-flow (idle) — Ctrl+Shift+Space to dictate",
            menu=menu,
        )

        self._icon.run()

    def _quit(self, icon, item) -> None:
        if self._on_quit:
            self._on_quit()
        icon.stop()
