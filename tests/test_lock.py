from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from typing import Any

import pytest
from websockets.exceptions import InvalidStatus

from live_dictate.app import DOUBLE_TAP_S, App, State, describe_error, space_locks
from live_dictate.config import Config
from live_dictate.keygrab import LOCKED_KEYS, SPACE_KEYS
from live_dictate.transcriber import TranscriptionError


class Ui:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Callable[..., None]:
        return lambda *_args: self.calls.append(name)


class Keys:
    def __init__(self) -> None:
        self.grabs: list[tuple[int, ...]] = []

    def set(self, keysyms: Iterable[int]) -> None:
        self.grabs.append(tuple(keysyms))


def _app(monkeypatch: pytest.MonkeyPatch, **config: Any) -> tuple[App, Ui, Keys]:
    def start(app: App) -> None:
        app._state = State.RECORDING
        app._ui.listening()

    monkeypatch.setattr(App, "_start", start)
    ui, keys = Ui(), Keys()
    app = App(Config(**config), "key", ui, False, print, None, keys)  # type: ignore[arg-type]
    return app, ui, keys


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
    assert keys.grabs == [SPACE_KEYS, ()]


def test_double_tap_locks_until_enter(monkeypatch: pytest.MonkeyPatch) -> None:
    app, ui, keys = _app(monkeypatch)

    async def scenario() -> None:
        app.on_hotkey_down()
        app.on_hotkey_up()
        app.on_hotkey_down()
        app.on_hotkey_up()
        await asyncio.sleep(DOUBLE_TAP_S + 0.05)
        assert app._state is State.RECORDING
        assert keys.grabs[-1] == LOCKED_KEYS
        app.on_enter()
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
        app.on_space()
        app.on_hotkey_up()
        assert app._state is State.RECORDING
        app.on_enter()
        await asyncio.sleep(0)

    _run(scenario)
    assert "locked" in ui.calls
    assert keys.grabs == [SPACE_KEYS, LOCKED_KEYS, ()]


def test_hotkey_press_also_finishes_a_locked_dictation(monkeypatch: pytest.MonkeyPatch) -> None:
    app, _ui, _keys = _app(monkeypatch)

    async def scenario() -> None:
        app.on_hotkey_down()
        app.on_space()
        app.on_hotkey_up()
        app.on_hotkey_down()
        await asyncio.sleep(0)

    _run(scenario)
    assert app._state is State.FINALIZING


def test_toggle_mode_never_locks(monkeypatch: pytest.MonkeyPatch) -> None:
    app, ui, keys = _app(monkeypatch, mode="toggle")

    async def scenario() -> None:
        app.on_hotkey_down()
        app.on_space()
        app.on_enter()
        assert app._state is State.RECORDING
        app.on_hotkey_down()
        await asyncio.sleep(0)

    _run(scenario)
    assert "locked" not in ui.calls
    assert app._state is State.FINALIZING
    assert all(grab == () for grab in keys.grabs)


def test_space_does_not_lock_when_it_is_part_of_the_hotkey() -> None:
    assert space_locks(Config())
    assert not space_locks(Config(hotkey="<ctrl>+<alt>+<space>"))
    assert not space_locks(Config(mode="toggle"))


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
