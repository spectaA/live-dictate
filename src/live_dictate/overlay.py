from __future__ import annotations

import math
import time
from collections import deque

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

MAX_LINES = 4
TEXT_WIDTH = 460
BOTTOM_MARGIN = 42
FRAME_MS = 33

BAR_COUNT = 18
BAR_WIDTH = 3
BAR_GAP = 3
WAVE_HEIGHT = 22
DOT_RADIUS = 4.5
DOT_SPACE = 18

COLORS = {
    "listening": (1.0, 0.27, 0.23),
    "cleaning": (0.39, 0.82, 1.0),
    "error": (1.0, 0.84, 0.04),
}

# GTK 3 blurs box-shadow well beyond its declared radius, and the window is cropped to the
# card margin: tests/test_overlay_render.py checks that the shadow fades out before the edges.
SHADOW = "0 6px 14px rgba(0, 0, 0, 0.38)"
CARD_MARGIN = "32px 44px 48px 44px"

CSS = f"""
.ld-card {{
    background-color: rgba(20, 20, 24, 0.94);
    border: 1px solid rgba(255, 255, 255, 0.09);
    border-radius: 18px;
    padding: 10px 14px;
    margin: {CARD_MARGIN};
    box-shadow: {SHADOW};
}}
.ld-status {{ color: rgba(235, 235, 245, 0.62); font-size: 9.5pt; font-weight: 600; }}
.ld-key {{
    color: rgba(235, 235, 245, 0.45);
    font-size: 8pt;
    border: 1px solid rgba(235, 235, 245, 0.18);
    border-radius: 5px;
    padding: 0 5px;
}}
.ld-text {{ color: #f2f2f7; font-size: 12.5pt; }}
.ld-error {{ color: #ffd60a; }}
""".encode()


def last_lines(layout: Pango.Layout, text: str, max_lines: int = MAX_LINES) -> str:
    """Drops whole leading lines so the kept lines wrap exactly as they did in the full text."""
    layout.set_text(text, -1)
    lines = layout.get_lines_readonly()
    if len(lines) <= max_lines:
        return text
    # Pango indexes are UTF-8 byte offsets.
    start = lines[-max_lines].start_index
    return text.encode()[start:].decode()


def level_from_rms(rms: float) -> float:
    """Maps PCM16 RMS to 0..1 on a -55..-12 dBFS scale."""
    if rms <= 0:
        return 0.0
    dbfs = 20 * math.log10(rms / 32768)
    return min(1.0, max(0.0, (dbfs + 55) / 43))


class Wave(Gtk.DrawingArea):
    def __init__(self) -> None:
        super().__init__()
        self.set_size_request(DOT_SPACE + BAR_COUNT * (BAR_WIDTH + BAR_GAP), WAVE_HEIGHT)
        self.mode = "listening"
        self._levels: deque[float] = deque([0.0] * BAR_COUNT, maxlen=BAR_COUNT)
        self._target = 0.0
        self._smoothed = 0.0
        self.connect("draw", self._draw)

    def push_level(self, level: float) -> None:
        self._target = max(self._target, level)

    def reset(self) -> None:
        self._levels.extend([0.0] * BAR_COUNT)
        self._target = self._smoothed = 0.0

    def tick(self) -> None:
        self._smoothed += (self._target - self._smoothed) * 0.55
        self._levels.append(self._smoothed)
        self._target *= 0.5
        self.queue_draw()

    def _bar_heights(self, now: float) -> list[float]:
        if self.mode == "cleaning":
            return [0.25 + 0.35 * (1 + math.sin(now * 7 - i * 0.55)) / 2 for i in range(BAR_COUNT)]
        if self.mode == "error":
            return [0.12] * BAR_COUNT
        return [0.1 + 0.9 * level for level in self._levels]

    def _draw(self, _widget: Gtk.Widget, cr: cairo.Context) -> bool:
        now = time.monotonic()
        red, green, blue = COLORS[self.mode]
        center = WAVE_HEIGHT / 2

        pulse = 0.55 + 0.45 * (1 + math.sin(now * 5)) / 2 if self.mode == "listening" else 1.0
        cr.set_source_rgba(red, green, blue, pulse)
        cr.arc(DOT_RADIUS + 1, center, DOT_RADIUS, 0, 2 * math.pi)
        cr.fill()

        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_width(BAR_WIDTH)
        for index, height in enumerate(self._bar_heights(now)):
            x = DOT_SPACE + index * (BAR_WIDTH + BAR_GAP) + BAR_WIDTH / 2
            half = max(1.0, height * (WAVE_HEIGHT - BAR_WIDTH) / 2)
            alpha = 0.35 + 0.65 * (index + 1) / BAR_COUNT
            cr.set_source_rgba(red, green, blue, alpha)
            cr.move_to(x, center - half)
            cr.line_to(x, center + half)
            cr.stroke()
        return False


class OverlayView(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.get_style_context().add_class("ld-card")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.wave = Wave()
        self._status = Gtk.Label(xalign=0)
        self._status.get_style_context().add_class("ld-status")
        self._key = Gtk.Label(label="Esc")
        self._key.get_style_context().add_class("ld-key")
        self._key.set_valign(Gtk.Align.CENTER)
        header.pack_start(self.wave, False, False, 0)
        header.pack_start(self._status, True, True, 0)
        header.pack_end(self._key, False, False, 0)

        self._text = Gtk.Label(xalign=0)
        self._text.set_line_wrap(True)
        self._text.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._text.set_size_request(TEXT_WIDTH, -1)
        self._text.set_max_width_chars(1)
        self._text.get_style_context().add_class("ld-text")

        self.pack_start(header, False, False, 0)
        self.pack_start(self._text, False, False, 0)
        self.show_all()
        self._key.set_no_show_all(True)
        self._text.set_no_show_all(True)
        self._text.hide()

    def _measure_layout(self) -> Pango.Layout:
        layout = self._text.create_pango_layout(None)
        layout.set_width(TEXT_WIDTH * Pango.SCALE)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        return layout

    def _set(self, mode: str, status: str, text: str | None) -> None:
        self.wave.mode = mode
        self._status.set_text(status)
        self._key.set_visible(mode != "error")
        context = self._text.get_style_context()
        if mode == "error":
            context.add_class("ld-error")
        else:
            context.remove_class("ld-error")
        if text:
            self._text.set_text(last_lines(self._measure_layout(), text))
            self._text.show()
        else:
            self._text.hide()

    def listening(self) -> None:
        self.wave.reset()
        self._set("listening", "Listening", None)

    def partial(self, text: str) -> None:
        self._set("listening", "Listening", text)

    def cleaning(self, text: str | None) -> None:
        self._set("cleaning", "Cleaning up", text)

    def error(self, message: str) -> None:
        self._set("error", "Something went wrong", message)


def install_css() -> None:
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


def clear_background(_widget: Gtk.Widget, cr: cairo.Context) -> bool:
    cr.save()
    cr.set_operator(cairo.OPERATOR_CLEAR)
    cr.paint()
    cr.restore()
    return False


def monitor_at_pointer() -> Gdk.Monitor:
    display = Gdk.Display.get_default()
    _, x, y = display.get_default_seat().get_pointer().get_position()
    return display.get_monitor_at_point(x, y)


class Overlay:
    """Must only be called from the GTK main thread."""

    def __init__(self) -> None:
        install_css()
        # A POPUP window is override-redirect: it never takes focus from the paste target.
        self._window = Gtk.Window(type=Gtk.WindowType.POPUP)
        self._window.set_accept_focus(False)
        self._window.set_app_paintable(True)
        visual = self._window.get_screen().get_rgba_visual()
        if visual:
            self._window.set_visual(visual)
        self._window.connect("draw", clear_background)

        self.view = OverlayView()
        self._window.add(self.view)
        self._window.connect("size-allocate", self._reposition)
        self._monitor: Gdk.Monitor | None = None
        self._animation: int | None = None
        self._last_text = ""

    def _reposition(self, *_: object) -> None:
        if not self._monitor:
            return
        area = self._monitor.get_workarea()
        width, height = self._window.get_size()
        x = area.x + (area.width - width) // 2
        y = area.y + area.height - height - BOTTOM_MARGIN
        self._window.move(x, y)

    def _shrink_to_content(self) -> None:
        self._window.resize(1, 1)

    def _animate(self) -> bool:
        self.view.wave.tick()
        return True

    def _show(self) -> None:
        self._monitor = monitor_at_pointer()
        self._window.show()
        self._shrink_to_content()
        self._reposition()
        if self._animation is None:
            self._animation = GLib.timeout_add(FRAME_MS, self._animate)

    def listening(self) -> None:
        self._last_text = ""
        self.view.listening()
        self._show()

    def level(self, value: float) -> None:
        self.view.wave.push_level(value)

    def partial(self, text: str) -> None:
        self._last_text = text
        self.view.partial(text)
        self._shrink_to_content()

    def cleaning(self) -> None:
        self.view.cleaning(self._last_text)

    def error(self, message: str) -> None:
        self.view.error(message)
        if not self._window.get_visible():
            self._show()
        self._shrink_to_content()

    def hide(self) -> None:
        if self._animation is not None:
            GLib.source_remove(self._animation)
            self._animation = None
        self._window.hide()
