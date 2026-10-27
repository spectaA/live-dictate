from __future__ import annotations

import json
import locale
import os
import subprocess
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from .accent import HEX_COLOR

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

KEYRING_ATTRIBUTES = ("service", "live-dictate", "account", "openai")
CLEANUP_STYLES = ("off", "light", "standard", "polished")
MAX_LINES_LIMIT = 20

DEFAULT_TERMINAL_CLASSES = (
    "gnome-terminal-server",
    "kitty",
    "alacritty",
    "tilix",
    "xterm",
    "konsole",
    "org.wezfurlong.wezterm",
    "terminator",
    "ghostty",
    "com.mitchellh.ghostty",
)


def system_language() -> str | None:
    """Returns the ISO 639-1 code of the user's locale, e.g. "fr" for fr_BE.UTF-8."""
    for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(variable)
        if value:
            break
    else:
        value = locale.getlocale()[0] or ""
    code = value.split(".")[0].split("_")[0].lower()
    return code if len(code) == 2 and code.isalpha() else None


def _default_languages() -> list[str]:
    language = system_language()
    return [language] if language else []


@dataclass
class Config:
    hotkey: str = "<ctrl_r>"
    mode: str = "hold"
    languages: list[str] = field(default_factory=_default_languages)
    transcription_model: str = "gpt-live-transcribe"
    transcription_delay: str = "low"
    transcription_prompt: str = ""
    keywords: list[str] = field(default_factory=list)
    # Kept for configs written before cleanup_style existed; False behaves like style "off".
    cleanup_enabled: bool = True
    cleanup_style: str = "standard"
    cleanup_model: str = "gpt-6-luna"
    cleanup_service_tier: str = "fast"
    cleanup_timeout_s: float = 3.0
    cleanup_timeout_per_word_s: float = 0.01
    cleanup_min_words: int = 5
    min_audio_ms: int = 300
    paste_restore_delay_s: float = 0.5
    terminal_classes: list[str] = field(default_factory=lambda: list(DEFAULT_TERMINAL_CLASSES))
    accent_color: str = ""
    max_lines: int = 4
    history_enabled: bool = False
    history_retention_days: int = 30

    @property
    def effective_cleanup_style(self) -> str:
        return self.cleanup_style if self.cleanup_enabled else "off"

    def __post_init__(self) -> None:
        if self.mode not in ("hold", "toggle"):
            raise ValueError(f"mode must be 'hold' or 'toggle', got {self.mode!r}")
        if self.cleanup_style not in CLEANUP_STYLES:
            styles = ", ".join(CLEANUP_STYLES)
            raise ValueError(f"cleanup_style must be one of {styles}, got {self.cleanup_style!r}")
        if self.history_retention_days < 1:
            raise ValueError("history_retention_days must be at least 1")
        if not 1 <= self.max_lines <= MAX_LINES_LIMIT:
            raise ValueError(f"max_lines must be between 1 and {MAX_LINES_LIMIT}")
        if self.accent_color and not HEX_COLOR.match(self.accent_color):
            raise ValueError(f"accent_color must be empty or #rrggbb, got {self.accent_color!r}")


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "live-dictate" / "config.toml"


def default_history_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "live-dictate" / "history.jsonl"


def read_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    known = {f.name for f in fields(Config)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"Unknown config keys in {path}: {', '.join(sorted(unknown))}")
    return raw


def load_config(path: Path | None) -> Config:
    return Config(**read_config_file(path or default_config_path()))


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        # A JSON string is a valid TOML basic string.
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"unsupported config value: {value!r}")


def dump_config(values: dict[str, Any]) -> str:
    return "".join(f"{key} = {_toml_value(value)}\n" for key, value in values.items())


def update_config_file(path: Path, updates: dict[str, Any]) -> None:
    """Merges `updates` into the file, keeping other keys; comments are not preserved."""
    values = {**read_config_file(path), **updates}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_config(values), encoding="utf-8")


def _keyring_lookup() -> str:
    try:
        result = subprocess.run(
            ["secret-tool", "lookup", *KEYRING_ATTRIBUTES],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except FileNotFoundError:
        return ""
    return result.stdout.strip()


def load_api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip() or _keyring_lookup()
    if not key:
        raise RuntimeError("No OpenAI API key: run `live-dictate setup` or set OPENAI_API_KEY")
    return key


def has_api_key() -> bool:
    try:
        load_api_key()
    except RuntimeError:
        return False
    return True


def store_api_key(key: str) -> None:
    subprocess.run(
        ["secret-tool", "store", "--label=live-dictate OpenAI API key", *KEYRING_ATTRIBUTES],
        input=key,
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
