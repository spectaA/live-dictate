from __future__ import annotations

import logging
import re
import subprocess
import time

log = logging.getLogger(__name__)

WM_CLASS_PATTERN = re.compile(r'"([^"]*)"')


def parse_wm_class(xprop_output: str) -> list[str]:
    return [name.lower() for name in WM_CLASS_PATTERN.findall(xprop_output)]


def paste_keys(window_classes: list[str], terminal_classes: list[str]) -> str:
    terminals = {name.lower() for name in terminal_classes}
    if any(name in terminals for name in window_classes):
        return "ctrl+shift+v"
    return "ctrl+v"


def _output(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False, timeout=2)


def _active_window_classes() -> list[str]:
    window = _output(["xdotool", "getactivewindow"]).stdout.strip()
    if not window:
        return []
    output = _output(["xprop", "-id", window, "WM_CLASS"]).stdout
    return parse_wm_class(output)


def _read_clipboard() -> str | None:
    result = _output(["xclip", "-selection", "clipboard", "-o"])
    return result.stdout if result.returncode == 0 else None


def _write_clipboard(text: str) -> None:
    # xclip forks to keep owning the selection; a captured stdout would block until it exits.
    subprocess.run(
        ["xclip", "-selection", "clipboard", "-i"],
        input=text,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=2,
    )


class Injector:
    def __init__(self, terminal_classes: list[str], restore_delay_s: float) -> None:
        self._terminal_classes = terminal_classes
        self._restore_delay_s = restore_delay_s

    def paste(self, text: str) -> None:
        previous = _read_clipboard()
        keys = paste_keys(_active_window_classes(), self._terminal_classes)
        _write_clipboard(text)
        subprocess.run(["xdotool", "key", "--clearmodifiers", keys], check=False, timeout=2)
        # The target app reads the clipboard asynchronously after the key event.
        time.sleep(self._restore_delay_s)
        if previous is not None:
            _write_clipboard(previous)
        log.debug("pasted %d chars with %s", len(text), keys)
