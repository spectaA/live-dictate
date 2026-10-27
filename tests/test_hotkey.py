from __future__ import annotations

import time

from pynput.keyboard import Key, KeyCode

from live_dictate.hotkey import AUTOREPEAT_GRACE_S, HotkeyListener


class Recorder:
    def __init__(self) -> None:
        self.events: list[str] = []

    def listener(self, spec: str) -> HotkeyListener:
        return HotkeyListener(
            spec,
            on_down=lambda: self.events.append("down"),
            on_up=lambda: self.events.append("up"),
            on_escape=lambda: self.events.append("esc"),
            on_enter=lambda: self.events.append("enter"),
            on_space=lambda: self.events.append("space"),
        )


def _settle() -> None:
    time.sleep(AUTOREPEAT_GRACE_S * 3)


def test_right_ctrl_matches_only_right_side() -> None:
    recorder = Recorder()
    hotkey = recorder.listener("<ctrl_r>")
    hotkey._press(Key.ctrl_l)
    hotkey._release(Key.ctrl_l)
    hotkey._press(Key.ctrl_r)
    hotkey._release(Key.ctrl_r)
    _settle()
    assert recorder.events == ["down", "up"]


def test_generic_ctrl_combo_matches_either_side() -> None:
    recorder = Recorder()
    hotkey = recorder.listener("<ctrl>+<alt>+d")
    hotkey._press(Key.ctrl_r)
    hotkey._press(Key.alt_l)
    hotkey._press(KeyCode.from_char("d"))
    hotkey._release(KeyCode.from_char("d"))
    _settle()
    assert recorder.events == ["down", "up"]


def test_autorepeat_release_press_pair_is_ignored() -> None:
    recorder = Recorder()
    hotkey = recorder.listener("<f9>")
    hotkey._press(Key.f9)
    hotkey._release(Key.f9)
    hotkey._press(Key.f9)
    _settle()
    assert recorder.events == ["down"]
    hotkey._release(Key.f9)
    _settle()
    assert recorder.events == ["down", "up"]


def test_escape_is_reported() -> None:
    recorder = Recorder()
    hotkey = recorder.listener("<ctrl_r>")
    hotkey._press(Key.esc)
    assert recorder.events == ["esc"]


def test_both_enter_keys_are_reported() -> None:
    recorder = Recorder()
    hotkey = recorder.listener("<ctrl_r>")
    hotkey._press(Key.enter)
    hotkey._press(KeyCode.from_vk(0xFF8D))
    hotkey._press(KeyCode.from_char("a"))
    hotkey._press(Key.space)
    assert recorder.events == ["enter", "enter", "space"]
