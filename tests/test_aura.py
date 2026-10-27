from __future__ import annotations

import colorsys

import pytest

from live_dictate import accent as accent_module
from live_dictate.accent import GNOME_ACCENTS, hex_to_rgb, resolve_accent
from live_dictate.aura import palette
from live_dictate.config import Config
from live_dictate.textflow import (
    LiveWords,
    first_word_of,
    removal_markup,
    removed_word_indices,
)


def test_hex_to_rgb() -> None:
    assert hex_to_rgb("#ff8000") == (1.0, 128 / 255, 0.0)
    with pytest.raises(ValueError, match="rrggbb"):
        hex_to_rgb("orange")


def test_configured_accent_wins_over_the_system(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(accent_module, "system_accent", lambda: GNOME_ACCENTS["blue"])
    assert resolve_accent("#fbb86c") == "#fbb86c"
    assert resolve_accent("") == GNOME_ACCENTS["blue"]


def test_without_any_accent_the_look_is_neutral(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(accent_module, "system_accent", lambda: None)
    assert resolve_accent("") is None
    for red, green, blue in palette(None).stops:
        assert red == green == blue


def test_accent_palette_stays_close_to_the_accent_hue() -> None:
    hue = colorsys.rgb_to_hls(*hex_to_rgb("#fbb86c"))[0]
    for stop in palette("#fbb86c").stops:
        distance = abs(colorsys.rgb_to_hls(*stop)[0] - hue)
        assert min(distance, 1 - distance) <= 25 / 360


def test_palette_interpolation_wraps_around() -> None:
    colors = palette("#3b82f6")
    assert colors.at(0.0) == pytest.approx(colors.stops[0])
    assert colors.at(1.0) == pytest.approx(colors.stops[0])


def test_config_rejects_malformed_accent() -> None:
    assert Config(accent_color="#FBB86C").accent_color == "#FBB86C"
    with pytest.raises(ValueError, match="accent_color"):
        Config(accent_color="orange")


def test_removed_words_ignore_punctuation_and_case() -> None:
    raw = "euh Bon je je voulais dire que la réunion est décalée à jeudi matin"
    clean = "Je voulais dire que la réunion est décalée à jeudi matin."
    assert removed_word_indices(raw, clean) == {0, 1, 2}


def test_rewritten_words_count_as_removed() -> None:
    assert removed_word_indices("parce que il pleut", "car il pleut") == {0, 1}


def test_first_word_of_a_trimmed_tail() -> None:
    words = ["alpha", "beta", "gamma", "delta"]
    assert first_word_of(words, "gamma delta") == 2
    assert first_word_of(words, "alpha beta gamma delta") == 0


def test_live_words_fade_in_only_new_words() -> None:
    live = LiveWords()
    live.update("hello there", now=0.0)
    live.update("hello there friend", now=1.5)
    markup = live.markup(0, now=1.6)
    assert markup.startswith("hello there ")
    assert 'alpha="33%"' in markup
    assert "alpha" not in live.markup(0, now=2.0)


def test_live_words_highlight_fresh_words() -> None:
    live = LiveWords()
    live.update("hello", now=0.0)
    live.update("hello there", now=1.0)
    assert live.markup(0, now=1.5) == 'hello <span foreground="#ffffff">there</span>'
    assert live.markup(0, now=3.0) == "hello there"


def test_live_words_escape_markup() -> None:
    live = LiveWords()
    live.update("a <b> & c", now=0.0)
    assert live.markup(0, now=5.0) == "a &lt;b&gt; &amp; c"


def test_removal_markup_strikes_then_fades() -> None:
    words = ["euh", "bonjour"]
    early = removal_markup(words, {0}, 0, elapsed=0.1)
    late = removal_markup(words, {0}, 0, elapsed=0.75)
    assert "strikethrough" not in early
    assert 'strikethrough="true"' in late
    assert late.endswith(" bonjour")
    assert 'alpha="100%"' in early
    assert 'alpha="100%"' not in late
