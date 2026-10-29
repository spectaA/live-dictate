from __future__ import annotations

import logging
import threading
from collections.abc import Iterable

from Xlib import X, display, error

log = logging.getLogger(__name__)

# X.AnyModifier would fail as a whole when the desktop already grabs one combination, such as
# Super+Space in GNOME, so each combination the user can hold is grabbed separately.
HELD_MODIFIERS = (0, X.ControlMask, X.ShiftMask, X.ControlMask | X.ShiftMask)
LOCK_MODIFIERS = (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask)
MODIFIERS = tuple(held | lock for held in HELD_MODIFIERS for lock in LOCK_MODIFIERS)


Grab = tuple[int, int]


class KeyGrab:
    """Keeps keys away from the focused window, e.g. Enter while a dictation is locked.

    Each grab is an X keysym and the modifier mask of its shortcut.

    The pynput listener reads keys through the RECORD extension, so it still sees grabbed keys.
    """

    def __init__(self) -> None:
        self._display: display.Display | None = None
        self._lock = threading.Lock()
        self._grabbed: list[tuple[int, int]] = []

    def _connection(self) -> display.Display:
        if self._display is None:
            self._display = display.Display()
        return self._display

    def set(self, grabs: Iterable[Grab]) -> int:
        """Grabs exactly `grabs`, releasing keys grabbed before; an empty set releases all.

        Returns how many combinations another client already grabs.
        """
        with self._lock:
            try:
                connection = self._connection()
                root = connection.screen().root
                for code, modifiers in self._grabbed:
                    root.ungrab_key(code, modifiers)
                self._grabbed = []
                failures: list[error.XError] = []

                def on_error(failure: error.XError, _request: object) -> bool:
                    failures.append(failure)
                    # A false value would also pass the error to the default handler, which prints.
                    return True

                for keysym, mask in grabs:
                    code = connection.keysym_to_keycode(keysym)
                    if not code:
                        continue
                    for modifiers in MODIFIERS:
                        root.grab_key(
                            code,
                            mask | modifiers,
                            False,
                            X.GrabModeAsync,
                            X.GrabModeAsync,
                            onerror=on_error,
                        )
                        self._grabbed.append((code, mask | modifiers))
                connection.sync()
                # Grabbed key events are queued on this connection and never read.
                while connection.pending_events():
                    connection.next_event()
            except Exception:
                log.exception("could not change the key grab")
                return 0
            if failures:
                log.debug("%d key combinations are grabbed by another client", len(failures))
            return len(failures)

    def keep(self, grabs: Iterable[Grab]) -> int:
        """Grabs for the life of the process; returns how many combinations failed."""
        failures = self.set(grabs)
        connection = self._connection()

        def drain() -> None:
            # Unread events would pile up in the X server's buffer for this client.
            while True:
                connection.next_event()

        threading.Thread(target=drain, name="keygrab", daemon=True).start()
        return failures


def is_free(grabs: Iterable[Grab]) -> bool:
    """True when no other client grabs these keys with exactly their modifiers."""
    try:
        connection = display.Display()
    except Exception:
        log.exception("could not open the display")
        return True
    try:
        root = connection.screen().root
        failures: list[error.XError] = []

        def on_error(failure: error.XError, _request: object) -> bool:
            failures.append(failure)
            return True

        for keysym, mask in grabs:
            code = connection.keysym_to_keycode(keysym)
            if not code:
                continue
            for modifiers in LOCK_MODIFIERS:
                root.grab_key(
                    code,
                    mask | modifiers,
                    False,
                    X.GrabModeAsync,
                    X.GrabModeAsync,
                    onerror=on_error,
                )
                connection.sync()
                if not failures:
                    root.ungrab_key(code, mask | modifiers)
                    connection.sync()
            if failures:
                return False
        return True
    finally:
        connection.close()
