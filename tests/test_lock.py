from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from typing import Any

import pytest
from websockets.exceptions import InvalidStatus

from live_dictate.app import (
    DOUBLE_TAP_S,
    App,
    State,
    describe_error,
    lock_key_enabled,
    lock_key_grabs,
    locked_grabs,
)
from live_dictate.config import Config
from live_dictate.keygrab import Grab
from live_dictate.transcriber import TranscriptionError


class Ui:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Callable[..., None]:
        return lambda *_args: self.calls.append(name)


class Keys:
    def __init__(self) -> None:
        self.grabs: list[tuple[Grab, ...]] = []

    def set(self, grabs: Iterable[Grab]) -> int:
        self.grabs.append(tuple(grabs))
        return 0


def _app(monkeypatch: pytest.MonkeyPatch, **config: Any) -> tuple[App, Ui, Keys]:
    def start(app: App) -> None:
        app._state = State.RECORDING
        app._ui.listening()

    monkeypatch.setattr(App, "_start", start)
    ui, keys = Ui(), Keys()
    app = App(Config(**config), "key", ui, False, print, None, keys)  # type: ignore[arg-type]
    return app, ui, keys


CONFIG = Config()
LOCK_GRABS = tuple(lock_key_grabs(CONFIG))
LOCKED_GRABS = tuple(locked_grabs(CONFIG))


def _run(scenario: Callable[[], Any]) -> None:
    async def main() -> None:
        await scenario()
        await asyncio.sleep(0)

    asyncio.run(main())


def test_long_press_pastes_on_release(monkeypatch: pytest.MonkeyPatch) -> None:
    app, ui, keys = _app(monkeypatch)

    async def scenario() -> None:
        app.on_hotkey_down()
        app._pressed_at -= 2
        app.on_hotkey_up()
        await asyncio.sleep(0)

    _run(scenario)
    assert app._state is State.FINALIZING
    assert "locked" not in ui.calls
    assert keys.grabs == [LOCK_GRABS, ()]


def test_double_tap_locks_until_enter(monkeypatch: pytest.MonkeyPatch) -> None:
    app, ui, keys = _app(monkeypatch)

    async def scenario() -> None:
        app.on_hotkey_down()
        app.on_hotkey_up()
        app.on_hotkey_down()
        app.on_hotkey_up()
        await asyncio.sleep(DOUBLE_TAP_S + 0.05)
        assert app._state is State.RECORDING
        assert keys.grabs[-1] == LOCKED_GRABS
        app.on_finish_key()
        await asyncio.sleep(0)

    _run(scenario)
    assert ui.calls.count("locked") == 1
    assert app._state is State.FINALIZING
    assert keys.grabs[-1] == ()


def test_single_tap_finishes_after_the_double_tap_window(monkeypatch: pytest.MonkeyPatch) -> None:
    app, ui, _keys = _app(monkeypatch)

    async def scenario() -> None:
        app.on_hotkey_down()
        app.on_hotkey_up()
        assert app._state is State.RECORDING
        await asyncio.sleep(DOUBLE_TAP_S + 0.05)

    _run(scenario)
    assert app._state is State.FINALIZING
    assert "locked" not in ui.calls


def test_space_locks_a_dictation_in_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    app, ui, keys = _app(monkeypatch)

    async def scenario() -> None:
        app.on_hotkey_down()
        app._pressed_at -= 60
        app.on_lock_key()
        app.on_hotkey_up()
        assert app._state is State.RECORDING
        app.on_finish_key()
        await asyncio.sleep(0)

    _run(scenario)
    assert "locked" in ui.calls
    assert keys.grabs == [LOCK_GRABS, LOCKED_GRABS, ()]


def test_hotkey_press_also_finishes_a_locked_dictation(monkeypatch: pytest.MonkeyPatch) -> None:
    app, _ui, _keys = _app(monkeypatch)

    async def scenario() -> None:
        app.on_hotkey_down()
        app.on_lock_key()
        app.on_hotkey_up()
        app.on_hotkey_down()
        await asyncio.sleep(0)

    _run(scenario)
    assert app._state is State.FINALIZING


def test_toggle_mode_never_locks(monkeypatch: pytest.MonkeyPatch) -> None:
    app, ui, keys = _app(monkeypatch, mode="toggle")

    async def scenario() -> None:
        app.on_hotkey_down()
        app.on_lock_key()
        app.on_finish_key()
        assert app._state is State.RECORDING
        app.on_hotkey_down()
        await asyncio.sleep(0)

    _run(scenario)
    assert "locked" not in ui.calls
    assert app._state is State.FINALIZING
    assert all(grab == () for grab in keys.grabs)


def test_lock_key_needs_hold_mode_and_a_key() -> None:
    assert lock_key_enabled(Config())
    assert not lock_key_enabled(Config(lock_key=""))
    assert not lock_key_enabled(Config(mode="toggle"))


def test_lock_key_is_grabbed_with_the_hotkey_modifiers() -> None:
    from Xlib import XK, X

    assert ((XK.XK_space, X.ControlMask),) == LOCK_GRABS
    assert (XK.XK_KP_Enter, 0) in LOCKED_GRABS
    assert (XK.XK_Escape, 0) in LOCKED_GRABS
    config = Config(hotkey="<f9>", lock_key="l", finish_key="<ctrl>+<enter>")
    assert lock_key_grabs(config) == [(XK.XK_l, 0)]
    assert (XK.XK_Return, X.ControlMask) in locked_grabs(config)


class Response:
    status_code = 401


@pytest.mark.parametrize(
    ("error", "title", "settings"),
    [
        (InvalidStatus(Response()), "OpenAI rejected the API key", True),  # type: ignore[arg-type]
        (ConnectionRefusedError("refused"), "Couldn't reach OpenAI", True),
        (
            TranscriptionError("connection closed before the final transcript"),
            "Couldn't reach OpenAI",
            True,
        ),
        (TranscriptionError("audio too noisy"), "Something went wrong", False),
    ],
)
def test_describe_error(error: Exception, title: str, settings: bool) -> None:
    described = describe_error(error)
    assert (described[0], described[2]) == (title, settings)
    assert described[1].endswith(". Nothing was pasted.")
    assert described[1][0].isupper()


def test_max_lines_is_validated() -> None:
    with pytest.raises(ValueError, match="max_lines"):
        Config(max_lines=0)
