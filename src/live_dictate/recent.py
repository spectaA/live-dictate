"""Keeps the last dictations on disk, so a failed paste never loses what was said."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

KEEP = 10


def default_recent_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "live-dictate" / "recent.json"


def load(path: Path) -> list[dict[str, Any]]:
    """Returns the saved dictations, newest first; a missing or corrupted file reads as empty."""
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict) and entry.get("text")]


def _write(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    # Dictations are private: the file must never be created world-readable.
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(entries, handle, ensure_ascii=False, indent=1)
    temporary.replace(path)


def add(path: Path, text: str, outcome: str, now: datetime | None = None) -> str:
    """Saves a dictation at the top of the list and returns its id."""
    moment = now or datetime.now(timezone.utc)
    entry_id = moment.isoformat(timespec="microseconds")
    entry = {"id": entry_id, "time": entry_id, "outcome": outcome, "raw": text, "text": text}
    _write(path, [entry, *load(path)][:KEEP])
    return entry_id


def update(path: Path, entry_id: str, **values: str) -> None:
    entries = load(path)
    for entry in entries:
        if entry.get("id") == entry_id:
            entry.update(values)
            _write(path, entries)
            return


def clear(path: Path) -> None:
    path.unlink(missing_ok=True)
