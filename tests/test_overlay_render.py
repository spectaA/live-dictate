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


def _shown_overlay(text: str | None, accent: str | None = None) -> Overlay:
    from live_dictate.overlay import Overlay

    overlay = Overlay(accent)
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


def test_long_dictation_shows_at_most_max_lines() -> None:
    from live_dictate.overlay import MAX_LINES

    overlay = _shown_overlay(TEXT * 8)
    assert 1 < overlay.view.text.get_layout().get_line_count() <= MAX_LINES
    overlay.hide()
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
