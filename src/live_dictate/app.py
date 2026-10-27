from __future__ import annotations

import asyncio
import enum
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from gi.repository import GLib

from . import history
from .accent import resolve_accent
from .audio import Recorder, rms
from .cleanup import Cleaner, CleanupResult, needs_cleanup
from .config import Config, default_history_path
from .hotkey import HotkeyListener
from .injector import Injector
from .overlay import Overlay, level_from_rms
from .transcriber import LiveTranscriber

log = logging.getLogger(__name__)


class State(enum.Enum):
    IDLE = "idle"
    RECORDING = "recording"
    FINALIZING = "finalizing"


class OverlayBridge:
    """Forwards overlay calls from any thread to the GTK main loop."""

    def __init__(self, overlay: Overlay) -> None:
        self._overlay = overlay

    @staticmethod
    def _call(action: Callable[[], None]) -> None:
        def run() -> bool:
            action()
            return False

        GLib.idle_add(run)

    def listening(self) -> None:
        self._call(self._overlay.listening)

    def partial(self, text: str) -> None:
        self._call(lambda: self._overlay.partial(text))

    def level(self, value: float) -> None:
        self._call(lambda: self._overlay.level(value))

    def cleaning(self) -> None:
        self._call(self._overlay.cleaning)

    def finished(self, raw: str, final: str) -> None:
        self._call(lambda: self._overlay.finished(raw, final))

    def hide(self) -> None:
        self._call(self._overlay.hide)

    def error(self, message: str) -> None:
        self._call(lambda: self._overlay.error(message))


class App:
    """Dictation state machine; every method below runs on the asyncio loop thread."""

    def __init__(
        self,
        config: Config,
        api_key: str,
        ui: OverlayBridge,
        cleanup: bool,
        output: Callable[[str], None] | None,
    ) -> None:
        self._config = config
        self._ui = ui
        self._transcriber = LiveTranscriber(api_key, config, ui.partial)
        self._cleaner = Cleaner(api_key, config) if cleanup else None
        injector = Injector(config.terminal_classes, config.paste_restore_delay_s)
        self._output = output or injector.paste
        self._state = State.IDLE
        self._recorder: Recorder | None = None
        self._audio: asyncio.Queue[bytes | None] | None = None
        self._task: asyncio.Task[None] | None = None
        self._finishing: asyncio.Task[None] | None = None
        self._released_at = 0.0
        self._history = default_history_path() if config.history_enabled else None
        self.paused = False

    def on_hotkey_down(self) -> None:
        if self.paused:
            return
        if self._state is State.IDLE:
            self._start()
        elif self._state is State.RECORDING and self._config.mode == "toggle":
            self._finishing = asyncio.create_task(self._finish_recording())

    def on_hotkey_up(self) -> None:
        if self._state is State.RECORDING and self._config.mode == "hold":
            self._finishing = asyncio.create_task(self._finish_recording())

    def on_escape(self) -> None:
        if self._task and self._state is not State.IDLE:
            log.info("dictation cancelled")
            self._task.cancel()

    def pause(self) -> None:
        """Cancels any dictation and ignores the hotkey, e.g. while the settings window is open."""
        self.paused = True
        self.on_escape()

    def resume(self) -> None:
        self.paused = False

    def _start(self) -> None:
        loop = asyncio.get_running_loop()
        audio: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._audio = audio
        ui = self._ui

        def on_chunk(chunk: bytes) -> None:
            loop.call_soon_threadsafe(audio.put_nowait, chunk)
            ui.level(level_from_rms(rms(chunk)))

        self._recorder = Recorder(on_chunk)
        self._recorder.start()
        self._state = State.RECORDING
        self._ui.listening()
        self._task = asyncio.create_task(self._session(audio))

    async def _stop_recorder(self) -> None:
        recorder, self._recorder = self._recorder, None
        if recorder:
            await asyncio.get_running_loop().run_in_executor(None, recorder.stop)

    async def _finish_recording(self) -> None:
        self._state = State.FINALIZING
        self._released_at = time.monotonic()
        audio = self._audio
        await self._stop_recorder()
        # Chunks scheduled by the recorder thread are queued before this point, so the sentinel
        # is guaranteed to come last.
        if audio:
            audio.put_nowait(None)

    async def _session(self, audio: asyncio.Queue[bytes | None]) -> None:
        transcript = text = ""
        cleanup = "off" if self._cleaner is None else "skipped"
        result: CleanupResult | None = None
        transcript_s: float | None = None
        cleanup_s: float | None = None
        outcome, error_message = "pasted", None
        try:
            transcript = await self._transcriber.run(audio)
            transcribed_at = time.monotonic()
            transcript_s = transcribed_at - self._released_at
            if not transcript:
                self._ui.hide()
                outcome = "empty"
                return
            text = transcript
            if self._cleaner and needs_cleanup(transcript, self._config.cleanup_min_words):
                self._ui.cleaning()
                result = await self._cleaner.clean(transcript)
                cleanup_s = time.monotonic() - transcribed_at
                cleanup = "failed" if result.failed else "done"
                text = result.text
            self._ui.finished(transcript, text)
            log.info(
                "latency after release: transcript %.2fs, cleanup %.2fs",
                transcript_s,
                cleanup_s or 0.0,
            )
            log.debug("raw: %s", transcript)
            log.debug("clean: %s", text)
            await asyncio.get_running_loop().run_in_executor(None, self._output, text)
        except asyncio.CancelledError:
            self._ui.hide()
            outcome = "cancelled"
        except Exception as error:
            log.exception("dictation failed")
            self._ui.error(str(error) or type(error).__name__)
            outcome, error_message = "error", str(error) or type(error).__name__
        finally:
            await self._stop_recorder()
            self._audio = None
            self._task = None
            self._state = State.IDLE
            if self._history and outcome != "empty":
                self._record(
                    self._history,
                    outcome,
                    transcript,
                    text,
                    cleanup,
                    transcript_s,
                    cleanup_s,
                    result,
                    error_message,
                )

    def _record(
        self,
        path: Path,
        outcome: str,
        raw: str,
        text: str,
        cleanup: str,
        transcript_s: float | None,
        cleanup_s: float | None,
        result: CleanupResult | None,
        error: str | None,
    ) -> None:
        entry = history.make_entry(
            config=self._config,
            outcome=outcome,
            raw=raw,
            text=text if outcome == "pasted" else "",
            cleanup=cleanup,
            audio_ms=self._transcriber.audio_ms,
            billed_seconds=self._transcriber.billed_seconds,
            transcript_s=transcript_s,
            cleanup_s=cleanup_s,
            result=result,
            error=error,
        )
        try:
            history.append(path, entry)
        except OSError:
            log.exception("could not write the history")


def _restart_process() -> None:
    """Re-executes the daemon in place so new settings apply; systemd keeps tracking the PID."""
    flags = ["-s"] if sys.flags.no_user_site else []
    os.execv(sys.executable, [sys.executable, *flags, "-m", "live_dictate", *sys.argv[1:]])  # noqa: S606


def run_app(
    config: Config,
    api_key: str,
    cleanup: bool,
    output: Callable[[str], None] | None,
    config_path: Path | None = None,
) -> None:
    from gi.repository import Gtk

    from .setup_window import open_settings

    if config.history_enabled:
        removed = history.prune(default_history_path(), config.history_retention_days)
        if removed:
            log.info("removed %d history entries past the retention period", removed)

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, name="asyncio", daemon=True).start()

    def after_settings(saved: bool) -> None:
        if saved:
            _restart_process()
        loop.call_soon_threadsafe(app.resume)

    def show_settings() -> None:
        loop.call_soon_threadsafe(app.pause)
        open_settings(config_path, after_settings)

    ui = OverlayBridge(Overlay(resolve_accent(config.accent_color), on_settings=show_settings))
    app = App(config, api_key, ui, cleanup, output)

    def dispatch(handler: Callable[[], None]) -> Callable[[], None]:
        def call() -> None:
            loop.call_soon_threadsafe(handler)

        return call

    hotkeys = HotkeyListener(
        config.hotkey,
        on_down=dispatch(app.on_hotkey_down),
        on_up=dispatch(app.on_hotkey_up),
        on_escape=dispatch(app.on_escape),
    )
    hotkeys.start()

    for signal_number in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal_number, Gtk.main_quit)

    log.info("ready: %s hotkey %s", config.mode, config.hotkey)
    try:
        Gtk.main()
    finally:
        hotkeys.stop()
        loop.call_soon_threadsafe(loop.stop)
