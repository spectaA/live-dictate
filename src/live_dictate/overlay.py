from __future__ import annotations

import math
import time
from collections.abc import Callable
from html import escape

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from . import aura, shortcuts  # noqa: E402
from .accent import hex_to_rgb  # noqa: E402
from .icons import RGB, draw_icon  # noqa: E402
from .textflow import (  # noqa: E402
    REMOVAL_S,
    LiveWords,
    first_word_of,
    removal_markup,
    removed_word_indices,
)

DEFAULT_MAX_LINES = 4
TEXT_WIDTH = 460
CARD_RADIUS = 18
# The window is cropped to this margin: it must contain the whole glow.
WINDOW_MARGIN = aura.GLOW_REACH + 10
BOTTOM_MARGIN = 44
FRAME_MS = 33
ERROR_DISPLAY_S = 5
FINAL_TEXT_S = 0.5
PASTED_S = 0.9
CHECK_DRAW_S = 0.35
TWINKLE_S = 1.2
SWEEP_S = 1.1

CLEANING_COLOR = "#60a5fa"
DONE_COLOR = "#22c55e"
ERROR_COLOR = "#f59e0b"
NEUTRAL_COLOR = "#d4d4d8"
MUTED = hex_to_rgb("#a1a1aa")
DIM = hex_to_rgb("#71717a")
BRIGHT = hex_to_rgb("#e4e4e7")
KEY_FILL = hex_to_rgb("#17171d")
KEY_BORDER = hex_to_rgb("#2e2e36")
TRACK = hex_to_rgb("#1f1f27")
DARK_TEXT = hex_to_rgb("#111111")

KEY_FONT = "Monospace Bold 7.5"
BADGE_FONT = "Sans Bold 8"

HOLD_HINT = "Release to paste · double-tap to lock"
TOGGLE_HINT = "Press the hotkey again to paste"

CSS = b"""
.ld-card { padding: 10px 12px 12px 12px; }
.ld-title { color: #e4e4e7; font-size: 9.5pt; font-weight: 600; }
.ld-meta { color: #71717a; font-family: monospace; font-size: 8.5pt; font-weight: 500; }
.ld-label { color: #71717a; font-size: 8pt; }
.ld-hint { color: #71717a; font-size: 8.5pt; }
.ld-text { color: #e4e4e7; font-size: 12.5pt; }
.ld-icon-button {
    background: none;
    border: none;
    box-shadow: none;
    border-radius: 7px;
    padding: 0;
    min-height: 24px;
    min-width: 24px;
}
.ld-icon-button:hover { background-color: #1f1f27; }
.ld-button {
    background-image: none;
    background-color: #1f1f27;
    border: 1px solid #2e2e36;
    border-radius: 7px;
    box-shadow: none;
    text-shadow: none;
    color: #e4e4e7;
    font-size: 8.5pt;
    font-weight: 600;
    padding: 1px 9px;
    min-height: 0;
}
.ld-button:hover { background-color: #2a2a33; }
"""

Parts = set[str]


def last_lines(layout: Pango.Layout, text: str, max_lines: int = DEFAULT_MAX_LINES) -> str:
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


def format_timer(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


class Painted(Gtk.DrawingArea):
    def __init__(self, width: int, height: int) -> None:
        super().__init__()
        self.set_size_request(width, height)
        self.set_valign(Gtk.Align.CENTER)
        self.connect("draw", self._on_draw)

    def _on_draw(self, _widget: Gtk.Widget, cr: cairo.Context) -> bool:
        self.paint(cr, self.get_allocated_width(), self.get_allocated_height())
        return False

    def paint(self, cr: cairo.Context, width: int, height: int) -> None:
        raise NotImplementedError

    def text_layout(self, text: str, font: str) -> Pango.Layout:
        layout = self.create_pango_layout(text)
        layout.set_font_description(Pango.FontDescription.from_string(font))
        return layout


class Icon(Painted):
    def __init__(self, name: str, size: int, color: RGB) -> None:
        super().__init__(size, size)
        self.name, self.size, self.color = name, size, color

    def paint(self, cr: cairo.Context, width: int, height: int) -> None:
        draw_icon(
            cr, self.name, (width - self.size) / 2, (height - self.size) / 2, self.size, self.color
        )


class Chip(Painted):
    """The tinted state disc with its icon, which can twinkle or draw itself."""

    def __init__(self) -> None:
        super().__init__(24, 24)
        self.icon = "mic"
        self.color: RGB = BRIGHT
        self.twinkle = False
        self.progress = 1.0
        self.now = 0.0

    def configure(self, icon: str, color: RGB, twinkle: bool = False) -> None:
        self.icon, self.color, self.twinkle, self.progress = icon, color, twinkle, 1.0

    def paint(self, cr: cairo.Context, width: int, height: int) -> None:
        cx, cy = width / 2, height / 2
        cr.arc(cx, cy, 12, 0, 2 * math.pi)
        cr.set_source_rgba(*self.color, 0.18)
        cr.fill()
        phase = 0.0
        if self.twinkle:
            phase = (1 - math.cos(self.now * 2 * math.pi / TWINKLE_S)) / 2
        cr.save()
        cr.translate(cx, cy)
        cr.rotate(math.radians(15) * phase)
        cr.scale(1 - 0.2 * phase, 1 - 0.2 * phase)
        draw_icon(cr, self.icon, -7, -7, 14, self.color, 1 - 0.3 * phase, self.progress)
        cr.restore()


class Equalizer(Painted):
    BARS = 5
    BAR_WIDTH = 2.5
    GAP = 2.0

    def __init__(self, color: RGB) -> None:
        super().__init__(21, 14)
        self.color = color
        self.level = 0.0
        self.now = 0.0

    def paint(self, cr: cairo.Context, width: int, height: int) -> None:
        cr.set_source_rgb(*self.color)
        for index in range(self.BARS):
            wave = abs(math.sin(self.now * 6 + index * 1.3) * math.sin(self.now * 2.1 + index))
            bar = 3 + (height - 3) * self.level * (0.45 + 0.55 * wave)
            x = index * (self.BAR_WIDTH + self.GAP)
            aura.rounded_rect(cr, x, (height - bar) / 2, self.BAR_WIDTH, bar, self.BAR_WIDTH / 2)
            cr.fill()


class Keycap(Painted):
    """A key drawn like a real keycap, with a thicker bottom edge."""

    PAD = 6
    ICON = 10

    def __init__(self, label: str, icon: str | None = None, accent: RGB | None = None) -> None:
        super().__init__(1, 1)
        self.label, self.icon, self.accent = label, icon, accent
        text_width, text_height = self.text_layout(label, KEY_FONT).get_pixel_size()
        icon_width = self.ICON + 3 if icon else 0
        self.set_size_request(2 * self.PAD + icon_width + text_width, text_height + 5)

    def paint(self, cr: cairo.Context, width: int, height: int) -> None:
        fill, border, ink = KEY_FILL, KEY_BORDER, MUTED
        if self.accent:
            red, green, blue = self.accent
            fill, border, ink = self.accent, (red * 0.7, green * 0.7, blue * 0.7), DARK_TEXT
        cr.set_source_rgb(*border)
        aura.rounded_rect(cr, 0, 0, width, height, 6)
        cr.fill()
        cr.set_source_rgb(*fill)
        aura.rounded_rect(cr, 1, 1, width - 2, height - 3, 5)
        cr.fill()
        x = self.PAD
        if self.icon:
            draw_icon(cr, self.icon, x, (height - 1 - self.ICON) / 2, self.ICON, ink)
            x += self.ICON + 3
        layout = self.text_layout(self.label, KEY_FONT)
        _, text_height = layout.get_pixel_size()
        cr.set_source_rgb(*ink)
        cr.move_to(x, (height - 1 - text_height) / 2)
        PangoCairo.show_layout(cr, layout)


class Badge(Painted):
    """The "Locked" pill."""

    LABEL = "Locked"

    def __init__(self, color: RGB) -> None:
        super().__init__(1, 1)
        self.color = color
        text_width, text_height = self.text_layout(self.LABEL, BADGE_FONT).get_pixel_size()
        self.set_size_request(8 + 11 + 5 + text_width + 8, text_height + 4)

    def paint(self, cr: cairo.Context, width: int, height: int) -> None:
        cr.set_source_rgba(*self.color, 0.14)
        aura.rounded_rect(cr, 0, 0, width, height, height / 2)
        cr.fill()
        draw_icon(cr, "lock", 8, (height - 11) / 2, 11, self.color)
        layout = self.text_layout(self.LABEL, BADGE_FONT)
        _, text_height = layout.get_pixel_size()
        cr.set_source_rgb(*self.color)
        cr.move_to(8 + 11 + 5, (height - text_height) / 2)
        PangoCairo.show_layout(cr, layout)


class Sweep(Painted):
    """An indeterminate progress bar."""

    def __init__(self, color: RGB) -> None:
        super().__init__(-1, 2)
        self.set_valign(Gtk.Align.FILL)
        self.color = color
        self.now = 0.0

    def paint(self, cr: cairo.Context, width: int, height: int) -> None:
        cr.set_source_rgb(*TRACK)
        aura.rounded_rect(cr, 0, 0, width, height, height / 2)
        cr.fill()
        segment = width * 0.4
        x = -segment + (self.now / SWEEP_S % 1.0) * (width + segment)
        gradient = cairo.LinearGradient(x, 0, x + segment, 0)
        gradient.add_color_stop_rgba(0, *self.color, 0)
        gradient.add_color_stop_rgba(0.5, *self.color, 1)
        gradient.add_color_stop_rgba(1, *self.color, 0)
        cr.set_source(gradient)
        cr.rectangle(0, 0, width, height)
        cr.fill()


def _styled(widget: Gtk.Widget, name: str) -> Gtk.Widget:
    widget.get_style_context().add_class(name)
    return widget


def _keycaps(spec: str, accent: RGB | None = None) -> Gtk.Box:
    box = Gtk.Box(spacing=3)
    if not spec:
        return box
    for label in shortcuts.labels(spec):
        icon = "enter" if label == "Enter" else None
        box.pack_start(Keycap(label, icon=icon, accent=accent), False, False, 0)
    return box


class OverlayView(Gtk.Box):
    def __init__(
        self,
        accent: RGB,
        max_lines: int,
        hint: str,
        lock_key: str = "<space>",
        finish_key: str = "<enter>",
        cancel_key: str = "<esc>",
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.max_lines = max_lines
        self.settings_enabled = False
        self.lock_enabled = bool(lock_key)
        _styled(self, "ld-card")
        for side in ("top", "bottom", "start", "end"):
            getattr(self, f"set_margin_{side}")(WINDOW_MARGIN)

        self.chip = Chip()
        self.eq = Equalizer(accent)
        self.badge = Badge(accent)
        self.title = _styled(Gtk.Label(), "ld-title")
        self.meta = _styled(Gtk.Label(), "ld-meta")
        self.lock = Gtk.Box(spacing=4)
        self.lock.pack_start(Icon("lock", 11, DIM), False, False, 0)
        self.lock.pack_start(_keycaps(lock_key), False, False, 0)
        self.lock.set_tooltip_text(
            f"Press {shortcuts.describe(lock_key)} while holding the hotkey to lock"
        )
        self.finish = _styled(Gtk.Label(label="finish"), "ld-label")
        self.finish_keys = _keycaps(finish_key, accent)
        self.cancel = _keycaps(cancel_key)
        self.settings = _styled(Gtk.Button(relief=Gtk.ReliefStyle.NONE), "ld-icon-button")
        self.settings.set_tooltip_text("Settings")
        self.settings.set_valign(Gtk.Align.CENTER)
        self._gear = Icon("sliders", 14, DIM)
        self.settings.add(self._gear)
        self.settings.connect("state-flags-changed", self._on_settings_state)
        self.error_button = _styled(Gtk.Button(label="Settings"), "ld-button")
        self.error_button.set_valign(Gtk.Align.CENTER)

        header = Gtk.Box(spacing=8)
        header.set_size_request(-1, 26)
        for widget in (self.chip, self.eq, self.badge, self.title, self.meta):
            header.pack_start(widget, False, False, 0)
        for widget in (
            self.error_button,
            self.settings,
            self.cancel,
            self.finish_keys,
            self.finish,
            self.lock,
        ):
            header.pack_end(widget, False, False, 0)

        self.text = _styled(Gtk.Label(xalign=0), "ld-text")
        self.text.set_line_wrap(True)
        self.text.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.text.set_size_request(TEXT_WIDTH, -1)
        self.text.set_max_width_chars(1)
        self.text.set_margin_top(8)
        self.hint = _styled(Gtk.Label(label=hint, xalign=0), "ld-hint")
        self.hint.set_margin_top(6)
        self.sweep = Sweep(hex_to_rgb(CLEANING_COLOR))
        self.sweep.set_margin_top(10)

        self.pack_start(header, False, False, 0)
        self.pack_start(self.text, False, False, 0)
        self.pack_start(self.hint, False, False, 0)
        self.pack_start(self.sweep, False, False, 0)
        self.show_all()
        self._parts = {
            "eq": self.eq,
            "badge": self.badge,
            "title": self.title,
            "meta": self.meta,
            "lock": self.lock,
            "finish": self.finish,
            "finish_keys": self.finish_keys,
            "cancel": self.cancel,
            "settings": self.settings,
            "error_button": self.error_button,
            "text": self.text,
            "hint": self.hint,
            "sweep": self.sweep,
        }
        for widget in self._parts.values():
            widget.set_no_show_all(True)
        self.show_parts(set())

    def _on_settings_state(self, button: Gtk.Button, _previous: Gtk.StateFlags) -> None:
        hovered = bool(button.get_state_flags() & Gtk.StateFlags.PRELIGHT)
        self._gear.color = BRIGHT if hovered else DIM
        self._gear.queue_draw()

    def show_parts(self, parts: Parts) -> None:
        if not self.settings_enabled:
            parts = parts - {"settings", "error_button"}
        if not self.lock_enabled:
            parts = parts - {"lock"}
        for name, widget in self._parts.items():
            widget.set_visible(name in parts)

    def visible_parts(self) -> Parts:
        return {name for name, widget in self._parts.items() if widget.get_visible()}

    def show_markup(self, markup: str) -> None:
        if self.text.get_label() != markup:
            self.text.set_markup(markup)

    def tail(self, words: list[str]) -> int:
        """Index of the first word that fits in the last `max_lines` lines."""
        layout = self.text.create_pango_layout(None)
        layout.set_width(TEXT_WIDTH * Pango.SCALE)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        return first_word_of(words, last_lines(layout, " ".join(words), self.max_lines))


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

    def __init__(
        self,
        accent: str | None = None,
        on_settings: Callable[[], None] | None = None,
        max_lines: int = DEFAULT_MAX_LINES,
        hold: bool = True,
        lock_key: str = "<space>",
        finish_key: str = "<enter>",
        cancel_key: str = "<esc>",
    ) -> None:
        install_css()
        self._accent = hex_to_rgb(accent or NEUTRAL_COLOR)
        self._palettes = {
            "accent": aura.palette(accent),
            "cleaning": aura.palette(CLEANING_COLOR),
            "done": aura.palette(DONE_COLOR),
            "error": aura.palette(ERROR_COLOR),
        }
        self._ring = "accent"
        # A POPUP window is override-redirect: it never takes focus from the paste target.
        self._window = Gtk.Window(type=Gtk.WindowType.POPUP)
        self._window.set_accept_focus(False)
        self._window.set_app_paintable(True)
        visual = self._window.get_screen().get_rgba_visual()
        if visual:
            self._window.set_visual(visual)
        self._window.connect("draw", self._draw)

        hint = HOLD_HINT if hold else TOGGLE_HINT
        self.view = OverlayView(
            self._accent,
            max_lines,
            hint,
            lock_key if hold else "",
            finish_key,
            cancel_key,
        )
        self._on_settings = on_settings
        if on_settings:
            self.view.settings_enabled = True
            self.view.settings.connect("clicked", lambda _button: on_settings())
            self.view.error_button.connect("clicked", self._settings_from_error)
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
        self._started = 0.0
        self._raw_words: list[str] = []
        self._removed: set[int] = set()
        self._final = ""

    def _draw(self, _window: Gtk.Window, cr: cairo.Context) -> bool:
        cr.save()
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.restore()
        if self._mode == "hidden":
            return False
        allocation = self.view.get_allocation()
        rect = (allocation.x, allocation.y, allocation.width, allocation.height)
        colors = self._palettes[self._ring]
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
        self._limit_input_to_card()

    def _limit_input_to_card(self) -> None:
        # The transparent glow margin must not swallow clicks meant for the window below.
        gdk_window = self._window.get_window()
        if gdk_window is None:
            return
        card = self.view.get_allocation()
        rect = cairo.RectangleInt(card.x, card.y, card.width, card.height)
        gdk_window.input_shape_combine_region(cairo.Region(rect), 0, 0)

    def _shrink_to_content(self) -> None:
        self._window.resize(1, 1)

    def _enter(self, mode: str, parts: Parts, ring: str) -> None:
        self._mode = mode
        self._ring = ring
        self._since = time.monotonic()
        self.view.show_parts(parts)
        self._shrink_to_content()

    def _show(self) -> None:
        self._monitor = monitor_at_pointer()
        self._window.show()
        self._shrink_to_content()
        self._reposition()
        if self._timer is None:
            self._last_tick = time.monotonic()
            self._timer = GLib.timeout_add(FRAME_MS, self._tick)

    def _settings_from_error(self, _button: Gtk.Button) -> None:
        self.hide()
        if self._on_settings:
            self._on_settings()

    def _motion(self, now: float) -> tuple[float, float]:
        """Returns the ring's rotation speed in rad/s and the glow target for the current mode."""
        if self._mode == "listening":
            return 0.6 + 1.6 * self._level, 0.2 + 0.8 * self._level
        if self._mode == "locked":
            return 0.35, 0.25 + 0.45 * self._level
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
        view = self.view
        view.chip.now = view.sweep.now = view.eq.now = now
        view.eq.level = self._level

        if self._mode in ("listening", "locked"):
            view.meta.set_text(format_timer(now - self._started))
            if self._words.words:
                self._show_live_text(now)
        elif self._mode == "finishing":
            self._advance_finish(now - self._since)
        elif self._mode == "pasted":
            view.chip.progress = min(1.0, (now - self._since) / CHECK_DRAW_S)
            if now - self._since >= PASTED_S:
                return self._stop_from_tick()
        elif self._mode == "error" and now - self._since >= ERROR_DISPLAY_S:
            return self._stop_from_tick()
        self._window.queue_draw()
        return True

    def _show_live_text(self, now: float) -> None:
        if not self.view.text.get_visible():
            self.view.show_parts(self.view.visible_parts() - {"hint"} | {"text"})
            self._shrink_to_content()
        first = self.view.tail(self._words.words)
        self.view.show_markup(self._words.markup(first, now))

    def _stop_from_tick(self) -> bool:
        # Returning False removes the timer; hide() must not remove it a second time.
        self._timer = None
        self.hide()
        return False

    def _tail_markup(self, words: list[str]) -> str:
        return escape(" ".join(words[self.view.tail(words) :]))

    def _advance_finish(self, elapsed: float) -> None:
        removal = REMOVAL_S if self._removed else 0.0
        if elapsed < removal:
            first = self.view.tail(self._raw_words)
            self.view.show_markup(removal_markup(self._raw_words, self._removed, first, elapsed))
        elif elapsed < removal + FINAL_TEXT_S:
            final = self._tail_markup(self._final.split())
            if self.view.text.get_label() != final:
                self.view.show_markup(final)
                self._shrink_to_content()
        else:
            self._pasted()

    def _pasted(self) -> None:
        self.view.chip.configure("check", hex_to_rgb(DONE_COLOR))
        self.view.chip.progress = 0.0
        self.view.title.set_text("Pasted")
        self.view.meta.set_text("· " + _plural(len(self._final.split()), "word"))
        self._enter("pasted", {"title", "meta"}, "done")

    def _live_parts(self, base: Parts) -> Parts:
        return base | {"text"} if self._words.words else base

    def listening(self) -> None:
        self._words = LiveWords()
        self._target_level = self._level = 0.0
        self._started = time.monotonic()
        self.view.chip.configure("mic", self._accent)
        self.view.meta.set_text(format_timer(0))
        self._enter("listening", {"eq", "meta", "lock", "cancel", "settings", "hint"}, "accent")
        self._show()

    def locked(self) -> None:
        if self._mode != "listening":
            return
        self.view.chip.configure("lock", self._accent)
        parts = self._live_parts({"badge", "meta", "finish", "finish_keys", "cancel", "settings"})
        self._enter("locked", parts, "accent")

    def level(self, value: float) -> None:
        self._target_level = max(self._target_level, value)

    def partial(self, text: str) -> None:
        if self._mode not in ("listening", "locked"):
            return
        self._words.update(text, time.monotonic())
        self._shrink_to_content()

    def cleaning(self) -> None:
        self.view.chip.configure("sparkles", hex_to_rgb(CLEANING_COLOR), twinkle=True)
        self.view.title.set_text("Cleaning up")
        if self._words.words:
            self.view.show_markup(self._tail_markup(self._words.words))
        self._enter("cleaning", self._live_parts({"title", "cancel", "sweep"}), "cleaning")

    def finished(self, raw: str, final: str) -> None:
        """Animates the words the cleanup removed, confirms the paste, then hides."""
        self._raw_words = raw.split()
        self._removed = removed_word_indices(raw, final) if raw != final else set()
        self._final = final
        parts: Parts = {"title", "text"}
        if self._mode == "cleaning":
            self.view.chip.configure("sparkles", hex_to_rgb(CLEANING_COLOR))
            if self._removed:
                self.view.meta.set_text("· " + _plural(len(self._removed), "word") + " removed")
                parts.add("meta")
            ring = "cleaning"
        else:
            self.view.chip.configure("check", hex_to_rgb(DONE_COLOR))
            self.view.title.set_text("Pasted")
            ring = "done"
        self._enter("finishing", parts, ring)
        if not self._window.get_visible():
            self._show()

    def error(self, title: str, detail: str = "", settings: bool = False) -> None:
        self.view.chip.configure("alert", hex_to_rgb(ERROR_COLOR))
        self.view.title.set_text(title)
        parts: Parts = {"title"}
        if detail:
            self.view.show_markup(
                f'<span size="smaller" foreground="#a1a1aa">{escape(detail)}</span>'
            )
            parts.add("text")
        if settings:
            parts.add("error_button")
        self._enter("error", parts, "error")
        if not self._window.get_visible():
            self._show()

    def hide(self) -> None:
        self._mode = "hidden"
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None
        self._window.hide()
