from __future__ import annotations

import math
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Any

import cairo
import gi
import httpx

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from . import aura, history, recent, shortcuts  # noqa: E402
from .accent import resolve_accent  # noqa: E402
from .config import (  # noqa: E402
    KEYWORD_FORBIDDEN,
    MAX_LINES_LIMIT,
    Config,
    default_config_path,
    default_history_path,
    has_api_key,
    load_config,
    store_api_key,
    update_config_file,
)
from .history import format_duration  # noqa: E402
from .keygrab import is_free  # noqa: E402
from .textflow import STRIKE_S, removal_markup, removed_word_indices  # noqa: E402

API_KEYS_URL = "https://platform.openai.com/api-keys"
MIN_WIDTH = 440
MODELS_URL = "https://api.openai.com/v1/models"

LANGUAGES = [
    ("", "Auto-detect"),
    ("en", "English"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("it", "Italiano"),
    ("nl", "Nederlands"),
    ("pt", "Português"),
    ("pl", "Polski"),
    ("sv", "Svenska"),
    ("uk", "Українська"),
    ("ja", "日本語"),
    ("zh", "中文"),
]

ACCENT_PRESETS = [
    ("", "Auto: your desktop accent color, or neutral gray"),
    ("#fbb86c", "Orange"),
    ("#3b82f6", "Blue"),
    ("#8b5cf6", "Purple"),
    ("#22c55e", "Green"),
    ("#f472b6", "Pink"),
    ("#22d3ee", "Cyan"),
]
SWATCH_SIZE = 26
PREVIEW_HEIGHT = 96
FRAME_MS = 33
WINDOW_WIDTH = 600
CHART_HEIGHT = 64
RECENT_COUNT = 3
RECOVERY_HEIGHT = 380
OUTCOMES = {
    "pasted": "pasted",
    "transcribed": "not pasted",
    "cancelled": "cancelled",
    "error": "failed",
}

MODES = [("hold", "Hold to talk"), ("toggle", "Press to start, press again to stop")]

CLEANUP_OPTIONS = [
    ("off", "Off", "Paste exactly what was transcribed.", None),
    (
        "light",
        "Light",
        "Remove filler words and stutters only.",
        (
            "euh je je voulais dire que bah la réunion est décalée",
            "Je voulais dire que la réunion est décalée.",
        ),
    ),
    (
        "standard",
        "Standard",
        "Recommended. Also fixes slips, repetitions and self-corrections, without rewording.",
        (
            "la réunion de mardi non de jeudi elle est elle est décalée",
            "La réunion de jeudi, elle est décalée.",
        ),
    ),
    (
        "polished",
        "Polished",
        "Smoother written style with the same meaning.",
        (
            "la réunion elle est décalée parce que le client il a pas validé",
            "La réunion est décalée, car le client n'a pas validé.",
        ),
    ),
]

PERIODS = [(7, "7 days"), (30, "30 days"), (0, "All")]

CSS = b"""
radiobutton.ld-option {
    border: 1px solid alpha(@theme_fg_color, 0.12);
    border-radius: 8px;
    padding: 10px 12px;
}
radiobutton.ld-option:checked { border-color: @theme_selected_bg_color; }
.ld-stat {
    background-color: alpha(@theme_fg_color, 0.05);
    border-radius: 8px;
    padding: 10px 12px;
}
.ld-stat-value { font-size: 15pt; font-weight: bold; }
.ld-key {
    background-color: alpha(@theme_fg_color, 0.1);
    border: 1px solid alpha(@theme_fg_color, 0.15);
    border-bottom-width: 2px;
    border-radius: 5px;
    padding: 0 7px;
    font-weight: bold;
    font-size: 9pt;
}
.ld-capturing { box-shadow: inset 0 0 0 2px @theme_selected_bg_color; }
.ld-warning { color: #f5b450; }
.ld-recent {
    background-color: alpha(@theme_fg_color, 0.04);
    border-radius: 8px;
    padding: 8px 12px;
}
"""


def check_api_key(client: httpx.Client, key: str, model: str) -> str | None:
    """Returns None when the key can use `model`, otherwise a message for the user."""
    try:
        response = client.get(f"{MODELS_URL}/{model}", headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as error:
        return f"Could not reach OpenAI: {error}"
    if response.status_code == 200:
        return None
    if response.status_code == 401:
        return "OpenAI rejected this key."
    if response.status_code == 404:
        return f"This key has no access to {model}."
    return f"OpenAI answered with HTTP {response.status_code}."


def _combo(options: list[tuple[str, str]], selected: str) -> Gtk.ComboBoxText:
    combo = Gtk.ComboBoxText(hexpand=True)
    if selected not in {value for value, _ in options}:
        options = [*options, (selected, selected)]
    for value, label in options:
        combo.append(value, label)
    combo.set_active_id(selected)
    return combo


def _rgba_to_hex(rgba: Gdk.RGBA) -> str:
    channels = (rgba.red, rgba.green, rgba.blue)
    return "#" + "".join(f"{round(channel * 255):02x}" for channel in channels)


class Swatch(Gtk.Button):
    def __init__(self, color: str | None, tooltip: str) -> None:
        super().__init__(relief=Gtk.ReliefStyle.NONE, tooltip_text=tooltip)
        self._rgb = aura.palette(color).stops[0]
        self.selected = False
        area = Gtk.DrawingArea()
        area.set_size_request(SWATCH_SIZE, SWATCH_SIZE)
        area.connect("draw", self._draw)
        self.add(area)

    def _draw(self, area: Gtk.DrawingArea, cr: cairo.Context) -> bool:
        center = SWATCH_SIZE / 2
        cr.set_source_rgb(*self._rgb)
        cr.arc(center, center, 8.5, 0, 2 * math.pi)
        cr.fill()
        if self.selected:
            cr.set_source_rgba(*self._rgb, 0.9)
            cr.set_line_width(2)
            cr.arc(center, center, 11.5, 0, 2 * math.pi)
            cr.stroke()
        return False


class AccentPicker(Gtk.Box):
    def __init__(self, value: str, on_change: Callable[[str], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._on_change = on_change
        self._swatches: dict[str, Swatch] = {}
        system = resolve_accent("")
        for color, tooltip in ACCENT_PRESETS:
            swatch = Swatch(color or system, tooltip)
            swatch.connect("clicked", lambda _button, chosen=color: self.select(chosen))
            self._swatches[color] = swatch
            self.pack_start(swatch, False, False, 0)
        self._custom = Gtk.ColorButton(use_alpha=False, tooltip_text="Custom color")
        self._custom.connect("color-set", self._on_custom)
        self.pack_start(self._custom, False, False, 6)
        self.value = value
        self.select(value)

    def _on_custom(self, button: Gtk.ColorButton) -> None:
        self.select(_rgba_to_hex(button.get_rgba()))

    def select(self, value: str) -> None:
        self.value = value
        for color, swatch in self._swatches.items():
            swatch.selected = color == value
            swatch.queue_draw()
        if value and value not in self._swatches:
            rgba = Gdk.RGBA()
            rgba.parse(value)
            self._custom.set_rgba(rgba)
        self._on_change(value)


class AuraPreview(Gtk.DrawingArea):
    def __init__(self, accent: str) -> None:
        super().__init__()
        self.set_size_request(MIN_WIDTH, PREVIEW_HEIGHT)
        self._palette = aura.palette(resolve_accent(accent))
        self._started = time.monotonic()
        self.connect("draw", self._draw)
        timer = GLib.timeout_add(FRAME_MS, self._animate)
        self.connect("destroy", lambda _area: GLib.source_remove(timer))

    def set_accent(self, accent: str) -> None:
        self._palette = aura.palette(resolve_accent(accent))
        self.queue_draw()

    def _animate(self) -> bool:
        self.queue_draw()
        return True

    def _draw(self, _area: Gtk.DrawingArea, cr: cairo.Context) -> bool:
        elapsed = time.monotonic() - self._started
        reach = aura.GLOW_REACH
        width = self.get_allocated_width()
        rect = (reach, reach, width - 2 * reach, PREVIEW_HEIGHT - 2 * reach)
        glow = 0.55 + 0.35 * abs(math.sin(elapsed * 2.6) * math.sin(elapsed * 1.1))
        aura.draw_card(cr, rect, 14, self._palette, elapsed * 1.2, glow)
        layout = self.create_pango_layout("Hold the hotkey and speak…")
        _, logical = layout.get_pixel_extents()
        cr.set_source_rgb(0.96, 0.96, 0.97)
        cr.move_to(reach + 16, (PREVIEW_HEIGHT - logical.height) / 2)
        PangoCairo.show_layout(cr, layout)
        return False


def _install_css() -> None:
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


def _label(text: str = "", *, markup: str | None = None, dim: bool = False) -> Gtk.Label:
    label = Gtk.Label(xalign=0, wrap=True, max_width_chars=60)
    if markup is None:
        label.set_text(text)
    else:
        label.set_markup(markup)
    if dim:
        label.get_style_context().add_class("dim-label")
    return label


def _grid() -> Gtk.Grid:
    return Gtk.Grid(row_spacing=12, column_spacing=16, margin=24)


def _add_row(grid: Gtk.Grid, row: int, label: str, widget: Gtk.Widget) -> None:
    grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
    grid.attach(widget, 1, row, 1, 1)


def _example_markup(raw: str, clean: str) -> str:
    removed = removed_word_indices(raw, clean)
    struck = removal_markup(raw.split(), removed, 0, STRIKE_S)
    return f'<small>{struck}  <span alpha="50%">→</span>  {GLib.markup_escape_text(clean)}</small>'


class StatsChart(Gtk.DrawingArea):
    """Bars of dictations per day, oldest on the left."""

    def __init__(self) -> None:
        super().__init__()
        self.set_size_request(-1, CHART_HEIGHT)
        self.counts: list[int] = []
        self.connect("draw", self._draw)

    def _draw(self, _area: Gtk.DrawingArea, cr: cairo.Context) -> bool:
        if not self.counts:
            return False
        width = self.get_allocated_width()
        peak = max(max(self.counts), 1)
        slot = width / len(self.counts)
        color = self.get_style_context().lookup_color("theme_selected_bg_color")
        rgba = color[1] if color[0] else Gdk.RGBA(0.4, 0.7, 0.8, 1)
        cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.85)
        for index, count in enumerate(self.counts):
            height = (CHART_HEIGHT - 4) * count / peak
            if count:
                aura.rounded_rect(
                    cr, index * slot + 1.5, CHART_HEIGHT - height, max(slot - 3, 1), height, 2
                )
                cr.fill()
        return False


class HistoryPage(Gtk.Box):
    def __init__(self, config: Config, history_path: Path) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=24)
        self._path = history_path
        self._days = 30

        switch_row = Gtk.Box(spacing=12)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        texts.pack_start(_label(markup="<b>Keep a history of dictations</b>"), False, False, 0)
        texts.pack_start(
            _label("Stored only on this computer, readable by you only.", dim=True), False, False, 0
        )
        self.enabled = Gtk.Switch(active=config.history_enabled, valign=Gtk.Align.CENTER)
        switch_row.pack_start(texts, True, True, 0)
        switch_row.pack_end(self.enabled, False, False, 0)
        self.pack_start(switch_row, False, False, 0)

        retention_row = Gtk.Box(spacing=8)
        retention_row.pack_start(Gtk.Label(label="Keep entries for"), False, False, 0)
        self.retention = Gtk.SpinButton.new_with_range(1, 365, 1)
        self.retention.set_value(config.history_retention_days)
        retention_row.pack_start(self.retention, False, False, 0)
        retention_row.pack_start(_label("days", dim=True), False, False, 0)
        self.pack_start(retention_row, False, False, 0)

        toolbar = Gtk.Box(spacing=8)
        periods = Gtk.Box()
        periods.get_style_context().add_class("linked")
        group: Gtk.RadioButton | None = None
        for days, label in PERIODS:
            button = Gtk.RadioButton.new_with_label_from_widget(group, label)
            button.set_mode(False)
            button.set_active(days == self._days)
            button.connect("toggled", self._on_period, days)
            periods.pack_start(button, False, False, 0)
            group = group or button
        self.open_file = Gtk.Button(label="Open history file")
        self.open_file.connect("clicked", self._on_open)
        toolbar.pack_start(periods, False, False, 0)
        toolbar.pack_end(self.open_file, False, False, 0)
        self.pack_start(toolbar, False, False, 0)

        self._stats = Gtk.Grid(row_spacing=8, column_spacing=8, column_homogeneous=True)
        self._values: dict[str, Gtk.Label] = {}
        captions = [
            ("dictations", "dictations"),
            ("speaking", "speaking time"),
            ("cost", "estimated cost"),
            ("wait", "avg. wait after release"),
            ("words", "words dictated"),
            ("removed", "words removed by cleanup"),
        ]
        for index, (key, caption) in enumerate(captions):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            card.get_style_context().add_class("ld-stat")
            value = Gtk.Label(xalign=0)
            value.get_style_context().add_class("ld-stat-value")
            card.pack_start(value, False, False, 0)
            card.pack_start(_label(caption, dim=True), False, False, 0)
            self._values[key] = value
            self._stats.attach(card, index % 3, index // 3, 1, 1)
        self.pack_start(self._stats, False, False, 0)

        self.pack_start(_label("Dictations per day", dim=True), False, False, 0)
        self._chart = StatsChart()
        self.pack_start(self._chart, False, False, 0)

        self._recent = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.pack_start(self._recent, False, False, 0)
        self._empty = _label(dim=True)
        self.pack_start(self._empty, False, False, 0)
        self.refresh()

    def _on_period(self, button: Gtk.RadioButton, days: int) -> None:
        if button.get_active():
            self._days = days
            self.refresh()

    def _on_open(self, _button: Gtk.Button) -> None:
        Gio.AppInfo.launch_default_for_uri(self._path.as_uri(), None)

    def refresh(self) -> None:
        now = datetime.now(timezone.utc)
        since = now - timedelta(days=self._days) if self._days else None
        entries = history.load(self._path, since)
        stats = history.summarize(entries)
        self.open_file.set_sensitive(self._path.exists())

        self._values["dictations"].set_text(f"{stats.dictations:,}")
        self._values["speaking"].set_text(format_duration(stats.speaking_s))
        self._values["cost"].set_text(f"${stats.cost_usd:.2f}")
        wait = stats.average_wait_s
        self._values["wait"].set_text("-" if wait is None else f"{wait:.1f} s")
        self._values["words"].set_text(f"{stats.words:,}")
        ratio = stats.removed_ratio
        self._values["removed"].set_text("-" if ratio is None else f"{ratio:.1%}")

        span = self._days or max(
            ((now.date() - min(stats.per_day)).days + 1) if stats.per_day else 1, 1
        )
        today = now.astimezone().date()
        self._chart.counts = [
            stats.per_day.get(today - timedelta(days=offset), 0)
            for offset in range(span - 1, -1, -1)
        ]
        self._chart.queue_draw()

        for child in self._recent.get_children():
            self._recent.remove(child)
        pasted = [entry for entry in entries if entry.get("outcome") == "pasted"]
        pasted.sort(key=lambda entry: str(entry["time"]), reverse=True)
        for entry in pasted[:RECENT_COUNT]:
            self._recent.pack_start(self._recent_row(entry), False, False, 0)
        self._recent.show_all()

        if not entries:
            hint = "" if self.enabled.get_active() else " Turn on the history to start recording."
            self._empty.set_text("No dictations recorded for this period." + hint)
        self._empty.set_visible(not entries)

    @staticmethod
    def _recent_row(entry: dict[str, Any]) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        row.get_style_context().add_class("ld-recent")
        moment = datetime.fromisoformat(str(entry["time"])).astimezone()
        wait = float(entry.get("transcript_s") or 0) + float(entry.get("cleanup_s") or 0)
        cost = entry.get("cost_usd")
        meta = Gtk.Box()
        meta.pack_start(
            _label(
                f"{moment:%b %d %H:%M} · {format_duration(float(entry.get('audio_s') or 0))}"
                f" · {entry.get('style')}",
                dim=True,
            ),
            True,
            True,
            0,
        )
        cost_text = "" if cost is None else f" · ${float(cost):.3f}"
        meta.pack_end(_label(f"{wait:.1f} s{cost_text}", dim=True), False, False, 0)
        row.pack_start(meta, False, False, 0)
        raw, text = str(entry.get("raw", "")), str(entry.get("text", ""))
        if entry.get("cleanup") == "done":
            markup = removal_markup(raw.split(), removed_word_indices(raw, text), 0, STRIKE_S)
        else:
            markup = GLib.markup_escape_text(text)
        row.pack_start(_label(markup=markup), False, False, 0)
        return row


class RecoveryPage(Gtk.Box):
    """Lists the last dictations kept by `recent`, whatever the history setting."""

    def __init__(self, path: Path) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=24)
        self._path = path
        top = Gtk.Box(spacing=12)
        top.pack_start(
            _label(
                f"The last {recent.KEEP} dictations stay on this computer, readable by you "
                "only, even with the history off. Copy one back if a paste went wrong.",
                dim=True,
            ),
            True,
            True,
            0,
        )
        self.clear = Gtk.Button(label="Clear", valign=Gtk.Align.START)
        self.clear.connect("clicked", self._on_clear)
        top.pack_end(self.clear, False, False, 0)
        self.pack_start(top, False, False, 0)

        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_min_content_height(RECOVERY_HEIGHT)
        scroller.add(self.rows)
        self.pack_start(scroller, True, True, 0)
        self.refresh()

    def _on_clear(self, _button: Gtk.Button) -> None:
        recent.clear(self._path)
        self.refresh()

    def refresh(self) -> None:
        for child in self.rows.get_children():
            self.rows.remove(child)
        entries = recent.load(self._path)
        for entry in entries:
            self.rows.pack_start(self._row(entry), False, False, 0)
        if not entries:
            self.rows.pack_start(_label("No dictation saved yet.", dim=True), False, False, 0)
        self.clear.set_sensitive(bool(entries))
        self.rows.show_all()

    @staticmethod
    def _row(entry: dict[str, Any]) -> Gtk.Widget:
        text = str(entry["text"])
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        row.get_style_context().add_class("ld-recent")
        meta = Gtk.Box(spacing=8)
        try:
            moment = f"{datetime.fromisoformat(str(entry['time'])).astimezone():%b %d %H:%M}"
        except ValueError:
            moment = ""
        outcome = OUTCOMES.get(str(entry.get("outcome")), str(entry.get("outcome")))
        words = len(text.split())
        meta.pack_start(_label(f"{moment} · {outcome} · {words} words", dim=True), True, True, 0)
        copy = Gtk.Button(label="Copy")

        def on_copy(button: Gtk.Button) -> None:
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text(text, -1)
            # Hands the text to a clipboard manager, if any, before this window closes.
            clipboard.store()
            button.set_label("Copied")

        copy.connect("clicked", on_copy)
        meta.pack_end(copy, False, False, 0)
        row.pack_start(meta, False, False, 0)
        body = _label(text)
        body.set_selectable(True)
        body.set_lines(4)
        body.set_ellipsize(Pango.EllipsizeMode.END)
        row.pack_start(body, False, False, 0)
        return row


class ShortcutButton(Gtk.Button):
    """Shows a shortcut as keycaps; a click captures the next keys pressed."""

    def __init__(self, spec: str, on_change: Callable[[], None]) -> None:
        super().__init__(hexpand=True)
        self.spec = spec
        self._on_change = on_change
        self._capturing = False
        self._pressed: list[str] = []
        self._keys = Gtk.Box(spacing=4)
        self.add(self._keys)
        self.connect("clicked", self._on_clicked)
        self.connect("key-press-event", self._on_key_press)
        self.connect("key-release-event", self._on_key_release)
        self.connect("focus-out-event", lambda _button, _event: self._stop())
        self._show()

    def set_spec(self, spec: str) -> None:
        self.spec = spec
        self._show()
        self._on_change()

    def _show(self, keys: list[str] | None = None) -> None:
        for child in self._keys.get_children():
            child.destroy()
        # Keys being captured show as the shortcut they will become, e.g. Ctrl, not Left Ctrl.
        spec = self.spec if keys is None else shortcuts.combine(keys) if keys else ""
        try:
            labels = shortcuts.labels(spec)
        except ValueError:
            labels = []
        if self._capturing and not labels:
            self._keys.pack_start(_label("Press the keys…", dim=True), False, False, 0)
        elif not labels:
            self._keys.pack_start(_label("None", dim=True), False, False, 0)
        for index, text in enumerate(labels):
            if index:
                self._keys.pack_start(_label("+", dim=True), False, False, 0)
            key = Gtk.Label(label=text)
            key.get_style_context().add_class("ld-key")
            self._keys.pack_start(key, False, False, 0)
        self._keys.show_all()

    def _on_clicked(self, _button: Gtk.Button) -> None:
        if self._capturing:
            self._stop()
            return
        self._capturing = True
        self._pressed = []
        self.get_style_context().add_class("ld-capturing")
        window = self.get_window()
        if window:
            # An active grab keeps desktop shortcuts, such as Ctrl+Space, from firing meanwhile.
            seat = window.get_display().get_default_seat()
            seat.grab(window, Gdk.SeatCapabilities.KEYBOARD, False, None, None, None)
        self._show([])

    def _stop(self) -> bool:
        if self._capturing:
            self._capturing = False
            self.get_style_context().remove_class("ld-capturing")
            Gdk.Display.get_default().get_default_seat().ungrab()
            self._show()
        return False

    def _on_key_press(self, _button: Gtk.Button, event: Gdk.EventKey) -> bool:
        if not self._capturing:
            return False
        codepoint = Gdk.keyval_to_unicode(event.keyval)
        key = shortcuts.key_from_keysym(
            Gdk.keyval_name(event.keyval) or "", chr(codepoint) if codepoint else ""
        )
        if key and key not in self._pressed:
            self._pressed.append(key)
            self._show(self._pressed)
        # Enter must not activate the Save button while it is being captured.
        return True

    def _on_key_release(self, _button: Gtk.Button, _event: Gdk.EventKey) -> bool:
        if not self._capturing:
            return False
        if self._pressed:
            try:
                spec = shortcuts.combine(self._pressed)
                shortcuts.parse(spec)
            except ValueError:
                self._pressed = []
                self._show([])
                return True
            self._stop()
            self.set_spec(spec)
        return True


def _icon_button(icon: str, tooltip: str, action: Callable[[], None]) -> Gtk.Button:
    button = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
    button.set_relief(Gtk.ReliefStyle.NONE)
    button.set_tooltip_text(tooltip)
    button.set_valign(Gtk.Align.CENTER)
    button.connect("clicked", lambda _button: action())
    return button


SHORTCUT_ROWS = [
    ("hotkey", "Dictate"),
    ("lock_key", "Lock while holding"),
    ("finish_key", "Finish a locked dictation"),
    ("cancel_key", "Cancel"),
]


class ShortcutsPage(Gtk.Grid):
    def __init__(self, config: Config, on_change: Callable[[], None]) -> None:
        super().__init__(row_spacing=8, column_spacing=16, margin=24)
        self._saved_hotkey = config.hotkey
        self._on_change = on_change
        self.problem: str | None = None
        self.mode = _combo(MODES, config.mode)
        self.mode.connect("changed", lambda _combo: self._refresh())
        _add_row(self, 0, "Mode", self.mode)
        self.attach(Gtk.Separator(margin_top=4, margin_bottom=4), 0, 1, 2, 1)
        defaults = Config()
        self.buttons: dict[str, ShortcutButton] = {}
        self._warnings: dict[str, Gtk.Label] = {}
        self._rows: dict[str, Gtk.Box] = {}
        for index, (name, title) in enumerate(SHORTCUT_ROWS):
            button = ShortcutButton(getattr(config, name), self._refresh)
            row = Gtk.Box(spacing=4)
            row.pack_start(button, True, True, 0)
            if name == "lock_key":
                row.pack_start(
                    _icon_button(
                        "edit-clear-symbolic", "No shortcut", partial(button.set_spec, "")
                    ),
                    False,
                    False,
                    0,
                )
            default = getattr(defaults, name)
            reset = _icon_button(
                "edit-undo-symbolic",
                f"Reset to {shortcuts.describe(default)}",
                partial(button.set_spec, default),
            )
            row.pack_start(reset, False, False, 0)
            _add_row(self, 2 + 2 * index, title, row)
            warning = _label()
            warning.get_style_context().add_class("ld-warning")
            warning.set_no_show_all(True)
            self.attach(warning, 1, 3 + 2 * index, 1, 1)
            self.buttons[name] = button
            self._warnings[name] = warning
            self._rows[name] = row
        note = _label(
            "Click a shortcut, then press a key or a combination. A modifier alone works too, "
            "such as Right Ctrl. Double-tapping the dictate shortcut also locks.",
            dim=True,
        )
        note.set_margin_top(4)
        self.attach(note, 1, 2 + 2 * len(SHORTCUT_ROWS), 1, 1)
        self._check()

    @property
    def values(self) -> dict[str, str]:
        return {"mode": self.mode.get_active_id(), **{n: b.spec for n, b in self.buttons.items()}}

    def _warning_for(self, name: str, specs: dict[str, str]) -> str | None:
        spec = specs[name]
        for other, title in SHORTCUT_ROWS:
            if other == name:
                break
            if shortcuts.overlaps(spec, specs[other]):
                return f"Overlaps with the {title.lower()} shortcut."
        if not spec or (name == "hotkey" and spec == self._saved_hotkey):
            return None
        if name == "lock_key":
            hotkey = specs["hotkey"]
            held = [k for k in shortcuts.parse(hotkey) if k in shortcuts.MODIFIERS]
            grabs = shortcuts.grabs(spec, shortcuts.modifier_mask(hotkey))
            if grabs and not is_free(grabs):
                if not held:
                    return "Another app already uses this shortcut."
                combined = shortcuts.describe(shortcuts.combine([*held, *shortcuts.parse(spec)]))
                return (
                    f"With {shortcuts.describe(hotkey)} held, this is {combined}. "
                    "Another app already uses it, so it may open that app."
                )
            return None
        grabs = shortcuts.grabs(spec)
        if grabs and not is_free(grabs):
            return "Another app already uses this shortcut."
        return None

    def _refresh(self) -> None:
        self._check()
        self._on_change()

    def _check(self) -> None:
        specs = {name: button.spec for name, button in self.buttons.items()}
        self._rows["lock_key"].set_sensitive(self.mode.get_active_id() == "hold")
        self.problem = None
        for name, _title in SHORTCUT_ROWS:
            message = self._warning_for(name, specs)
            warning = self._warnings[name]
            warning.set_text(message or "")
            warning.set_visible(bool(message))
            if message and message.startswith("Overlaps") and self.problem is None:
                self.problem = "Two shortcuts overlap: change one of them to save."


class DictionaryPage(Gtk.Box):
    def __init__(self, words: list[str]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=24)
        self.words = list(words)
        self.pack_start(
            _label(
                "Words you use often, spelled exactly as they must appear. The transcription "
                "and the cleanup both receive this list.",
                dim=True,
            ),
            False,
            False,
            0,
        )
        add_row = Gtk.Box(spacing=8)
        self.entry = Gtk.Entry(hexpand=True, placeholder_text="Add a word, such as Coolify")
        self.entry.connect("activate", lambda _entry: self.add_word())
        add_row.pack_start(self.entry, True, True, 0)
        add = Gtk.Button(label="Add")
        add.connect("clicked", lambda _button: self.add_word())
        add_row.pack_start(add, False, False, 0)
        self.pack_start(add_row, False, False, 0)
        self._message = _label()
        self._message.get_style_context().add_class("ld-warning")
        self._message.set_no_show_all(True)
        self.pack_start(self._message, False, False, 0)
        self._list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._list.get_style_context().add_class("frame")
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_min_content_height(RECOVERY_HEIGHT - 120)
        scroller.add(self._list)
        self.pack_start(scroller, True, True, 0)
        self._count = _label(dim=True)
        self.pack_start(self._count, False, False, 0)
        self._render()

    def _say(self, message: str) -> None:
        self._message.set_text(message)
        self._message.set_visible(bool(message))

    def add_word(self) -> None:
        word = self.entry.get_text().strip()
        if not word:
            return
        if any(char in word for char in KEYWORD_FORBIDDEN):
            self._say("A word cannot contain < or >.")
            return
        if word.lower() in {known.lower() for known in self.words}:
            self._say(f"{word} is already in the dictionary.")
            return
        self.words.append(word)
        self.entry.set_text("")
        self._say("")
        self._render()

    def remove_word(self, word: str) -> None:
        self.words.remove(word)
        self._render()

    def _render(self) -> None:
        for row in self._list.get_children():
            row.destroy()
        for word in sorted(self.words, key=str.lower):
            line = Gtk.Box(spacing=8, margin_start=12, margin_end=6, margin_top=2, margin_bottom=2)
            line.pack_start(_label(word), True, True, 0)
            line.pack_end(
                _icon_button("window-close-symbolic", "Remove", partial(self.remove_word, word)),
                False,
                False,
                0,
            )
            self._list.add(line)
        self._list.show_all()
        count = len(self.words)
        self._count.set_text("No words yet." if not count else f"{count} word{'s' * (count > 1)}")


class SetupWindow(Gtk.Window):
    def __init__(
        self,
        config: Config,
        config_path: Path,
        key_present: bool,
        on_done: Callable[[bool], None],
        history_path: Path | None = None,
        recent_path: Path | None = None,
    ) -> None:
        super().__init__(title="live-dictate")
        _install_css()
        self._config = config
        self._config_path = config_path
        self._key_present = key_present
        self._on_done = on_done
        self._saved = False
        self.set_default_size(WINDOW_WIDTH, -1)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.connect("destroy", lambda _window: self._on_done(self._saved))

        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._stack.add_titled(self._general_page(), "general", "General")
        self.shortcuts = ShortcutsPage(config, self._update_save)
        self._stack.add_titled(self.shortcuts, "shortcuts", "Shortcuts")
        self.dictionary = DictionaryPage(config.keywords)
        self._stack.add_titled(self.dictionary, "dictionary", "Dictionary")
        self._stack.add_titled(self._cleanup_page(), "cleanup", "Cleanup")
        self._stack.add_titled(self._appearance_page(), "appearance", "Appearance")
        self.history = HistoryPage(config, history_path or default_history_path())
        self._stack.add_titled(self.history, "history", "History")
        self.recovery = RecoveryPage(recent_path or recent.default_recent_path())
        self._stack.add_titled(self.recovery, "recent", "Recent")

        header = Gtk.HeaderBar(custom_title=Gtk.StackSwitcher(stack=self._stack))
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _button: self.destroy())
        self._save = Gtk.Button(label="Save")
        self._save.get_style_context().add_class("suggested-action")
        self._save.connect("clicked", self._on_save)
        header.pack_start(cancel)
        header.pack_end(self._save)
        self.set_titlebar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.pack_start(self._stack, True, True, 0)
        self._status = _label()
        self._status.set_margin_start(24)
        self._status.set_margin_end(24)
        self._status.set_margin_bottom(16)
        content.pack_start(self._status, False, False, 0)
        self.add(content)

        self._save.set_can_default(True)
        self.set_default(self._save)

    def _general_page(self) -> Gtk.Widget:
        grid = _grid()
        grid.attach(
            _label("Hold the hotkey, speak, and release: the text is pasted where your cursor is."),
            0,
            0,
            2,
            1,
        )
        self._key = Gtk.Entry(visibility=False, hexpand=True, activates_default=True)
        self._key.set_placeholder_text("sk-...")
        if self._key_present:
            key_area = Gtk.Stack()
            stored = Gtk.Box(spacing=8)
            stored.pack_start(_label("●●●●●●●● stored in your keyring", dim=True), True, True, 0)
            replace = Gtk.Button(label="Replace…")
            replace.connect("clicked", lambda _button: self._replace_key(key_area))
            stored.pack_end(replace, False, False, 0)
            key_area.add_named(stored, "stored")
            key_area.add_named(self._key, "entry")
            _add_row(grid, 1, "OpenAI API key", key_area)
        else:
            _add_row(grid, 1, "OpenAI API key", self._key)

        language = self._config.languages[0] if self._config.languages else ""
        self._language = _combo(LANGUAGES, language)
        _add_row(grid, 2, "Language", self._language)
        note = _label(
            markup=f'Get a key on <a href="{API_KEYS_URL}">platform.openai.com</a>. '
            "live-dictate checks it before saving.",
            dim=True,
        )
        grid.attach(note, 1, 3, 1, 1)
        return grid

    def _replace_key(self, key_area: Gtk.Stack) -> None:
        key_area.set_visible_child_name("entry")
        self._key.grab_focus()

    def _cleanup_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=24)
        self._styles: dict[str, Gtk.RadioButton] = {}
        group: Gtk.RadioButton | None = None
        for style, title, description, example in CLEANUP_OPTIONS:
            button = Gtk.RadioButton.new_from_widget(group)
            button.get_style_context().add_class("ld-option")
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_start=6)
            texts.pack_start(_label(markup=f"<b>{title}</b>"), False, False, 0)
            texts.pack_start(_label(description, dim=True), False, False, 0)
            if example:
                texts.pack_start(
                    _label(markup=_example_markup(*example), dim=True), False, False, 0
                )
            button.add(texts)
            button.set_active(style == self._config.effective_cleanup_style)
            box.pack_start(button, False, False, 0)
            self._styles[style] = button
            group = group or button

        threshold = Gtk.Box(spacing=8, margin_top=8)
        threshold.pack_start(Gtk.Label(label="Skip cleanup under"), False, False, 0)
        self._min_words = Gtk.SpinButton.new_with_range(1, 50, 1)
        self._min_words.set_value(self._config.cleanup_min_words)
        threshold.pack_start(self._min_words, False, False, 0)
        threshold.pack_start(_label("words", dim=True), False, False, 0)
        box.pack_start(threshold, False, False, 0)
        return box

    def _appearance_page(self) -> Gtk.Widget:
        grid = _grid()
        self._preview = AuraPreview(self._config.accent_color)
        self._accent = AccentPicker(self._config.accent_color, self._preview.set_accent)
        _add_row(grid, 0, "Color", self._accent)
        grid.attach(
            _label(
                "Auto follows your desktop accent on GNOME 47 and later, and stays neutral "
                "otherwise.",
                dim=True,
            ),
            1,
            1,
            1,
            1,
        )
        lines = Gtk.Box(spacing=8)
        self._max_lines = Gtk.SpinButton.new_with_range(1, MAX_LINES_LIMIT, 1)
        self._max_lines.set_value(self._config.max_lines)
        lines.pack_start(self._max_lines, False, False, 0)
        lines.pack_start(_label("lines, older text scrolls away", dim=True), False, False, 0)
        _add_row(grid, 2, "Live text", lines)
        grid.attach(self._preview, 0, 3, 2, 1)
        return grid

    @property
    def cleanup_style(self) -> str:
        return next(style for style, button in self._styles.items() if button.get_active())

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._save.set_sensitive(not busy and self.shortcuts.problem is None)
        self._status.set_text(message)

    def _update_save(self) -> None:
        self._set_busy(False, self.shortcuts.problem or "")

    def _on_save(self, _button: Gtk.Button) -> None:
        key = self._key.get_text().strip()
        if not key and not self._key_present:
            self._stack.set_visible_child_name("general")
            self._set_busy(False, "Enter your OpenAI API key.")
            return
        if not key:
            self._finish(None)
            return
        self._set_busy(True, "Checking the key…")
        model = self._config.transcription_model
        threading.Thread(target=self._validate, args=(key, model), daemon=True).start()

    def _validate(self, key: str, model: str) -> None:
        with httpx.Client(timeout=15) as client:
            problem = check_api_key(client, key, model)
        GLib.idle_add(self._after_validation, key, problem)

    def _after_validation(self, key: str, problem: str | None) -> bool:
        if problem:
            self._set_busy(False, problem)
        else:
            self._finish(key)
        return False

    def _finish(self, key: str | None) -> None:
        language = self._language.get_active_id()
        try:
            if key:
                store_api_key(key)
            update_config_file(
                self._config_path,
                {
                    "languages": [language] if language else [],
                    **self.shortcuts.values,
                    "keywords": self.dictionary.words,
                    "accent_color": self._accent.value,
                    "max_lines": self._max_lines.get_value_as_int(),
                    "cleanup_enabled": True,
                    "cleanup_style": self.cleanup_style,
                    "cleanup_min_words": self._min_words.get_value_as_int(),
                    "history_enabled": self.history.enabled.get_active(),
                    "history_retention_days": self.history.retention.get_value_as_int(),
                },
            )
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            self._set_busy(False, f"Could not save: {error}")
            return
        self._saved = True
        self.destroy()


_open_window: SetupWindow | None = None


def open_settings(config_path: Path | None, on_done: Callable[[bool], None]) -> None:
    """Shows the settings window without blocking; a second call raises the open window."""
    global _open_window
    if _open_window is not None:
        _open_window.present()
        return
    path = config_path or default_config_path()

    def done(saved: bool) -> None:
        global _open_window
        _open_window = None
        on_done(saved)

    _open_window = SetupWindow(load_config(path), path, has_api_key(), done)
    _open_window.show_all()
    _open_window.present()


def run_setup(config_path: Path | None = None) -> bool:
    """Shows the settings window and returns True once the user saved a working configuration."""
    result: list[bool] = []

    def done(saved: bool) -> None:
        result.append(saved)
        Gtk.main_quit()

    open_settings(config_path, done)
    Gtk.main()
    return bool(result and result[0])
