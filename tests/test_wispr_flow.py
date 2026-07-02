import os
import pytest
from whisper_flow.snippets import expand_snippets
from whisper_flow.transforms import build_transform_prompt
from whisper_flow.history import save_dictation, load_history, search_history, get_stats
from whisper_flow.hotkeys import parse_hotkey
from whisper_flow.config import Config, load_config, save_config


def test_expand_snippets():
    snippets = {
        "my email": "parth@example.com",
        "br": "Best regards,\nParth",
        "my address": "123 Main St",
    }
    # Basic expansion
    text = "Please send it to my email when ready."
    assert expand_snippets(text, snippets) == "Please send it to parth@example.com when ready."

    # Case insensitive
    text = "MY EMAIL is down."
    assert expand_snippets(text, snippets) == "parth@example.com is down."

    # Multiple snippets
    text = "Here is my address. br"
    assert expand_snippets(text, snippets) == "Here is 123 Main St. Best regards,\nParth"

    # No match
    assert expand_snippets("Hello world", snippets) == "Hello world"


def test_build_transform_prompt():
    selected = "This is a very long and wordy sentence that goes on."
    
    # Built-in polish
    sys_p, usr_p = build_transform_prompt(selected, "please polish this")
    assert "clarity, conciseness" in sys_p
    assert selected in usr_p

    # Built-in concise
    sys_p, usr_p = build_transform_prompt(selected, "make it concise")
    assert "short and clear" in sys_p

    # Custom transform override
    custom = {
        "pirate": {
            "system": "Talk like a pirate.",
            "user": "Rewrite: {selected}",
        }
    }
    sys_p, usr_p = build_transform_prompt(selected, "pirate mode", custom_transforms=custom)
    assert sys_p == "Talk like a pirate."
    assert usr_p == f"Rewrite: {selected}"

    # Free-form instruction
    sys_p, usr_p = build_transform_prompt(selected, "translate to Spanish")
    assert "editing assistant" in sys_p
    assert "translate to Spanish" in usr_p


def test_history_crud(tmp_path):
    hdir = str(tmp_path)
    save_dictation(
        transcript="hello world",
        processed="Hello world.",
        mode="high",
        app_name="notepad.exe",
        app_category="other",
        duration_sec=2.5,
        history_dir=hdir,
    )
    save_dictation(
        transcript="send email",
        processed="Send email.",
        mode="light",
        app_name="outlook.exe",
        app_category="email",
        duration_sec=1.5,
        history_dir=hdir,
    )

    records = load_history(history_dir=hdir)
    assert len(records) == 2
    assert records[0]["transcript"] == "send email"  # most recent first
    assert records[1]["transcript"] == "hello world"

    matches = search_history("email", history_dir=hdir)
    assert len(matches) == 1
    assert matches[0]["app_category"] == "email"

    stats = get_stats(history_dir=hdir)
    assert stats["total_dictations"] == 2
    assert stats["total_duration_sec"] == 4.0


def test_parse_hotkey():
    mods, trig = parse_hotkey("ctrl+shift+space")
    assert mods == {"Key.ctrl_l", "Key.shift"}
    assert trig == "space"

    mods, trig = parse_hotkey("alt+t")
    assert mods == {"Key.alt_l"}
    assert trig == "t"


def test_config_save_and_load(tmp_path):
    cfg = Config()
    cfg.dictation_hotkey = "ctrl+alt+space"
    cfg.snippets = {"test": "123"}
    cfg.app_styles = {"email": {"mode": "formal"}}

    path = os.path.join(str(tmp_path), "test.toml")
    save_config(cfg, path)

    loaded = load_config(path)
    assert loaded.dictation_hotkey == "ctrl+alt+space"
    assert loaded.snippets == {"test": "123"}
    assert loaded.app_styles == {"email": {"mode": "formal"}}
