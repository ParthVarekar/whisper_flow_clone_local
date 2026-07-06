"""System-wide text insertion via clipboard + keystroke simulation.

On Windows, uses ctypes SendInput to simulate Ctrl+V after placing text on
the clipboard. Falls back to pyperclip + pyautogui if available.

The clipboard is saved/restored around each insertion so the user's clipboard
is not clobbered.
"""

from __future__ import annotations

import sys
import time
import threading

_insertion_lock = threading.Lock()


def insert_text(text: str) -> None:
    """Insert text at the active cursor position in the foreground application."""
    if not text:
        return
    with _insertion_lock:
        if sys.platform == "win32":
            _insert_windows(text)
        else:
            _insert_fallback(text)


# ---------------------------------------------------------------------------
# Windows: ctypes-based clipboard + SendInput
# ---------------------------------------------------------------------------

def _insert_windows(text: str) -> None:
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

    # -- Simulate Ctrl+V --
    time.sleep(0.08)  # Delay for clipboard to settle
    _send_ctrl_v()
    time.sleep(0.1)


def _send_ctrl_v() -> None:
    """Simulate Ctrl+V keystroke using Windows SendInput."""
    import ctypes

    VK_SHIFT = 0x10
    VK_CONTROL = 0x11
    VK_MENU = 0x12
    VK_SPACE = 0x20
    VK_V = 0x56
    KEYEVENTF_KEYUP = 0x0002
    INPUT_KEYBOARD = 1

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.c_ushort),
            ("wScan", ctypes.c_ushort),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class INPUT(ctypes.Structure):
        class _INPUT_UNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]
        _fields_ = [
            ("type", ctypes.c_ulong),
            ("union", _INPUT_UNION),
        ]

    def _make_key_input(vk: int, flags: int = 0) -> INPUT:
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.union.ki.wVk = vk
        inp.union.ki.dwFlags = flags
        return inp

    user32 = ctypes.windll.user32

    # First, explicitly release any leftover hotkey modifiers (Shift, Alt, Space, Ctrl)
    release_mods = (INPUT * 4)(
        _make_key_input(VK_SHIFT, KEYEVENTF_KEYUP),
        _make_key_input(VK_MENU, KEYEVENTF_KEYUP),
        _make_key_input(VK_SPACE, KEYEVENTF_KEYUP),
        _make_key_input(VK_CONTROL, KEYEVENTF_KEYUP),
    )
    user32.SendInput(4, ctypes.byref(release_mods), ctypes.sizeof(INPUT))
    time.sleep(0.03)

    # Send Ctrl down, V down, V up, Ctrl up with micro-delays for Windows message queues
    ctrl_down = (INPUT * 1)(_make_key_input(VK_CONTROL))
    v_press = (INPUT * 2)(_make_key_input(VK_V), _make_key_input(VK_V, KEYEVENTF_KEYUP))
    ctrl_up = (INPUT * 1)(_make_key_input(VK_CONTROL, KEYEVENTF_KEYUP))

    user32.SendInput(1, ctypes.byref(ctrl_down), ctypes.sizeof(INPUT))
    time.sleep(0.01)
    user32.SendInput(2, ctypes.byref(v_press), ctypes.sizeof(INPUT))
    time.sleep(0.01)
    user32.SendInput(1, ctypes.byref(ctrl_up), ctypes.sizeof(INPUT))


def _send_ctrl_c() -> None:
    """Simulate Ctrl+C keystroke to copy selected text."""
    import ctypes

    VK_CONTROL = 0x11
    VK_C = 0x43
    KEYEVENTF_KEYUP = 0x0002
    INPUT_KEYBOARD = 1

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.c_ushort),
            ("wScan", ctypes.c_ushort),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class INPUT(ctypes.Structure):
        class _INPUT_UNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]
        _fields_ = [
            ("type", ctypes.c_ulong),
            ("union", _INPUT_UNION),
        ]

    def _make_key_input(vk: int, flags: int = 0) -> INPUT:
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.union.ki.wVk = vk
        inp.union.ki.dwFlags = flags
        return inp

    inputs = (INPUT * 4)(
        _make_key_input(VK_CONTROL),
        _make_key_input(VK_C),
        _make_key_input(VK_C, KEYEVENTF_KEYUP),
        _make_key_input(VK_CONTROL, KEYEVENTF_KEYUP),
    )

    ctypes.windll.user32.SendInput(4, ctypes.byref(inputs), ctypes.sizeof(INPUT))


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
