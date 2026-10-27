from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from live_dictate import config as config_module
from live_dictate.config import (
    Config,
    dump_config,
    load_api_key,
    load_config,
    store_api_key,
    system_language,
    update_config_file,
)

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

needs_display = pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="needs an X11 display")


@pytest.mark.parametrize(
    ("variables", "expected"),
    [
        ({"LANG": "fr_BE.UTF-8"}, "fr"),
        ({"LC_ALL": "de_DE.UTF-8", "LANG": "fr_BE.UTF-8"}, "de"),
        ({"LANG": "C.UTF-8"}, None),
        ({"LANG": "POSIX"}, None),
    ],
)
def test_system_language(
    monkeypatch: pytest.MonkeyPatch, variables: dict[str, str], expected: str | None
) -> None:
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    for name, value in variables.items():
        monkeypatch.setenv(name, value)
    assert system_language() == expected


def test_default_languages_follow_the_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LC_ALL", "es_ES.UTF-8")
    assert Config().languages == ["es"]


def test_dump_config_is_valid_toml() -> None:
    values = {
        "hotkey": '<ctrl>+"x"',
        "languages": ["fr", "en"],
        "cleanup_enabled": False,
        "cleanup_timeout_s": 2.5,
        "min_audio_ms": 300,
        "transcription_prompt": "Réunion d'équipe",
    }
    assert tomllib.loads(dump_config(values)) == values


def test_update_config_file_keeps_other_keys(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "config.toml"
    update_config_file(path, {"mode": "toggle", "keywords": ["Kubernetes"]})
    update_config_file(path, {"languages": ["fr"]})
    config = load_config(path)
    assert (config.mode, config.keywords, config.languages) == ("toggle", ["Kubernetes"], ["fr"])


def test_load_api_key_prefers_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", " sk-env ")
    assert load_api_key() == "sk-env"


def test_load_api_key_explains_how_to_fix_a_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(config_module, "_keyring_lookup", lambda: "")
    with pytest.raises(RuntimeError, match="live-dictate setup"):
        load_api_key()


def test_store_api_key_passes_the_key_on_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, str(kwargs["input"])))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    store_api_key("sk-secret")
    args, stdin = calls[0]
    assert args[:2] == ["secret-tool", "store"]
    assert "sk-secret" not in args
    assert stdin == "sk-secret"


@pytest.mark.parametrize(
    ("status", "expected"),
    [(200, None), (401, "rejected"), (404, "no access"), (500, "HTTP 500")],
)
def test_check_api_key(status: int, expected: str | None) -> None:
    from live_dictate.setup_window import check_api_key

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        return httpx.Response(status)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        problem = check_api_key(client, "sk-test", "gpt-live-transcribe")
    assert seen == ["Bearer sk-test"]
    if expected is None:
        assert problem is None
    else:
        assert problem is not None
        assert expected in problem


@needs_display
def test_setup_window_saves_every_setting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from live_dictate import setup_window

    stored: list[str] = []
    monkeypatch.setattr(setup_window, "store_api_key", stored.append)
    path = tmp_path / "config.toml"
    results: list[bool] = []
    window = setup_window.SetupWindow(
        Config(languages=[]),
        path,
        False,
        results.append,
        tmp_path / "history.jsonl",
        tmp_path / "recent.json",
    )

    assert window._language.get_active_id() == ""
    assert window.cleanup_style == "standard"
    window._language.set_active_id("fr")
    window._hotkey.set_active_id("<f9>")
    window._mode.set_active_id("toggle")
    window._accent.select("#fbb86c")
    window._styles["light"].set_active(True)
    window._min_words.set_value(3)
    window._max_lines.set_value(8)
    window.history.enabled.set_active(True)
    window.history.retention.set_value(7)
    window._finish("sk-new")

    assert stored == ["sk-new"]
    assert results == [True]
    config = load_config(path)
    assert (config.languages, config.hotkey, config.mode) == (["fr"], "<f9>", "toggle")
    assert (config.accent_color, config.max_lines) == ("#fbb86c", 8)
    assert (config.cleanup_style, config.cleanup_min_words) == ("light", 3)
    assert (config.history_enabled, config.history_retention_days) == (True, 7)


@needs_display
def test_history_tab_shows_recorded_dictations(tmp_path: Path) -> None:
    from live_dictate import history, setup_window
    from live_dictate.cleanup import CleanupResult

    path = tmp_path / "history.jsonl"
    entry = history.make_entry(
        config=Config(),
        outcome="pasted",
        raw="euh bonjour à tous",
        text="Bonjour à tous.",
        cleanup="done",
        audio_ms=3000,
        billed_seconds=3.0,
        transcript_s=0.4,
        cleanup_s=1.0,
        result=CleanupResult("Bonjour à tous."),
    )
    history.append(path, entry)
    page = setup_window.HistoryPage(Config(history_enabled=True), path)
    assert page._values["dictations"].get_text() == "1"
    assert page._values["wait"].get_text() == "1.4 s"
    assert page._values["removed"].get_text() == "25.0%"
    assert len(page._recent.get_children()) == 1
    assert page.open_file.get_sensitive()


@needs_display
def test_accent_picker_accepts_custom_colors() -> None:
    from live_dictate.setup_window import AccentPicker

    chosen: list[str] = []
    picker = AccentPicker("", chosen.append)
    picker.select("#123456")
    assert picker.value == "#123456"
    assert chosen == ["", "#123456"]
    assert not any(swatch.selected for swatch in picker._swatches.values())


@needs_display
def test_setup_window_requires_a_key_on_first_run(tmp_path: Path) -> None:
    from live_dictate import setup_window

    window = setup_window.SetupWindow(Config(), tmp_path / "c.toml", False, lambda _: None)
    window._on_save(window._save)
    assert "Enter your OpenAI API key" in window._status.get_text()
    assert not (tmp_path / "c.toml").exists()
    window.destroy()
