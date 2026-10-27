from __future__ import annotations

import logging
import threading
from collections.abc import Iterable

from Xlib import XK, X, display, error

log = logging.getLogger(__name__)

SPACE_KEYS = (XK.XK_space,)
LOCKED_KEYS = (XK.XK_Return, XK.XK_KP_Enter, XK.XK_Escape)
# X.AnyModifier would fail as a whole when the desktop already grabs one combination, such as
# Super+Space in GNOME, so each combination the user can hold is grabbed separately.
HELD_MODIFIERS = (0, X.ControlMask, X.ShiftMask, X.ControlMask | X.ShiftMask)
LOCK_MODIFIERS = (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask)
MODIFIERS = tuple(held | lock for held in HELD_MODIFIERS for lock in LOCK_MODIFIERS)


class KeyGrab:
    """Keeps a set of keys away from the focused window, e.g. Enter while a dictation is locked.

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

    def set(self, keysyms: Iterable[int]) -> None:
        """Grabs exactly `keysyms`, releasing keys grabbed before; an empty set releases all."""
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

                for keysym in keysyms:
                    code = connection.keysym_to_keycode(keysym)
                    if not code:
                        continue
                    for modifiers in MODIFIERS:
                        root.grab_key(
                            code,
                            modifiers,
                            False,
                            X.GrabModeAsync,
                            X.GrabModeAsync,
                            onerror=on_error,
                        )
                        self._grabbed.append((code, modifiers))
                connection.sync()
                # Grabbed key events are queued on this connection and never read.
                while connection.pending_events():
                    connection.next_event()
            except Exception:
                log.exception("could not change the key grab")
                return
            if failures:
                log.debug("%d key combinations are grabbed by another client", len(failures))
