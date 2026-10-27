from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

KEYRING_ATTRIBUTES = ("service", "live-dictate", "account", "openai")

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


@dataclass
class Config:
    hotkey: str = "<ctrl_r>"
    mode: str = "hold"
    languages: list[str] = field(default_factory=list)
    transcription_model: str = "gpt-live-transcribe"
    transcription_delay: str = "low"
    transcription_prompt: str = ""
    keywords: list[str] = field(default_factory=list)
    cleanup_enabled: bool = True
    cleanup_model: str = "gpt-6-luna"
    cleanup_service_tier: str = "fast"
    cleanup_timeout_s: float = 3.0
    cleanup_min_words: int = 5
    min_audio_ms: int = 300
    paste_restore_delay_s: float = 0.5
    terminal_classes: list[str] = field(default_factory=lambda: list(DEFAULT_TERMINAL_CLASSES))

    def __post_init__(self) -> None:
        if self.mode not in ("hold", "toggle"):
            raise ValueError(f"mode must be 'hold' or 'toggle', got {self.mode!r}")


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "live-dictate" / "config.toml"


def load_config(path: Path | None) -> Config:
    path = path or default_config_path()
    if not path.exists():
        return Config()
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    known = {f.name for f in fields(Config)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"Unknown config keys in {path}: {', '.join(sorted(unknown))}")
    return Config(**raw)


def load_api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        return key
    try:
        result = subprocess.run(
            ["secret-tool", "lookup", *KEYRING_ATTRIBUTES],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except FileNotFoundError:
        result = None
    key = result.stdout.strip() if result else ""
    if not key:
        raise RuntimeError(
            "No OpenAI API key: set OPENAI_API_KEY or store it with "
            f"`secret-tool store --label='live-dictate' {' '.join(KEYRING_ATTRIBUTES)}`"
        )
    return key
