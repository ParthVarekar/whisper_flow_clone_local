"""Dictation history — persistent log of all dictation sessions.

Stores each dictation result (timestamp, raw transcript, processed text,
active app, mode, duration) in a local JSON Lines file for later retrieval.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional


_DEFAULT_HISTORY_DIR = os.path.join(
    os.path.expanduser("~"), ".config", "whisper-flow"
)
_HISTORY_FILE = "dictation_history.jsonl"


def _history_path(history_dir: Optional[str] = None) -> str:
    d = history_dir or _DEFAULT_HISTORY_DIR
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, _HISTORY_FILE)


def save_dictation(
    *,
    transcript: str,
    processed: str = "",
    mode: str = "high",
    writing_style: str = "default",
    app_name: str = "",
    app_category: str = "",
    duration_sec: float = 0.0,
    was_transform: bool = False,
    history_dir: Optional[str] = None,
) -> None:
    """Append a dictation record to the history file."""
    record = {
        "timestamp": time.time(),
        "iso_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "transcript": transcript,
        "processed": processed,
        "mode": mode,
        "writing_style": writing_style,
        "app_name": app_name,
        "app_category": app_category,
        "duration_sec": round(duration_sec, 2),
        "was_transform": was_transform,
        "char_count": len(processed or transcript),
        "word_count": len((processed or transcript).split()),
    }
    path = _history_path(history_dir)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def load_history(
    *,
    limit: int = 100,
    history_dir: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Load the most recent `limit` dictation records."""
    path = _history_path(history_dir)
    if not os.path.isfile(path):
        return []
    records: List[Dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return []
    # Return most recent first, limited
    return records[-limit:][::-1]


def search_history(
    query: str,
    *,
    limit: int = 50,
    history_dir: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Search history for records containing `query` in transcript or processed text."""
    all_records = load_history(limit=10000, history_dir=history_dir)
    q = query.lower()
    matches = [
        r for r in all_records
        if q in r.get("transcript", "").lower() or q in r.get("processed", "").lower()
    ]
    return matches[:limit]


def get_stats(*, history_dir: Optional[str] = None) -> Dict[str, Any]:
    """Get aggregate stats from dictation history."""
    records = load_history(limit=100000, history_dir=history_dir)
    if not records:
        return {"total_dictations": 0, "total_words": 0, "total_duration_sec": 0}
    return {
        "total_dictations": len(records),
        "total_words": sum(r.get("word_count", 0) for r in records),
        "total_duration_sec": round(sum(r.get("duration_sec", 0) for r in records), 1),
        "total_transforms": sum(1 for r in records if r.get("was_transform")),
        "most_used_mode": max(
            set(r.get("mode", "high") for r in records),
            key=lambda m: sum(1 for r in records if r.get("mode") == m),
        ),
    }
