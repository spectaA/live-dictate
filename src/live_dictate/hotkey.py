from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable
from threading import Lock, Timer

from pynput import keyboard

AUTOREPEAT_GRACE_S = 0.05
KEYPAD_ENTER_VK = 0xFF8D


def key_id(key: keyboard.Key | keyboard.KeyCode) -> tuple[str, object]:
    # KeyCode equality also compares internal fields (`_symbol`) that differ between
    # `HotKey.parse` results and listener events for the same physical key.
    code = key.value if isinstance(key, keyboard.Key) else key
    if code.char:
        return ("char", code.char.lower())
    return ("vk", code.vk)


def _is_enter(key: Hashable) -> bool:
    if key == keyboard.Key.enter:
        return True
    return isinstance(key, keyboard.KeyCode) and key.vk == KEYPAD_ENTER_VK


class ComboTracker:
    """Reports combo edges; `normalize` maps a raw key to every target name it satisfies."""

    def __init__(
        self, targets: Iterable[Hashable], normalize: Callable[[Hashable], set[Hashable]]
    ) -> None:
        self._targets = set(targets)
        self._normalize = normalize
        self._held: set[Hashable] = set()
        self._active = False

    def press(self, key: Hashable) -> bool:
        self._held |= self._normalize(key) & self._targets
        if not self._active and self._held == self._targets:
            self._active = True
            return True
        return False

    def release(self, key: Hashable) -> bool:
        self._held -= self._normalize(key)
        if self._active and self._held != self._targets:
            self._active = False
            return True
        return False


class HotkeyListener:
    def __init__(
        self,
        spec: str,
        on_down: Callable[[], None],
        on_up: Callable[[], None],
        on_escape: Callable[[], None],
        on_enter: Callable[[], None] | None = None,
        on_space: Callable[[], None] | None = None,
    ) -> None:
        self._on_down = on_down
        self._on_up = on_up
        self._on_escape = on_escape
        self._on_enter = on_enter
        self._on_space = on_space
        self._lock = Lock()
        self._pending_up: Timer | None = None
        self._listener = keyboard.Listener(on_press=self._press, on_release=self._release)
        targets = [key_id(key) for key in keyboard.HotKey.parse(spec)]
        self._tracker = ComboTracker(targets, self._normalize)

    def _normalize(self, key: Hashable) -> set[Hashable]:
        # pynput reports None for keys without a keysym.
        if key is None:
            return set()
        # `<ctrl_r>` must match only the right key, while `<ctrl>` must match either side.
        return {key_id(key), key_id(self._listener.canonical(key))}

    def _press(self, key: Hashable) -> None:
        if key == keyboard.Key.esc:
            self._on_escape()
        elif self._on_enter and _is_enter(key):
            self._on_enter()
        elif self._on_space and key == keyboard.Key.space:
            self._on_space()
        if not self._tracker.press(key):
            return
        with self._lock:
            pending, self._pending_up = self._pending_up, None
        if pending:
            pending.cancel()
            return
        self._on_down()

    def _release(self, key: Hashable) -> None:
        if not self._tracker.release(key):
            return
        # X11 autorepeat on a held non-modifier key emits release+press pairs.
        timer = Timer(AUTOREPEAT_GRACE_S, lambda: self._fire_up(timer))
        with self._lock:
            self._pending_up = timer
        timer.start()

    def _fire_up(self, timer: Timer) -> None:
        with self._lock:
            if self._pending_up is not timer:
                return
            self._pending_up = None
        self._on_up()

    def start(self) -> None:
        self._listener.start()

    def stop(self) -> None:
        self._listener.stop()
