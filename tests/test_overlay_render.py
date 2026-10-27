from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="needs an X11 display")

TEXT = "Bon, je voulais te dire que la réunion de demain est décalée à jeudi matin."


def max_edge_alpha(state: str) -> int:
    import cairo
    from gi.repository import Gtk

    from live_dictate.overlay import Overlay

    overlay = Overlay()
    overlay.view.listening()
    if state == "text":
        overlay.view.partial(TEXT)
    window = overlay._window
    # Mapped outside every monitor so nothing appears on screen.
    window.move(-3000, -3000)
    window.show()
    while Gtk.events_pending():
        Gtk.main_iteration()
    width, height = window.get_size()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    window.draw(cairo.Context(surface))
    window.destroy()

    data, stride = surface.get_data(), surface.get_stride()

    def alpha(x: int, y: int) -> int:
        return data[y * stride + x * 4 + 3]

    edges = [alpha(x, y) for x in range(width) for y in (0, height - 1)]
    edges += [alpha(x, y) for y in range(height) for x in (0, width - 1)]
    return max(edges)


@pytest.mark.parametrize("state", ["pill", "text"])
def test_shadow_fades_out_before_window_edges(state: str) -> None:
    assert max_edge_alpha(state) == 0


def test_long_dictation_shows_at_most_max_lines() -> None:
    from gi.repository import Gtk

    from live_dictate.overlay import MAX_LINES, Overlay

    overlay = Overlay()
    overlay.view.listening()
    overlay.view.partial(TEXT * 8)
    overlay._window.move(-3000, -3000)
    overlay._window.show()
    while Gtk.events_pending():
        Gtk.main_iteration()
    label = overlay.view._text
    assert 1 < label.get_layout().get_line_count() <= MAX_LINES
    overlay._window.destroy()
