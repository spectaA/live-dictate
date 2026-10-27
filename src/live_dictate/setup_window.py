from __future__ import annotations

import math
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

import cairo
import gi
import httpx

gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, PangoCairo  # noqa: E402

from . import aura  # noqa: E402
from .accent import resolve_accent  # noqa: E402
from .config import (  # noqa: E402
    Config,
    default_config_path,
    has_api_key,
    load_config,
    store_api_key,
    update_config_file,
)

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


class SetupWindow(Gtk.Window):
    def __init__(
        self,
        config: Config,
        config_path: Path,
        key_present: bool,
        on_done: Callable[[bool], None],
    ) -> None:
        super().__init__(title="live-dictate setup")
        self._config = config
        self._config_path = config_path
        self._key_present = key_present
        self._on_done = on_done
        self._saved = False
        self.set_resizable(False)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.connect("destroy", lambda _window: self._on_done(self._saved))

        header = Gtk.HeaderBar(title="live-dictate", subtitle="Setup")
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _button: self.destroy())
        self._save = Gtk.Button(label="Save")
        self._save.get_style_context().add_class("suggested-action")
        self._save.connect("clicked", self._on_save)
        header.pack_start(cancel)
        header.pack_end(self._save)
        self.set_titlebar(header)

        grid = Gtk.Grid(row_spacing=12, column_spacing=12, margin=24)
        grid.set_size_request(MIN_WIDTH, -1)
        intro = Gtk.Label(xalign=0, wrap=True, max_width_chars=50)
        intro.set_markup(
            "Hold the hotkey, speak, and release: the text is pasted where your cursor is.\n"
            f'live-dictate needs an <a href="{API_KEYS_URL}">OpenAI API key</a>.'
        )
        grid.attach(intro, 0, 0, 2, 1)

        self._key = Gtk.Entry(visibility=False, hexpand=True, activates_default=True)
        self._key.set_placeholder_text(
            "Leave empty to keep the current key" if key_present else "sk-..."
        )
        self._add_row(grid, 1, "OpenAI API key", self._key)

        language = config.languages[0] if config.languages else ""
        self._language = _combo(LANGUAGES, language)
        self._add_row(grid, 2, "Language", self._language)

        self._hotkey = _combo(HOTKEYS, config.hotkey)
        self._add_row(grid, 3, "Hotkey", self._hotkey)

        self._preview = AuraPreview(config.accent_color)
        self._accent = AccentPicker(config.accent_color, self._preview.set_accent)
        self._add_row(grid, 4, "Color", self._accent)
        grid.attach(self._preview, 0, 5, 2, 1)

        self._status = Gtk.Label(xalign=0, wrap=True, max_width_chars=50)
        grid.attach(self._status, 0, 6, 2, 1)
        self.add(grid)

        self._save.set_can_default(True)
        self.set_default(self._save)

    @staticmethod
    def _add_row(grid: Gtk.Grid, row: int, label: str, widget: Gtk.Widget) -> None:
        grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
        grid.attach(widget, 1, row, 1, 1)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._save.set_sensitive(not busy)
        self._status.set_text(message)

    def _on_save(self, _button: Gtk.Button) -> None:
        key = self._key.get_text().strip()
        if not key and not self._key_present:
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
        try:
            if key:
                store_api_key(key)
            language = self._language.get_active_id()
            update_config_file(
                self._config_path,
                {
                    "languages": [language] if language else [],
                    "hotkey": self._hotkey.get_active_id(),
                    "accent_color": self._accent.value,
                },
            )
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            self._set_busy(False, f"Could not save: {error}")
            return
        self._saved = True
        self.destroy()


def run_setup(config_path: Path | None = None) -> bool:
    """Shows the setup window and returns True once the user saved a working configuration."""
    path = config_path or default_config_path()
    config = load_config(path)
    result: list[bool] = []

    def done(saved: bool) -> None:
        result.append(saved)
        Gtk.main_quit()

    window = SetupWindow(config, path, has_api_key(), done)
    window.show_all()
    window.present()
    Gtk.main()
    return bool(result and result[0])
