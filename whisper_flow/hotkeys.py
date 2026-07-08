"""Global hotkey listener for push-to-talk dictation.

Provides:
  - Hold-to-record (push-to-talk): hold hotkey → record, release → transcribe + insert
  - Double-tap toggle: tap hotkey twice quickly → hands-free continuous listening
  - Command mode hotkey: separate key combo for transforms (select text + voice edit)

Uses `pynput` for cross-platform global keyboard hooks.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Key combo parsing
# ---------------------------------------------------------------------------

_MODIFIER_MAP = {
    "ctrl": "Key.ctrl_l",
    "ctrl_l": "Key.ctrl_l",
    "ctrl_r": "Key.ctrl_r",
    "shift": "Key.shift",
    "shift_l": "Key.shift",
    "shift_r": "Key.shift_r",
    "alt": "Key.alt_l",
    "alt_l": "Key.alt_l",
    "alt_r": "Key.alt_r",
    "cmd": "Key.cmd",
    "win": "Key.cmd",
    "super": "Key.cmd",
    "option": "Key.alt_l",
}


def parse_hotkey(combo_str: str) -> Tuple[frozenset, str]:
    """Parse a hotkey string like 'ctrl+shift+space' into (modifier_set, trigger_key).

    Returns (frozenset of pynput Key names, trigger key name).
    """
    parts = [p.strip().lower() for p in combo_str.split("+")]
    if not parts:
        raise ValueError(f"empty hotkey string: {combo_str!r}")
    trigger = parts[-1]
    modifiers = frozenset(_MODIFIER_MAP.get(p, p) for p in parts[:-1])
    return modifiers, trigger


def _key_to_name(key) -> str:
    """Convert a pynput key object to a comparable string name."""
    # Special keys: key.name exists (e.g. "ctrl_l", "shift", "space")
    name = getattr(key, "name", None)
    if name:
        return f"Key.{name}"
    # Character keys: key.char exists
    ch = getattr(key, "char", None)
    if ch:
        return ch.lower()
    # VK-based keys
    vk = getattr(key, "vk", None)
    if vk:
        return f"vk_{vk}"
    return str(key)


# ---------------------------------------------------------------------------
# HotkeyManager
# ---------------------------------------------------------------------------

class HotkeyManager:
    """Global hotkey listener that drives push-to-talk and command mode.

    Usage::

        mgr = HotkeyManager(
            dictation_hotkey="ctrl+shift+space",
            command_hotkey="ctrl+shift+t",
            on_dictation_start=start_recording,
            on_dictation_stop=stop_and_insert,
            on_hands_free_toggle=toggle_continuous,
            on_command_start=start_command_recording,
            on_command_stop=stop_command_and_transform,
        )
        mgr.start()   # non-blocking; runs listener in background thread
        mgr.join()     # block until stopped
    """

    def __init__(
        self,
        *,
        dictation_hotkey: str = "ctrl+shift+space",
        command_hotkey: str = "ctrl+shift+t",
        on_dictation_start: Optional[Callable[[], None]] = None,
        on_dictation_stop: Optional[Callable[[], None]] = None,
        on_hands_free_toggle: Optional[Callable[[], None]] = None,
        on_command_start: Optional[Callable[[], None]] = None,
        on_command_stop: Optional[Callable[[], None]] = None,
        double_tap_ms: int = 350,
    ):
        self._dict_mods, self._dict_trigger = parse_hotkey(dictation_hotkey)
        self._cmd_mods, self._cmd_trigger = parse_hotkey(command_hotkey)

        self._on_dict_start = on_dictation_start
        self._on_dict_stop = on_dictation_stop
        self._on_hands_free = on_hands_free_toggle
        self._on_cmd_start = on_command_start
        self._on_cmd_stop = on_command_stop

        self._double_tap_s = double_tap_ms / 1000.0
        self._pressed_keys: set[str] = set()
        self._listener = None
        self._running = False

        # State tracking
        self._dict_held = False
        self._cmd_held = False
        self._last_dict_tap: float = 0.0
        self._hands_free_active = False

    # -- public API ----------------------------------------------------------

    def start(self) -> None:
        """Start the global keyboard listener (non-blocking)."""
        try:
            from pynput import keyboard
        except ImportError as exc:
            raise RuntimeError(
                "pynput is required for global hotkeys; run `pip install pynput`"
            ) from exc

        self._running = True
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        """Stop the listener."""
        self._running = False
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:  # noqa: BLE001
                pass

    def join(self, timeout: Optional[float] = None) -> None:
        if self._listener is not None:
            self._listener.join(timeout=timeout)

    @property
    def is_recording(self) -> bool:
        return self._dict_held

    @property
    def is_hands_free(self) -> bool:
        return self._hands_free_active

    # -- internal ------------------------------------------------------------

    def _modifiers_held(self, required: frozenset) -> bool:
        """Check if all required modifier keys are currently pressed."""
        for mod in required:
            # Allow matching either side (e.g. Key.ctrl_l matches Key.ctrl_r too)
            base = mod.replace("_l", "").replace("_r", "")
            if mod not in self._pressed_keys:
                # Check if the base form or the other side is held
                found = any(
                    k == mod or k.replace("_l", "").replace("_r", "") == base
                    for k in self._pressed_keys
                )
                if not found:
                    return False
        return True

    def _on_press(self, key) -> None:
        if not self._running:
            return
        name = _key_to_name(key)
        self._pressed_keys.add(name)

        # C1 FIX: check trigger using both bare name and Key. prefix.
        # _key_to_name returns "t" for letter keys but "Key.space" for special keys.
        # The old code only checked f"Key.{trigger}" which never matched letters.
        dict_trigger_names = {self._dict_trigger, f"Key.{self._dict_trigger}"}
        cmd_trigger_names = {self._cmd_trigger, f"Key.{self._cmd_trigger}"}

        # -- Dictation hotkey press --
        if (
            not self._dict_held
            and not self._cmd_held
            and self._modifiers_held(self._dict_mods)
            and not self._pressed_keys.isdisjoint(dict_trigger_names)
        ):
            now = time.monotonic()
            gap = now - self._last_dict_tap
            self._last_dict_tap = now

            if gap < self._double_tap_s:
                # Double-tap → toggle hands-free
                self._hands_free_active = not self._hands_free_active
                if self._on_hands_free:
                    _safe_call(self._on_hands_free)
                return

            self._dict_held = True
            if self._on_dict_start:
                _safe_call(self._on_dict_start)

        # -- Command hotkey press --
        if (
            not self._cmd_held
            and not self._dict_held
            and self._modifiers_held(self._cmd_mods)
            and not self._pressed_keys.isdisjoint(cmd_trigger_names)
        ):
            self._cmd_held = True
            if self._on_cmd_start:
                _safe_call(self._on_cmd_start)

    def _on_release(self, key) -> None:
        if not self._running:
            return
        name = _key_to_name(key)
        self._pressed_keys.discard(name)

        # -- Dictation hotkey release --
        # C1 FIX: check both bare name and Key. prefix for release too
        dict_trigger_names = {self._dict_trigger, f"Key.{self._dict_trigger}"}
        if self._dict_held and (name in dict_trigger_names or not self._modifiers_held(self._dict_mods)):
            self._dict_held = False
            if self._on_dict_stop:
                _safe_call(self._on_dict_stop)

        # -- Command hotkey release --
        cmd_trigger_names = {self._cmd_trigger, f"Key.{self._cmd_trigger}"}
        if self._cmd_held and (name in cmd_trigger_names or not self._modifiers_held(self._cmd_mods)):
            self._cmd_held = False
            if self._on_cmd_stop:
                _safe_call(self._on_cmd_stop)


def _safe_call(fn: Callable[[], None]) -> None:
    """Call fn in a daemon thread so the keyboard listener isn't blocked."""
    t = threading.Thread(target=fn, daemon=True)
    t.start()
