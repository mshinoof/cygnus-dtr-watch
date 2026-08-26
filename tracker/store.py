"""
Snapshot storage. Deliberately plain JSON files, not a database -- the whole
point is that this can live in a git repo alongside your other Cygnus tools,
so every change to KSEB's data is also a git commit you can `git log`.

    data/latest.json           most recent full snapshot
    data/history/2026-08-26.json   dated snapshots, one per run day
    data/changes.json          rolling change log, newest first
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

DATA_DIR = "data"
LATEST = os.path.join(DATA_DIR, "latest.json")
HISTORY_DIR = os.path.join(DATA_DIR, "history")
CHANGES = os.path.join(DATA_DIR, "changes.json")

MAX_CHANGES = 2000
MAX_HISTORY_FILES = 120


def now_ist() -> datetime:
    return datetime.now(IST)


def _ensure_dirs() -> None:
    os.makedirs(HISTORY_DIR, exist_ok=True)


def load_latest() -> tuple[list[dict], str | None]:
    """Returns (rows, captured_at). Empty list on first ever run."""
    if not os.path.exists(LATEST):
        return [], None
    with open(LATEST) as f:
        blob = json.load(f)
    return blob.get("rows", []), blob.get("captured_at")


def save_snapshot(rows: list[dict]) -> str:
    _ensure_dirs()
    ts = now_ist()
    blob = {
        "captured_at": ts.isoformat(),
        "source": "https://wss.kseb.in/selfservices/reCap",
        "count": len(rows),
        "rows": rows,
    }
    with open(LATEST, "w") as f:
        json.dump(blob, f, indent=1)
    dated = os.path.join(HISTORY_DIR, f"{ts:%Y-%m-%d}.json")
    with open(dated, "w") as f:
        json.dump(blob, f, indent=1)
    _prune_history()
    return blob["captured_at"]


def _prune_history() -> None:
    files = sorted(f for f in os.listdir(HISTORY_DIR) if f.endswith(".json"))
    for stale in files[:-MAX_HISTORY_FILES]:
        os.remove(os.path.join(HISTORY_DIR, stale))


def append_changes(changes: list[dict], captured_at: str) -> list[dict]:
    _ensure_dirs()
    existing = []
    if os.path.exists(CHANGES):
        with open(CHANGES) as f:
            existing = json.load(f).get("changes", [])
    stamped = [dict(c, detected_at=captured_at) for c in changes]
    merged = stamped + existing
    merged = merged[:MAX_CHANGES]
    with open(CHANGES, "w") as f:
        json.dump({"updated_at": captured_at, "changes": merged}, f, indent=1)
    return merged
