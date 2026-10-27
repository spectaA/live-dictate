from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

import gi
import httpx

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

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

        self._status = Gtk.Label(xalign=0, wrap=True, max_width_chars=50)
        grid.attach(self._status, 0, 4, 2, 1)
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
