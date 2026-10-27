"""Renders each overlay state to a PNG, using the real popup window mapped off-screen.

Usage: uv run python scripts/render_overlay.py [output-dir]
"""

from __future__ import annotations

import math
import sys
from collections.abc import Callable
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from live_dictate.overlay import Overlay, OverlayView, install_css  # noqa: E402

SAMPLE = (
    "Hi team, quick update: the client meeting moves to Thursday morning, "
    "because they still need to review the latest mockups."
)

STATES: dict[str, Callable[[OverlayView], None]] = {
    "listening": lambda view: view.listening(),
    "live-text": lambda view: view.partial(SAMPLE),
    "cleaning": lambda view: view.cleaning(SAMPLE),
    "error": lambda view: view.error("Connection closed before the final transcript"),
}


def render(name: str, apply: Callable[[OverlayView], None], output: Path) -> Path:
    overlay = Overlay()
    view = overlay.view
    view.listening()
    for index in range(40):
        view.wave.push_level(abs(math.sin(index * 0.7)) * 0.8)
        view.wave.tick()
    apply(view)

    window = overlay._window
    window.move(-3000, -3000)
    window.show()
    while Gtk.events_pending():
        Gtk.main_iteration()
    width, height = window.get_size()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    window.draw(cairo.Context(surface))
    window.destroy()

    path = output / f"overlay-{name}.png"
    surface.write_to_png(str(path))
    return path


def main() -> None:
    output = Path(sys.argv[1] if len(sys.argv) > 1 else ".cache/preview")
    output.mkdir(parents=True, exist_ok=True)
    install_css()
    for name, apply in STATES.items():
        print(render(name, apply, output))


if __name__ == "__main__":
    main()
