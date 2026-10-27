from __future__ import annotations

from pathlib import Path

import pytest

from live_dictate.audio import rms
from live_dictate.cleanup import CleanupError, build_request, extract_output_text, needs_cleanup
from live_dictate.config import Config, load_config
from live_dictate.hotkey import ComboTracker
from live_dictate.injector import parse_wm_class, paste_keys
from live_dictate.overlay import level_from_rms


def test_load_config_missing_file_returns_defaults(tmp_path: Path) -> None:
    assert load_config(tmp_path / "absent.toml") == Config()


def test_load_config_reads_values_and_rejects_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('mode = "toggle"\nlanguages = ["fr", "en"]\n')
    config = load_config(path)
    assert config.mode == "toggle"
    assert config.languages == ["fr", "en"]

    path.write_text("hotkeys = 1\n")
    with pytest.raises(ValueError, match="hotkeys"):
        load_config(path)


def test_config_rejects_invalid_mode() -> None:
    with pytest.raises(ValueError, match="mode"):
        Config(mode="push")


def test_cleanup_request_wraps_transcript_and_disables_reasoning() -> None:
    request = build_request(Config(), "euh salut")
    assert request["model"] == "gpt-6-luna"
    assert request["input"] == "<transcript>\neuh salut\n</transcript>"
    assert request["reasoning"] == {"effort": "none"}
    assert request["service_tier"] == "fast"
    assert request["store"] is False


def test_extract_output_text_skips_reasoning_items() -> None:
    payload = {
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "content": [{"type": "output_text", "text": " Salut, ça va ? "}],
            },
        ]
    }
    assert extract_output_text(payload) == "Salut, ça va ?"


def test_extract_output_text_raises_when_empty() -> None:
    with pytest.raises(CleanupError):
        extract_output_text({"output": []})


def test_parse_wm_class() -> None:
    output = 'WM_CLASS(STRING) = "gnome-terminal-server", "Gnome-terminal"\n'
    assert parse_wm_class(output) == ["gnome-terminal-server", "gnome-terminal"]


def test_paste_keys_uses_shift_in_terminals_only() -> None:
    terminals = ["gnome-terminal-server", "kitty"]
    assert paste_keys(["gnome-terminal-server", "gnome-terminal"], terminals) == "ctrl+shift+v"
    assert paste_keys(["navigator", "firefox"], terminals) == "ctrl+v"
    assert paste_keys([], terminals) == "ctrl+v"


def _generic(key: str) -> set[str]:
    return {key, key.split("_")[0]}


def test_combo_tracker_single_specific_key() -> None:
    tracker = ComboTracker(["ctrl_r"], _generic)
    assert not tracker.press("ctrl_l")
    assert tracker.press("ctrl_r")
    assert not tracker.press("ctrl_r")
    assert tracker.release("ctrl_r")
    assert not tracker.release("ctrl_r")


def test_combo_tracker_generic_modifier_combo() -> None:
    tracker = ComboTracker(["ctrl", "alt", "d"], _generic)
    assert not tracker.press("ctrl_l")
    assert not tracker.press("alt_l")
    assert tracker.press("d")
    assert tracker.release("alt_l")
    assert not tracker.release("d")


def test_rms_of_constant_signal() -> None:
    chunk = (1000).to_bytes(2, "little", signed=True) * 100
    assert rms(chunk) == 1000
    assert rms(b"") == 0


def test_level_from_rms_is_clamped() -> None:
    assert level_from_rms(0) == 0
    assert level_from_rms(1) == 0
    assert level_from_rms(32767) == 1
    assert 0 < level_from_rms(1000) < 1


def _line_texts(layout, text: str) -> list[str]:
    layout.set_text(text, -1)
    raw = text.encode()
    return [
        raw[line.start_index : line.start_index + line.length].decode()
        for line in layout.get_lines_readonly()
    ]


def test_last_lines_keeps_whole_lines_with_identical_wrapping() -> None:
    import cairo
    import gi

    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo

    from live_dictate.overlay import last_lines

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 10, 10)
    layout = PangoCairo.create_layout(cairo.Context(surface))
    layout.set_font_description(Pango.FontDescription.from_string("Sans 12.5"))
    layout.set_width(460 * Pango.SCALE)
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)

    words = [
        "la",
        "réunion",
        "de",
        "demain",
        "est",
        "décalée",
        "à",
        "jeudi",
        "matin",
        "parce",
        "que",
        "le",
        "client",
        "hésite",
    ]
    text = ""
    for index in range(80):
        text = f"{text} {words[index % len(words)]}".strip()
        full_lines = _line_texts(layout, text)
        kept = last_lines(layout, text, max_lines=4)
        assert _line_texts(layout, kept) == full_lines[-4:]


def test_needs_cleanup_skips_short_dictations() -> None:
    assert not needs_cleanup("OK, c'est un test.", 5)
    assert needs_cleanup("euh je voulais te dire que", 5)
    assert not needs_cleanup("", 5)
