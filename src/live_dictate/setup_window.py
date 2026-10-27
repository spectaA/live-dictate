from __future__ import annotations

import math
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
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

from . import aura, history, recent  # noqa: E402
from .accent import resolve_accent  # noqa: E402
from .config import (  # noqa: E402
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

HOTKEYS = [
    ("<ctrl_r>", "Right Ctrl"),
    ("<pause>", "Pause"),
    ("<f9>", "F9"),
    ("<ctrl>+<alt>+<space>", "Ctrl + Alt + Space"),
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
        self._hotkey = _combo(HOTKEYS, self._config.hotkey)
        _add_row(grid, 3, "Hotkey", self._hotkey)
        self._mode = _combo(MODES, self._config.mode)
        _add_row(grid, 4, "Mode", self._mode)
        note = _label(
            markup=f'Get a key on <a href="{API_KEYS_URL}">platform.openai.com</a>. '
            "live-dictate checks it before saving.",
            dim=True,
        )
        grid.attach(note, 1, 5, 1, 1)
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
        self._save.set_sensitive(not busy)
        self._status.set_text(message)

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
                    "hotkey": self._hotkey.get_active_id(),
                    "mode": self._mode.get_active_id(),
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
