"""System-wide text insertion via clipboard + keystroke simulation.

On Windows, uses ctypes SendInput to simulate Ctrl+V after placing text on
the clipboard. Falls back to pyperclip + pyautogui if available.

NOTE: The clipboard is NOT saved/restored around insertion — the dictated
text remains on the clipboard for re-paste. This is intentional (matching
Wispr Flow behavior). If clipboard preservation is needed in the future,
add save/restore logic around the EmptyClipboard/SetClipboardData calls.
"""

from __future__ import annotations

import sys
import time
import threading

_insertion_lock = threading.Lock()


def insert_text(text: str, target_hwnd: int = 0) -> None:
    """Insert text at the active cursor position in the foreground application."""
    if not text:
        return
    with _insertion_lock:
        if sys.platform == "win32":
            _insert_windows(text, target_hwnd=target_hwnd)
        else:
            _insert_fallback(text)


# ---------------------------------------------------------------------------
# Windows: ctypes-based clipboard + SendInput
# ---------------------------------------------------------------------------

def _insert_windows(text: str, target_hwnd: int = 0) -> None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    # -- Set new text to clipboard (no save/restore — text stays for re-paste) --
    try:
        if not user32.OpenClipboard(0):
            # Retry once after a short delay
            time.sleep(0.05)
            if not user32.OpenClipboard(0):
                # Last resort: use pyperclip
                _insert_fallback(text)
                return

        user32.EmptyClipboard()

        # Allocate global memory for the string
        buf = (text + "\0").encode("utf-16-le")
        h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(buf))
        if not h_mem:
            user32.CloseClipboard()
            _insert_fallback(text)
            return

        p = kernel32.GlobalLock(h_mem)
        ctypes.memmove(p, buf, len(buf))
        kernel32.GlobalUnlock(h_mem)
        user32.SetClipboardData(CF_UNICODETEXT, h_mem)
        user32.CloseClipboard()
    except Exception:  # noqa: BLE001
        try:
            user32.CloseClipboard()
        except Exception:  # noqa: BLE001
            pass
        _insert_fallback(text)
        return

    # -- Restore target window focus if specified and not focused --
    if target_hwnd and user32.IsWindow(target_hwnd):
        fg = user32.GetForegroundWindow()
        if fg != target_hwnd:
            user32.SetForegroundWindow(target_hwnd)
            time.sleep(0.04)

    # -- Simulate Ctrl+V --
    time.sleep(0.08)  # Delay for clipboard to settle
    _send_ctrl_v()
    time.sleep(0.1)


def _send_ctrl_v() -> None:
    """Simulate Ctrl+V keystroke using Windows keybd_event."""
    import ctypes
    user32 = ctypes.windll.user32

    VK_SHIFT = 0x10
    VK_CONTROL = 0x11
    VK_MENU = 0x12
    VK_SPACE = 0x20
    VK_V = 0x56
    KEYEVENTF_KEYUP = 0x0002

    # Release leftover hotkey modifiers (Shift, Alt, Space, Ctrl)
    for vk in (VK_SHIFT, VK_MENU, VK_SPACE, VK_CONTROL):
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.02)

    # Send Ctrl down, V down, V up, Ctrl up
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    time.sleep(0.01)
    user32.keybd_event(VK_V, 0, 0, 0)
    time.sleep(0.01)
    user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.01)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


def _send_ctrl_c() -> None:
    """Simulate Ctrl+C keystroke to copy selected text."""
    import ctypes
    user32 = ctypes.windll.user32

    VK_SHIFT = 0x10
    VK_CONTROL = 0x11
    VK_MENU = 0x12
    VK_SPACE = 0x20
    VK_C = 0x43
    KEYEVENTF_KEYUP = 0x0002

    # Release leftover hotkey modifiers
    for vk in (VK_SHIFT, VK_MENU, VK_SPACE):
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.02)

    # Send Ctrl down, C down, C up, Ctrl up
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    time.sleep(0.01)
    user32.keybd_event(VK_C, 0, 0, 0)
    time.sleep(0.01)
    user32.keybd_event(VK_C, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.01)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


def get_selected_text() -> str:
    """Read currently selected text from the active app by simulating Ctrl+C."""
    if sys.platform != "win32":
        try:
            import pyperclip
            return pyperclip.paste()
        except Exception:  # noqa: BLE001
            return ""

    import ctypes

    CF_UNICODETEXT = 13
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # Save current clipboard
    old = None
    try:
        if user32.OpenClipboard(0):
            h = user32.GetClipboardData(CF_UNICODETEXT)
            if h:
                p = kernel32.GlobalLock(h)
                if p:
                    old = ctypes.wstring_at(p)
                    kernel32.GlobalUnlock(h)
            user32.CloseClipboard()
    except Exception:  # noqa: BLE001
        pass

    # Simulate Ctrl+C
    _send_ctrl_c()
    time.sleep(0.15)

    # Read new clipboard
    selected = ""
    try:
        if user32.OpenClipboard(0):
            h = user32.GetClipboardData(CF_UNICODETEXT)
            if h:
                p = kernel32.GlobalLock(h)
                if p:
                    selected = ctypes.wstring_at(p)
                    kernel32.GlobalUnlock(h)
            user32.CloseClipboard()
    except Exception:  # noqa: BLE001
        pass

    # Restore old clipboard if copy didn't change anything meaningful
    if old is not None and selected == old:
        selected = ""  # Nothing was selected

    return selected.strip()


# C2 FIX: alias for backward compat — daemon.py imports copy_selected_text
copy_selected_text = get_selected_text


# ---------------------------------------------------------------------------
# Fallback: pyperclip-based
# ---------------------------------------------------------------------------

def _insert_fallback(text: str) -> None:
    """Fallback insertion using pyperclip."""
    try:
        import pyperclip
        pyperclip.copy(text)
        # User must manually Ctrl+V
    except ImportError:
        # Absolute last resort: just print it
        sys.stderr.write(f"[whisper-flow] could not insert text; copied to stderr:\n{text}\n")
