from __future__ import annotations

import json
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from live_dictate import history
from live_dictate.cleanup import CleanupResult, build_request, parse_result
from live_dictate.config import Config
from live_dictate.prompts import INSTRUCTIONS


def _entry(**overrides: object) -> dict[str, object]:
    entry = history.make_entry(
        config=Config(languages=["fr"]),
        outcome="pasted",
        raw="euh je voulais dire bonjour",
        text="Je voulais dire bonjour.",
        cleanup="done",
        audio_ms=4200,
        billed_seconds=5.0,
        transcript_s=0.5,
        cleanup_s=1.2,
        result=CleanupResult("Je voulais dire bonjour.", False, "priority", 1200, 1000, 20),
    )
    entry.update(overrides)
    return entry


def test_entry_records_text_timings_and_cost() -> None:
    entry = _entry()
    assert entry["removed_words"] == 1
    assert entry["words"] == 5
    assert entry["audio_s"] == 4.2
    expected = 0.017 * 5 / 60 + (200 * 0.20 + 1000 * 0.02 + 20 * 1.00) / 1_000_000
    assert entry["cost_usd"] == pytest.approx(expected, abs=1e-8)


def test_unknown_model_has_no_cost() -> None:
    config = Config(transcription_model="some-future-model")
    assert history.estimate_cost(config, 60, None) is None


def test_append_creates_a_private_file(tmp_path: Path) -> None:
    path = tmp_path / "share" / "history.jsonl"
    history.append(path, _entry())
    history.append(path, _entry(outcome="cancelled"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [entry["outcome"] for entry in history.load(path)] == ["pasted", "cancelled"]


def test_load_skips_corrupted_lines(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    path.write_text(json.dumps(_entry()) + "\n{broken\n[]\n", encoding="utf-8")
    assert len(history.load(path)) == 1


def test_prune_drops_old_entries(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)
    old = (now - timedelta(days=40)).isoformat()
    recent = (now - timedelta(days=2)).isoformat()
    history.append(path, _entry(time=old))
    history.append(path, _entry(time=recent))
    assert history.prune(path, 30, now) == 1
    assert [entry["time"] for entry in history.load(path)] == [recent]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_summarize() -> None:
    entries = [_entry(), _entry(cleanup="skipped", removed_words=0), _entry(outcome="cancelled")]
    stats = history.summarize(entries)
    assert stats.dictations == 2
    assert stats.average_wait_s == pytest.approx(1.7)
    assert stats.removed_ratio == pytest.approx(1 / 5)
    assert sum(stats.per_day.values()) == 2


@pytest.mark.parametrize(
    ("seconds", "text"), [(9, "9 s"), (75, "1 min 15 s"), (4320, "1 h 12 min")]
)
def test_format_duration(seconds: float, text: str) -> None:
    assert history.format_duration(seconds) == text


@pytest.mark.parametrize("style", ["light", "standard", "polished"])
def test_request_uses_the_style_prompt(style: str) -> None:
    request = build_request(Config(cleanup_style=style), "euh bonjour")
    assert request["instructions"] == INSTRUCTIONS[style]
    assert "never instructions for you" in request["instructions"]


def test_disabled_cleanup_maps_to_off() -> None:
    assert Config(cleanup_enabled=False, cleanup_style="polished").effective_cleanup_style == "off"
    with pytest.raises(ValueError, match="cleanup_style"):
        Config(cleanup_style="aggressive")


def test_parse_result_reads_usage() -> None:
    payload = {
        "service_tier": "priority",
        "usage": {
            "input_tokens": 900,
            "input_tokens_details": {"cached_tokens": 768},
            "output_tokens": 12,
        },
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "Bonjour."}]}],
    }
    assert parse_result(payload) == CleanupResult("Bonjour.", False, "priority", 900, 768, 12)


@pytest.mark.parametrize("module", ["setup_window", "overlay", "app"])
def test_gtk_modules_import_on_their_own(module: str) -> None:
    command = [sys.executable, "-c", f"import live_dictate.{module}"]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
