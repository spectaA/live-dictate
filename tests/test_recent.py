from __future__ import annotations

import asyncio
import stat
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from live_dictate import recent
from live_dictate.app import App
from live_dictate.cleanup import cleanup_timeout
from live_dictate.config import Config
from live_dictate.injector import MAX_RESTORE_DELAY_S, restore_delay


class SilentUi:
    def __getattr__(self, _name: str) -> Callable[..., None]:
        return lambda *_args: None


class FakeTranscriber:
    audio_ms = 1000
    billed_seconds = 1.0

    def __init__(self, on_partial: Callable[[str], None], partial: str, final: str | None) -> None:
        self._on_partial = on_partial
        self._partial = partial
        self._final = final

    async def run(self, _audio: object) -> str:
        if self._partial:
            self._on_partial(self._partial)
        if self._final is None:
            await asyncio.Event().wait()
        return self._final or ""


def _app(path: Path, output: Callable[[str], None], partial: str, final: str | None) -> App:
    app = App(Config(), "key", SilentUi(), False, output, path)  # type: ignore[arg-type]
    app._transcriber = FakeTranscriber(app._on_partial, partial, final)  # type: ignore[assignment]
    return app


def _broken_paste(_text: str) -> None:
    raise RuntimeError("xdotool failed")


def test_add_keeps_the_newest_entries_privately(tmp_path: Path) -> None:
    path = tmp_path / "state" / "recent.json"
    start = datetime(2026, 10, 27, tzinfo=timezone.utc)
    for index in range(recent.KEEP + 2):
        recent.add(path, f"dictation {index}", "pasted", start + timedelta(seconds=index))
    entries = recent.load(path)
    assert len(entries) == recent.KEEP
    assert entries[0]["text"] == f"dictation {recent.KEEP + 1}"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_update_changes_one_entry(tmp_path: Path) -> None:
    path = tmp_path / "recent.json"
    first = recent.add(path, "euh bonjour", "transcribed")
    recent.add(path, "autre chose", "pasted")
    recent.update(path, first, text="Bonjour.", outcome="pasted")
    older = recent.load(path)[1]
    assert (older["raw"], older["text"], older["outcome"]) == ("euh bonjour", "Bonjour.", "pasted")


def test_corrupted_file_reads_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "recent.json"
    path.write_text("{not json", encoding="utf-8")
    assert recent.load(path) == []


def test_transcript_survives_a_failed_paste(tmp_path: Path) -> None:
    path = tmp_path / "recent.json"
    app = _app(path, _broken_paste, "five minutes of", "five minutes of speech")

    asyncio.run(app._session(asyncio.Queue()))

    [entry] = recent.load(path)
    assert (entry["text"], entry["outcome"]) == ("five minutes of speech", "error")


def test_cancelled_dictation_keeps_the_live_text(tmp_path: Path) -> None:
    path = tmp_path / "recent.json"
    recent.add(path, "the dictation that failed", "transcribed")

    async def cancel_after_partial(partial: str) -> None:
        app = _app(path, print, partial, None)
        session = asyncio.create_task(app._session(asyncio.Queue()))
        await asyncio.sleep(0.01)
        session.cancel()
        await session

    # Opening the settings starts a dictation only to reach the gear, then cancels it.
    asyncio.run(cancel_after_partial(""))
    assert [entry["text"] for entry in recent.load(path)] == ["the dictation that failed"]

    asyncio.run(cancel_after_partial("wait no"))
    assert [entry["text"] for entry in recent.load(path)] == [
        "wait no",
        "the dictation that failed",
    ]


def test_cleanup_timeout_grows_with_length() -> None:
    config = Config(cleanup_timeout_s=3.0, cleanup_timeout_per_word_s=0.01)
    assert cleanup_timeout(config, "un deux trois") == pytest.approx(3.03)
    assert cleanup_timeout(config, "mot " * 750) == pytest.approx(10.5)


def test_restore_delay_grows_with_length_up_to_a_cap() -> None:
    assert restore_delay(0.5, "short") == pytest.approx(0.5 + 5 / 4000)
    assert restore_delay(0.5, "x" * 4000) == pytest.approx(1.5)
    assert restore_delay(0.5, "x" * 100_000) == MAX_RESTORE_DELAY_S
