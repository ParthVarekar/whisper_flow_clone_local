"""Detect the active foreground window and map it to an app category.

Used to apply per-app-category formatting styles (messaging vs email vs code).
Windows only; returns a safe fallback on other platforms.
"""

from __future__ import annotations

import sys
from typing import Optional, Tuple

# App category constants
CAT_MESSAGING = "messaging"
CAT_WORK_MESSAGING = "work_messaging"
CAT_EMAIL = "email"
CAT_CODE = "code"
CAT_TERMINAL = "terminal"
CAT_BROWSER = "browser"
CAT_OTHER = "other"

# Process name → category mapping
_PROCESS_CATEGORIES = {
    # Messaging
    "whatsapp.exe": CAT_MESSAGING,
    "telegram.exe": CAT_MESSAGING,
    "signal.exe": CAT_MESSAGING,
    "discord.exe": CAT_MESSAGING,
    "messenger.exe": CAT_MESSAGING,
    # Work messaging
    "slack.exe": CAT_WORK_MESSAGING,
    "teams.exe": CAT_WORK_MESSAGING,
    "ms-teams.exe": CAT_WORK_MESSAGING,
    "msteams.exe": CAT_WORK_MESSAGING,
    # Email
    "outlook.exe": CAT_EMAIL,
    "thunderbird.exe": CAT_EMAIL,
    # Code editors
    "code.exe": CAT_CODE,
    "cursor.exe": CAT_CODE,
    "windsurf.exe": CAT_CODE,
    "devenv.exe": CAT_CODE,
    "idea64.exe": CAT_CODE,
    "pycharm64.exe": CAT_CODE,
    "sublime_text.exe": CAT_CODE,
    "notepad++.exe": CAT_CODE,
    # Terminals
    "windowsterminal.exe": CAT_TERMINAL,
    "cmd.exe": CAT_TERMINAL,
    "powershell.exe": CAT_TERMINAL,
    "pwsh.exe": CAT_TERMINAL,
    "wt.exe": CAT_TERMINAL,
    "conhost.exe": CAT_TERMINAL,
    # Browsers
    "chrome.exe": CAT_BROWSER,
    "msedge.exe": CAT_BROWSER,
    "firefox.exe": CAT_BROWSER,
    "brave.exe": CAT_BROWSER,
    "opera.exe": CAT_BROWSER,
    "arc.exe": CAT_BROWSER,
}

# Window title substring → category (for browser-based apps)
_TITLE_OVERRIDES = [
    ("slack", CAT_WORK_MESSAGING),
    ("teams", CAT_WORK_MESSAGING),
    ("gmail", CAT_EMAIL),
    ("outlook", CAT_EMAIL),
    ("mail", CAT_EMAIL),
    ("whatsapp", CAT_MESSAGING),
    ("telegram", CAT_MESSAGING),
    ("discord", CAT_MESSAGING),
    ("github", CAT_CODE),
    ("vs code", CAT_CODE),
    ("cursor", CAT_CODE),
]


def get_active_window_info() -> Tuple[str, str]:
    """Return (process_name, window_title) of the foreground window.

    Returns ("", "") on non-Windows or on failure.
    """
    if sys.platform != "win32":
        return "", ""

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return "", ""

        # Window title
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value

        # Process name
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        proc_name = ""
        if h_proc:
            try:
                import os
                name_buf = ctypes.create_unicode_buffer(260)
                size = wintypes.DWORD(260)
                # QueryFullProcessImageNameW
                if kernel32.QueryFullProcessImageNameW(h_proc, 0, name_buf, ctypes.byref(size)):
                    proc_name = os.path.basename(name_buf.value).lower()
            finally:
                kernel32.CloseHandle(h_proc)

        return proc_name, title
    except Exception:  # noqa: BLE001
        return "", ""


def detect_app_category() -> str:
    """Detect the foreground app and return its category string."""
    proc_name, title = get_active_window_info()

    # Direct process name match
    if proc_name and proc_name in _PROCESS_CATEGORIES:
        cat = _PROCESS_CATEGORIES[proc_name]
        # For browsers, check title for web app overrides
        if cat == CAT_BROWSER and title:
            title_lower = title.lower()
            for keyword, override_cat in _TITLE_OVERRIDES:
                if keyword in title_lower:
                    return override_cat
        return cat

    # Title-based fallback
    if title:
        title_lower = title.lower()
        for keyword, cat in _TITLE_OVERRIDES:
            if keyword in title_lower:
                return cat

    return CAT_OTHER
