from __future__ import annotations

import asyncio
import enum
import logging
import signal
import threading
import time
from collections.abc import Callable

from gi.repository import GLib

from .audio import Recorder, rms
from .cleanup import Cleaner, needs_cleanup
from .config import Config
from .hotkey import HotkeyListener
from .injector import Injector
from .overlay import Overlay, level_from_rms
from .transcriber import LiveTranscriber

ERROR_DISPLAY_S = 4

log = logging.getLogger(__name__)


class State(enum.Enum):
    IDLE = "idle"
    RECORDING = "recording"
    FINALIZING = "finalizing"


class OverlayBridge:
    """Forwards overlay calls from any thread to the GTK main loop."""

    def __init__(self, overlay: Overlay) -> None:
        self._overlay = overlay
        self._hide_timer: int | None = None

    def _call(self, action: Callable[[], None]) -> None:
        def run() -> bool:
            if self._hide_timer is not None:
                GLib.source_remove(self._hide_timer)
                self._hide_timer = None
            action()
            return False

        GLib.idle_add(run)

    def listening(self) -> None:
        self._call(self._overlay.listening)

    def partial(self, text: str) -> None:
        self._call(lambda: self._overlay.partial(text))

    def level(self, value: float) -> None:
        def run() -> bool:
            self._overlay.level(value)
            return False

        GLib.idle_add(run)

    def cleaning(self) -> None:
        self._call(self._overlay.cleaning)

    def hide(self) -> None:
        self._call(self._overlay.hide)

    def error(self, message: str) -> None:
        def show_then_schedule_hide() -> None:
            self._overlay.error(message)
            self._hide_timer = GLib.timeout_add_seconds(ERROR_DISPLAY_S, self._expire_error)

        self._call(show_then_schedule_hide)

    def _expire_error(self) -> bool:
        self._hide_timer = None
        self._overlay.hide()
        return False


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

    def on_hotkey_down(self) -> None:
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
        try:
            transcript = await self._transcriber.run(audio)
            transcribed_at = time.monotonic()
            if not transcript:
                self._ui.hide()
                return
            text = transcript
            if self._cleaner and needs_cleanup(transcript, self._config.cleanup_min_words):
                self._ui.cleaning()
                text = await self._cleaner.clean(transcript)
            self._ui.hide()
            log.info(
                "latency after release: transcript %.2fs, cleanup %.2fs",
                transcribed_at - self._released_at,
                time.monotonic() - transcribed_at,
            )
            log.debug("raw: %s", transcript)
            log.debug("clean: %s", text)
            await asyncio.get_running_loop().run_in_executor(None, self._output, text)
        except asyncio.CancelledError:
            self._ui.hide()
        except Exception as error:
            log.exception("dictation failed")
            self._ui.error(str(error) or type(error).__name__)
        finally:
            await self._stop_recorder()
            self._audio = None
            self._task = None
            self._state = State.IDLE


def run_app(
    config: Config, api_key: str, cleanup: bool, output: Callable[[str], None] | None
) -> None:
    from gi.repository import Gtk

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, name="asyncio", daemon=True).start()

    ui = OverlayBridge(Overlay())
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
