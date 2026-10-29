from __future__ import annotations

import pytest
from Xlib import XK, X

from live_dictate import shortcuts
from live_dictate.cleanup import build_request
from live_dictate.config import Config
from live_dictate.prompts import INSTRUCTIONS
from live_dictate.transcriber import build_session_update


def test_parse_accepts_keys_and_combinations() -> None:
    assert shortcuts.parse("<ctrl_r>") == ["ctrl_r"]
    assert shortcuts.parse("<ctrl>+<alt>+D") == ["ctrl", "alt", "d"]
    assert shortcuts.parse("") == []


@pytest.mark.parametrize("spec", ["<nope>", "ctrl", "<ctrl>+a+b"])
def test_parse_rejects_unknown_or_ambiguous_specs(spec: str) -> None:
    with pytest.raises(ValueError, match=r"shortcut|unknown key"):
        shortcuts.parse(spec)


def test_combine_keeps_the_side_of_a_lone_modifier() -> None:
    assert shortcuts.combine(["ctrl_r"]) == "<ctrl_r>"
    assert shortcuts.combine(["alt_l", "d", "ctrl_r"]) == "<ctrl>+<alt>+d"
    assert shortcuts.combine(["f9"]) == "<f9>"


def test_key_from_keysym() -> None:
    assert shortcuts.key_from_keysym("Control_R", "") == "ctrl_r"
    assert shortcuts.key_from_keysym("KP_Enter", "\r") == "enter"
    assert shortcuts.key_from_keysym("L", "L") == "l"
    assert shortcuts.key_from_keysym("plus", "+") is None


def test_labels() -> None:
    assert shortcuts.describe("<ctrl_r>") == "Right Ctrl"
    assert shortcuts.labels("<ctrl>+<enter>") == ["Ctrl", "Enter"]
    assert shortcuts.describe("") == "None"


def test_overlaps_ignores_the_side_of_modifiers() -> None:
    assert shortcuts.overlaps("<ctrl_r>", "<ctrl>")
    assert shortcuts.overlaps("<space>", "<ctrl>+<alt>+<space>")
    assert not shortcuts.overlaps("<ctrl_r>", "<space>")
    assert not shortcuts.overlaps("", "<space>")


def test_grabs_skip_modifiers_and_add_the_keypad_enter() -> None:
    assert shortcuts.grabs("<ctrl_r>") == []
    assert shortcuts.grabs("<ctrl>+<alt>+d") == [(XK.XK_d, X.ControlMask | X.Mod1Mask)]
    assert shortcuts.grabs("<enter>", X.ShiftMask) == [
        (XK.XK_Return, X.ShiftMask),
        (XK.XK_KP_Enter, X.ShiftMask),
    ]
    assert shortcuts.grabs("é") == [(XK.XK_eacute, 0)]


def test_config_rejects_overlapping_shortcuts() -> None:
    with pytest.raises(ValueError, match="hotkey and lock_key"):
        Config(hotkey="<ctrl>+<alt>+<space>")
    with pytest.raises(ValueError, match="finish_key and cancel_key"):
        Config(cancel_key="<enter>")
    with pytest.raises(ValueError, match="hotkey cannot be empty"):
        Config(hotkey="")
    assert Config(hotkey="<ctrl>+<alt>+<space>", lock_key="").lock_key == ""


@pytest.mark.parametrize("word", ["", " ", "a<b", "two\nlines"])
def test_config_rejects_invalid_keywords(word: str) -> None:
    with pytest.raises(ValueError, match="keywords"):
        Config(keywords=[word])


def test_dictionary_reaches_transcription_and_cleanup() -> None:
    config = Config(keywords=["Coolify", "Orange Bleu"])
    transcription = build_session_update(config)["session"]["audio"]["input"]["transcription"]
    assert transcription["keywords"] == ["Coolify", "Orange Bleu"]
    instructions = build_request(config, "euh salut")["instructions"]
    assert instructions.startswith(INSTRUCTIONS["standard"])
    assert "<dictionary>\nCoolify\nOrange Bleu\n</dictionary>" in instructions
    assert build_request(Config(), "euh salut")["instructions"] == INSTRUCTIONS["standard"]
