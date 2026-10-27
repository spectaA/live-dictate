"""Renders overlay states to PNGs, using the real popup window mapped off-screen.

Usage: uv run python scripts/render_overlay.py [output-dir [state ...]]
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from live_dictate.overlay import Overlay  # noqa: E402

RAW = (
    "so um I wanted to to tell you that the client meeting moves to uh Thursday morning "
    "because they still need to review the latest mockups"
)
CLEAN = (
    "I wanted to tell you that the client meeting moves to Thursday morning, "
    "because they still need to review the latest mockups."
)


def _speaking(overlay: Overlay) -> None:
    overlay.listening()
    overlay._words.update(" ".join(RAW.split()[:-4]), time.monotonic() - 5)
    overlay._words.update(RAW, time.monotonic() - 0.6)
    overlay._level = overlay._target_level = 0.8
    overlay._glow = 0.85
    overlay._angle = 1.2


def _locked(overlay: Overlay) -> None:
    _speaking(overlay)
    overlay._started -= 42
    overlay.locked()
    overlay._glow = 0.5


def _removing(overlay: Overlay) -> None:
    _speaking(overlay)
    overlay.cleaning()
    overlay.finished(RAW, CLEAN)
    overlay._since = time.monotonic() - 0.5
    overlay._glow = 0.4


def _cleaning(overlay: Overlay) -> None:
    _speaking(overlay)
    overlay.cleaning()
    overlay._glow = 0.5


def _pasted(overlay: Overlay) -> None:
    _removing(overlay)
    overlay._advance_finish(10)
    overlay._since -= 0.5


def _error(overlay: Overlay) -> None:
    overlay.error(
        "Couldn't reach OpenAI",
        "Connection closed before the final transcript. Nothing was pasted.",
        settings=True,
    )
    overlay._glow = 0.35


STATES: dict[str, tuple[str | None, Callable[[Overlay], None]]] = {
    "listening": (None, Overlay.listening),
    "live-text": (None, _speaking),
    "locked": (None, _locked),
    "cleaning": (None, _cleaning),
    "removing": (None, _removing),
    "pasted": (None, _pasted),
    "error": (None, _error),
    "accent-orange": ("#fbb86c", _speaking),
    "accent-blue": ("#3b82f6", _speaking),
}


def render(name: str, accent: str | None, apply: Callable[[Overlay], None], output: Path) -> Path:
    overlay = Overlay(accent, on_settings=lambda: None)
    apply(overlay)
    window = overlay._window
    window.move(-3000, -3000)
    window.show()
    # Frames are advanced by hand: the animation timer would relayout the text mid-capture.
    if overlay._timer is not None:
        GLib.source_remove(overlay._timer)
        overlay._timer = None
    for _ in range(3):
        overlay._tick()
        while Gtk.events_pending():
            Gtk.main_iteration()
    # GTK lays widgets out on its frame clock, so let a few frames pass before drawing.
    deadline = time.monotonic() + 0.15
    while time.monotonic() < deadline:
        while Gtk.events_pending():
            Gtk.main_iteration()
        time.sleep(0.01)
    width, height = window.get_size()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    window.draw(cairo.Context(surface))
    overlay.hide()
    window.destroy()

    path = output / f"overlay-{name}.png"
    surface.write_to_png(str(path))
    return path


def main() -> None:
    output = Path(sys.argv[1] if len(sys.argv) > 1 else ".cache/preview")
    names = sys.argv[2:] or list(STATES)
    output.mkdir(parents=True, exist_ok=True)
    for name in names:
        accent, apply = STATES[name]
        print(render(name, accent, apply, output))


if __name__ == "__main__":
    main()
