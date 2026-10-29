from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from live_dictate.overlay import Overlay

pytestmark = pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="needs an X11 display")

TEXT = "Bon, je voulais te dire que la réunion de demain est décalée à jeudi matin."


def _settle() -> None:
    from gi.repository import Gtk

    # GTK lays widgets out on its frame clock, so let a few frames pass.
    deadline = time.monotonic() + 0.15
    while time.monotonic() < deadline:
        while Gtk.events_pending():
            Gtk.main_iteration()
        time.sleep(0.01)


def _shown_overlay(text: str | None, accent: str | None = None, max_lines: int = 4) -> Overlay:
    from live_dictate.overlay import Overlay

    overlay = Overlay(accent, max_lines=max_lines)
    overlay.listening()
    if text:
        overlay._words.update(text, time.monotonic() - 5)
    window = overlay._window
    # Mapped outside every monitor so nothing appears on screen.
    window.move(-3000, -3000)
    window.show()
    overlay._tick()
    _settle()
    return overlay


def max_edge_alpha(text: str | None, accent: str | None) -> int:
    import cairo

    overlay = _shown_overlay(text, accent)
    overlay._glow = 1.0
    window = overlay._window
    width, height = window.get_size()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    window.draw(cairo.Context(surface))
    overlay.hide()
    window.destroy()

    data, stride = surface.get_data(), surface.get_stride()

    def alpha(x: int, y: int) -> int:
        return data[y * stride + x * 4 + 3]

    edges = [alpha(x, y) for x in range(width) for y in (0, height - 1)]
    edges += [alpha(x, y) for y in range(height) for x in (0, width - 1)]
    return max(edges)


@pytest.mark.parametrize("text", [None, TEXT])
@pytest.mark.parametrize("accent", [None, "#fbb86c"])
def test_glow_fades_out_before_window_edges(text: str | None, accent: str | None) -> None:
    assert max_edge_alpha(text, accent) == 0


@pytest.mark.parametrize("max_lines", [2, 4, 9])
def test_long_dictation_shows_at_most_max_lines(max_lines: int) -> None:
    overlay = _shown_overlay(TEXT * 12, max_lines=max_lines)
    assert overlay.view.text.get_layout().get_line_count() == max_lines
    overlay.hide()
    overlay._window.destroy()


def test_locked_overlay_shows_how_to_finish() -> None:
    from live_dictate.overlay import Overlay

    overlay = Overlay(on_settings=lambda: None)
    overlay.listening()
    assert {"lock", "hint", "cancel", "settings"} <= overlay.view.visible_parts()
    overlay.locked()
    parts = overlay.view.visible_parts()
    assert {"badge", "finish_keys", "cancel", "settings"} <= parts
    assert not parts & {"lock", "hint", "eq"}
    overlay.hide()
    overlay._window.destroy()


def test_toggle_mode_has_no_lock_hint() -> None:
    from live_dictate.overlay import TOGGLE_HINT, Overlay

    overlay = Overlay(hold=False)
    overlay.listening()
    assert "lock" not in overlay.view.visible_parts()
    assert overlay.view.hint.get_text() == TOGGLE_HINT
    overlay._window.destroy()


def test_error_offers_the_settings() -> None:
    from live_dictate.overlay import Overlay

    clicks: list[bool] = []
    overlay = Overlay(on_settings=lambda: clicks.append(True))
    overlay.error("Couldn't reach OpenAI", "Connection closed.", settings=True)
    assert {"title", "text", "error_button"} <= overlay.view.visible_parts()
    overlay.view.error_button.clicked()
    assert clicks == [True]
    assert not overlay._window.get_visible()
    overlay._window.destroy()


def test_finish_sequence_ends_hidden() -> None:
    overlay = _shown_overlay(TEXT)
    overlay.cleaning()
    overlay.finished("euh " + TEXT, TEXT)
    assert overlay._removed == {0}
    overlay._advance_finish(10)
    assert overlay._mode == "pasted"
    assert not overlay.view.text.get_visible()
    overlay._since -= 10
    assert overlay._tick() is False
    assert not overlay._window.get_visible()
    overlay._window.destroy()


def test_settings_gear_reports_clicks() -> None:
    from live_dictate.overlay import Overlay

    clicks: list[bool] = []
    overlay = Overlay(on_settings=lambda: clicks.append(True))
    overlay.listening()
    assert overlay.view.settings.get_visible()
    overlay.view.settings.clicked()
    assert clicks == [True]
    overlay.finished("bonjour à tous", "bonjour à tous")
    assert not overlay.view.settings.get_visible()
    overlay.hide()
    overlay._window.destroy()


def test_overlay_without_settings_hides_the_gear() -> None:
    from live_dictate.overlay import Overlay

    overlay = Overlay()
    overlay.listening()
    assert not overlay.view.settings.get_visible()
    overlay._window.destroy()


def test_keycaps_follow_the_configured_shortcuts() -> None:
    from live_dictate.overlay import Overlay

    overlay = Overlay(lock_key="l", finish_key="<ctrl>+<enter>", cancel_key="<f8>")
    view = overlay.view
    finish = [keycap.label for keycap in view.finish_keys.get_children()]
    assert finish == ["Ctrl", "Enter"]
    assert [keycap.label for keycap in view.cancel.get_children()] == ["F8"]
    assert view.lock.get_tooltip_text() == "Press L while holding the hotkey to lock"
    overlay._window.destroy()


def test_no_lock_key_hides_the_reminder() -> None:
    from live_dictate.overlay import Overlay

    overlay = Overlay(lock_key="")
    overlay.listening()
    assert "lock" not in overlay.view.visible_parts()
    overlay._window.destroy()
