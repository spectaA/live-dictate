from __future__ import annotations

import math
import time
from html import escape

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from . import aura  # noqa: E402
from .textflow import (  # noqa: E402
    REMOVAL_S,
    LiveWords,
    first_word_of,
    removal_markup,
    removed_word_indices,
)

MAX_LINES = 4
TEXT_WIDTH = 460
CARD_RADIUS = 18
# The window is cropped to this margin: it must contain the whole glow.
WINDOW_MARGIN = aura.GLOW_REACH + 10
BOTTOM_MARGIN = 44
FRAME_MS = 33
ERROR_DISPLAY_S = 4
FINAL_TEXT_S = 0.5
PASTED_S = 0.9

ERROR_ACCENT = "#f59e0b"

CSS = b"""
.ld-card { padding: 12px 16px 14px 16px; }
.ld-status { color: #a1a1aa; font-size: 9.5pt; font-weight: 600; }
.ld-icon { color: #a1a1aa; }
.ld-icon-done { color: #22c55e; }
.ld-icon-error { color: #f59e0b; }
.ld-key {
    color: #a1a1aa;
    font-size: 8pt;
    border: 1px solid #3f3f46;
    border-radius: 5px;
    padding: 0 5px;
}
.ld-text { color: #f4f4f6; font-size: 12.5pt; }
"""

PLACEHOLDER = '<span foreground="#71717a">Speak…</span>'


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


class OverlayView(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.get_style_context().add_class("ld-card")
        self.set_margin_top(WINDOW_MARGIN)
        self.set_margin_bottom(WINDOW_MARGIN)
        self.set_margin_start(WINDOW_MARGIN)
        self.set_margin_end(WINDOW_MARGIN)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._icon = Gtk.Image()
        self._icon.set_pixel_size(14)
        self._status = Gtk.Label(xalign=0)
        self._status.get_style_context().add_class("ld-status")
        self._key = Gtk.Label(label="Esc")
        self._key.get_style_context().add_class("ld-key")
        self._key.set_valign(Gtk.Align.CENTER)
        header.pack_start(self._icon, False, False, 0)
        header.pack_start(self._status, True, True, 0)
        header.pack_end(self._key, False, False, 0)

        self.text = Gtk.Label(xalign=0)
        self.text.set_line_wrap(True)
        self.text.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.text.set_size_request(TEXT_WIDTH, -1)
        self.text.set_max_width_chars(1)
        self.text.get_style_context().add_class("ld-text")

        self.pack_start(header, False, False, 0)
        self.pack_start(self.text, False, False, 0)
        self.show_all()
        self._key.set_no_show_all(True)
        self.text.set_no_show_all(True)

    def header(self, icon: str, status: str, icon_class: str = "ld-icon", key: bool = True) -> None:
        self._icon.set_from_icon_name(icon, Gtk.IconSize.MENU)
        context = self._icon.get_style_context()
        for name in ("ld-icon", "ld-icon-done", "ld-icon-error"):
            context.remove_class(name)
        context.add_class(icon_class)
        self._status.set_text(status)
        self._key.set_visible(key)

    def show_markup(self, markup: str | None) -> None:
        if markup is None:
            self.text.hide()
            return
        if self.text.get_label() != markup:
            self.text.set_markup(markup)
        self.text.show()

    def tail(self, words: list[str]) -> int:
        """Index of the first word that fits in the last MAX_LINES lines."""
        layout = self.text.create_pango_layout(None)
        layout.set_width(TEXT_WIDTH * Pango.SCALE)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        return first_word_of(words, last_lines(layout, " ".join(words)))


def install_css() -> None:
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


def monitor_at_pointer() -> Gdk.Monitor:
    display = Gdk.Display.get_default()
    _, x, y = display.get_default_seat().get_pointer().get_position()
    return display.get_monitor_at_point(x, y)


class Overlay:
    """Must only be called from the GTK main thread."""

    def __init__(self, accent: str | None = None) -> None:
        install_css()
        self._palette = aura.palette(accent)
        self._error_palette = aura.palette(ERROR_ACCENT)
        # A POPUP window is override-redirect: it never takes focus from the paste target.
        self._window = Gtk.Window(type=Gtk.WindowType.POPUP)
        self._window.set_accept_focus(False)
        self._window.set_app_paintable(True)
        visual = self._window.get_screen().get_rgba_visual()
        if visual:
            self._window.set_visual(visual)
        self._window.connect("draw", self._draw)

        self.view = OverlayView()
        self._window.add(self.view)
        self._window.connect("size-allocate", self._reposition)
        self._monitor: Gdk.Monitor | None = None
        self._timer: int | None = None

        self._mode = "hidden"
        self._words = LiveWords()
        self._angle = 0.0
        self._glow = 0.0
        self._target_level = 0.0
        self._level = 0.0
        self._last_tick = time.monotonic()
        self._since = 0.0
        self._raw_words: list[str] = []
        self._removed: set[int] = set()
        self._final = ""
        self._message = ""

    def _draw(self, _window: Gtk.Window, cr: cairo.Context) -> bool:
        cr.save()
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.restore()
        if self._mode == "hidden":
            return False
        allocation = self.view.get_allocation()
        rect = (allocation.x, allocation.y, allocation.width, allocation.height)
        colors = self._error_palette if self._mode == "error" else self._palette
        aura.draw_card(cr, rect, CARD_RADIUS, colors, self._angle, self._glow)
        return False

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

    def _enter(self, mode: str) -> None:
        self._mode = mode
        self._since = time.monotonic()

    def _show(self) -> None:
        self._monitor = monitor_at_pointer()
        self._window.show()
        self._shrink_to_content()
        self._reposition()
        if self._timer is None:
            self._last_tick = time.monotonic()
            self._timer = GLib.timeout_add(FRAME_MS, self._tick)

    def _motion(self, now: float) -> tuple[float, float]:
        """Returns the ring's rotation speed in rad/s and the glow target for the current mode."""
        if self._mode == "listening":
            return 0.6 + 1.6 * self._level, 0.2 + 0.8 * self._level
        if self._mode == "cleaning":
            return 2.6, 0.45 + 0.15 * math.sin(now * 4)
        if self._mode == "error":
            return 0.3, 0.35
        return 1.0, 0.35

    def _tick(self) -> bool:
        now = time.monotonic()
        elapsed, self._last_tick = now - self._last_tick, now
        self._level += (self._target_level - self._level) * 0.4
        self._target_level *= 0.6
        speed, glow = self._motion(now)
        self._angle = (self._angle + speed * elapsed) % (2 * math.pi)
        self._glow += (glow - self._glow) * 0.25

        if self._mode == "listening" and self._words.words:
            first = self.view.tail(self._words.words)
            self.view.show_markup(self._words.markup(first, now))
        elif self._mode == "finishing":
            self._advance_finish(now - self._since)
        elif (self._mode == "pasted" and now - self._since >= PASTED_S) or (
            self._mode == "error" and now - self._since >= ERROR_DISPLAY_S
        ):
            return self._stop_from_tick()
        self._window.queue_draw()
        return True

    def _stop_from_tick(self) -> bool:
        # Returning False removes the timer; hide() must not remove it a second time.
        self._timer = None
        self.hide()
        return False

    def _advance_finish(self, elapsed: float) -> None:
        removal = REMOVAL_S if self._removed else 0.0
        if elapsed < removal:
            first = self.view.tail(self._raw_words)
            self.view.show_markup(removal_markup(self._raw_words, self._removed, first, elapsed))
        elif elapsed < removal + FINAL_TEXT_S:
            words = self._final.split()
            final = escape(" ".join(words[self.view.tail(words) :]))
            if self.view.text.get_label() != final:
                self.view.show_markup(final)
                self._shrink_to_content()
        else:
            self._enter("pasted")
            self.view.header("object-select-symbolic", "Pasted", "ld-icon-done", key=False)
            self.view.show_markup(None)
            self._shrink_to_content()

    def listening(self) -> None:
        self._enter("listening")
        self._words = LiveWords()
        self._target_level = self._level = 0.0
        self.view.header("audio-input-microphone-symbolic", "Listening")
        self.view.show_markup(PLACEHOLDER)
        self._show()

    def level(self, value: float) -> None:
        self._target_level = max(self._target_level, value)

    def partial(self, text: str) -> None:
        if self._mode != "listening":
            return
        self._words.update(text, time.monotonic())
        self._shrink_to_content()

    def cleaning(self) -> None:
        self._enter("cleaning")
        self.view.header("starred-symbolic", "Cleaning up")

    def finished(self, raw: str, final: str) -> None:
        """Animates the words the cleanup removed, confirms the paste, then hides."""
        self._raw_words = raw.split()
        self._removed = removed_word_indices(raw, final) if raw != final else set()
        self._final = final
        if self._mode != "cleaning":
            self.view.header("object-select-symbolic", "Pasted", "ld-icon-done", key=False)
        self._enter("finishing")
        if not self._window.get_visible():
            self._show()

    def error(self, message: str) -> None:
        self._enter("error")
        self.view.header("dialog-warning-symbolic", "Something went wrong", "ld-icon-error", False)
        self.view.show_markup(escape(message))
        if not self._window.get_visible():
            self._show()
        self._shrink_to_content()

    def hide(self) -> None:
        self._mode = "hidden"
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None
        self._window.hide()
