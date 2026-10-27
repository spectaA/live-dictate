from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .cleanup import CleanupResult
from .config import Config
from .textflow import removed_word_indices

# USD, from OpenAI's pricing page in October 2026. Raw usage is stored too, so costs can be
# recomputed if prices change.
TRANSCRIPTION_PER_MINUTE = {
    "gpt-live-transcribe": 0.017,
    "gpt-realtime-whisper": 0.017,
    "gpt-transcribe": 0.0045,
}
# (input, cached input, output) per million tokens, by service tier.
TEXT_PER_MILLION = {
    "gpt-6-luna": {"default": (0.10, 0.01, 0.50), "fast": (0.20, 0.02, 1.00)},
    "gpt-6.1-terra": {"default": (1.00, 0.05, 5.00), "fast": (2.00, 0.10, 10.00)},
}


def _tier(served: str | None) -> str:
    # Fast mode reports itself as "priority".
    return "fast" if served in ("fast", "priority") else "default"


def estimate_cost(
    config: Config, billed_seconds: float, result: CleanupResult | None
) -> float | None:
    """Returns None when a model's price is unknown."""
    per_minute = TRANSCRIPTION_PER_MINUTE.get(config.transcription_model)
    if per_minute is None:
        return None
    cost = per_minute * billed_seconds / 60
    if result and (result.input_tokens or result.output_tokens):
        prices = TEXT_PER_MILLION.get(config.cleanup_model, {}).get(_tier(result.service_tier))
        if prices is None:
            return None
        uncached = result.input_tokens - result.cached_tokens
        cost += (
            uncached * prices[0]
            + result.cached_tokens * prices[1]
            + result.output_tokens * prices[2]
        ) / 1_000_000
    return round(cost, 8)


def make_entry(
    *,
    config: Config,
    outcome: str,
    raw: str,
    text: str,
    cleanup: str,
    audio_ms: int,
    billed_seconds: float,
    transcript_s: float | None,
    cleanup_s: float | None,
    result: CleanupResult | None,
    error: str | None = None,
) -> dict[str, Any]:
    """Builds one history record; `cleanup` is "done", "failed", "skipped" or "off"."""
    return {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "version": __version__,
        "outcome": outcome,
        "languages": config.languages,
        "style": config.effective_cleanup_style,
        "transcription_model": config.transcription_model,
        "cleanup_model": config.cleanup_model if cleanup in ("done", "failed") else None,
        "service_tier": result.service_tier if result else None,
        "cleanup": cleanup,
        "audio_s": round(audio_ms / 1000, 2),
        "billed_audio_s": billed_seconds,
        "transcript_s": None if transcript_s is None else round(transcript_s, 3),
        "cleanup_s": None if cleanup_s is None else round(cleanup_s, 3),
        "raw": raw,
        "text": text,
        "words": len(raw.split()),
        "removed_words": len(removed_word_indices(raw, text)) if cleanup == "done" else 0,
        "input_tokens": result.input_tokens if result else 0,
        "cached_tokens": result.cached_tokens if result else 0,
        "output_tokens": result.output_tokens if result else 0,
        "cost_usd": estimate_cost(config, billed_seconds, result),
        "error": error,
    }


def append(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    # Dictations are private: the file must never be created world-readable.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
        handle.write(line)


def _parse_time(entry: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(entry["time"]))
    except (KeyError, ValueError):
        return None


def load(path: Path, since: datetime | None = None) -> list[dict[str, Any]]:
    """Reads the history, skipping lines that are not valid records."""
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        moment = _parse_time(entry) if isinstance(entry, dict) else None
        if moment and (since is None or moment >= since):
            entries.append(entry)
    return entries


def prune(path: Path, retention_days: int, now: datetime | None = None) -> int:
    """Drops entries older than the retention period and returns how many were removed."""
    if not path.exists():
        return 0
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)
    lines = path.read_text(encoding="utf-8").splitlines()
    kept = []
    for line in lines:
        try:
            moment = _parse_time(json.loads(line))
        except json.JSONDecodeError:
            continue
        if moment and moment >= cutoff:
            kept.append(line)
    removed = len(lines) - len(kept)
    if removed:
        temporary = path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("".join(f"{line}\n" for line in kept))
        temporary.replace(path)
    return removed


@dataclass(frozen=True)
class Stats:
    dictations: int
    speaking_s: float
    cost_usd: float
    average_wait_s: float | None
    words: int
    removed_ratio: float | None
    per_day: dict[date, int]


def summarize(entries: list[dict[str, Any]]) -> Stats:
    pasted = [entry for entry in entries if entry.get("outcome") == "pasted"]
    waits = [
        (entry.get("transcript_s") or 0) + (entry.get("cleanup_s") or 0)
        for entry in pasted
        if entry.get("transcript_s") is not None
    ]
    cleaned = [entry for entry in pasted if entry.get("cleanup") == "done"]
    cleaned_words = sum(entry.get("words", 0) for entry in cleaned)
    per_day: dict[date, int] = {}
    for entry in pasted:
        moment = _parse_time(entry)
        if moment:
            day = moment.astimezone().date()
            per_day[day] = per_day.get(day, 0) + 1
    return Stats(
        dictations=len(pasted),
        speaking_s=sum(entry.get("audio_s", 0) for entry in entries),
        cost_usd=sum(entry.get("cost_usd") or 0 for entry in entries),
        average_wait_s=sum(waits) / len(waits) if waits else None,
        words=sum(entry.get("words", 0) for entry in pasted),
        removed_ratio=(
            sum(entry.get("removed_words", 0) for entry in cleaned) / cleaned_words
            if cleaned_words
            else None
        ),
        per_day=per_day,
    )


def format_duration(seconds: float) -> str:
    minutes, secs = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {minutes:02d} min"
    if minutes:
        return f"{minutes} min {secs:02d} s"
    return f"{secs} s"
