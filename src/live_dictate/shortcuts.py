"""Shortcut specs in pynput's format, such as `<ctrl_r>` or `<ctrl>+<alt>+d`."""

from __future__ import annotations

from Xlib import XK, X

GENERIC = {
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "shift_l": "shift",
    "shift_r": "shift",
    "alt_l": "alt",
    "alt_r": "alt",
    "cmd_l": "cmd",
    "cmd_r": "cmd",
}
MODIFIER_ORDER = ("ctrl", "alt", "alt_gr", "shift", "cmd")
MODIFIERS = {*GENERIC, *MODIFIER_ORDER}
MODIFIER_MASKS = {
    "ctrl": X.ControlMask,
    "shift": X.ShiftMask,
    "alt": X.Mod1Mask,
    "alt_gr": X.Mod5Mask,
    "cmd": X.Mod4Mask,
}

# X keysym name -> pynput key name, for keys that are not characters.
KEYSYM_NAMES = {
    "Control_L": "ctrl_l",
    "Control_R": "ctrl_r",
    "Shift_L": "shift_l",
    "Shift_R": "shift_r",
    "Alt_L": "alt_l",
    "Alt_R": "alt_r",
    "Meta_L": "alt_l",
    "Meta_R": "alt_r",
    "ISO_Level3_Shift": "alt_gr",
    "Super_L": "cmd_l",
    "Super_R": "cmd_r",
    "Return": "enter",
    "KP_Enter": "enter",
    "Escape": "esc",
    "space": "space",
    "Tab": "tab",
    "ISO_Left_Tab": "tab",
    "BackSpace": "backspace",
    "Delete": "delete",
    "Insert": "insert",
    "Home": "home",
    "End": "end",
    "Page_Up": "page_up",
    "Page_Down": "page_down",
    "Up": "up",
    "Down": "down",
    "Left": "left",
    "Right": "right",
    "Pause": "pause",
    "Print": "print_screen",
    "Scroll_Lock": "scroll_lock",
    "Menu": "menu",
    "Caps_Lock": "caps_lock",
    "Num_Lock": "num_lock",
    **{f"F{number}": f"f{number}" for number in range(1, 21)},
}
NAMED_KEYSYMS = {name: keysym for keysym, name in KEYSYM_NAMES.items()}
NAMED_KEYSYMS.update(enter="Return", tab="Tab", alt_l="Alt_L", alt_r="Alt_R")

LABELS = {
    "ctrl": "Ctrl",
    "ctrl_l": "Left Ctrl",
    "ctrl_r": "Right Ctrl",
    "shift": "Shift",
    "shift_l": "Left Shift",
    "shift_r": "Right Shift",
    "alt": "Alt",
    "alt_l": "Left Alt",
    "alt_r": "Right Alt",
    "alt_gr": "AltGr",
    "cmd": "Super",
    "cmd_l": "Left Super",
    "cmd_r": "Right Super",
    "enter": "Enter",
    "esc": "Esc",
    "space": "Space",
    "tab": "Tab",
    "backspace": "Backspace",
    "delete": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "page_up": "Page Up",
    "page_down": "Page Down",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "pause": "Pause",
    "print_screen": "Print",
    "scroll_lock": "Scroll Lock",
    "menu": "Menu",
    "caps_lock": "Caps Lock",
    "num_lock": "Num Lock",
}
KNOWN = MODIFIERS | set(LABELS) | {f"f{number}" for number in range(1, 21)}


def parse(spec: str) -> list[str]:
    """Splits a spec into key names; raises ValueError for an unknown or ambiguous spec."""
    if not spec:
        return []
    keys = []
    for part in spec.split("+"):
        if len(part) > 2 and part.startswith("<") and part.endswith(">"):
            name = part[1:-1]
            if name not in KNOWN:
                raise ValueError(f"unknown key <{name}> in {spec!r}")
            keys.append(name)
        elif len(part) == 1:
            keys.append(part.lower())
        else:
            raise ValueError(f"invalid shortcut {spec!r}")
    if len([key for key in keys if key not in MODIFIERS]) > 1:
        raise ValueError(f"shortcut {spec!r} has more than one non-modifier key")
    return keys


def _part(key: str) -> str:
    return key if len(key) == 1 else f"<{key}>"


def combine(keys: list[str]) -> str:
    """Builds a spec from captured keys; a modifier used alone keeps its side, e.g. Right Ctrl."""
    if len(keys) == 1:
        return _part(keys[0])
    names = {GENERIC.get(key, key) for key in keys}
    modifiers = [name for name in MODIFIER_ORDER if name in names]
    others = [key for key in keys if GENERIC.get(key, key) not in MODIFIER_ORDER]
    return "+".join(_part(key) for key in [*modifiers, *others])


def key_from_keysym(keysym_name: str, char: str) -> str | None:
    if keysym_name in KEYSYM_NAMES:
        return KEYSYM_NAMES[keysym_name]
    # "+" separates keys in a spec.
    if len(char) == 1 and char.isprintable() and not char.isspace() and char != "+":
        return char.lower()
    return None


def labels(spec: str) -> list[str]:
    return [LABELS.get(key, key.upper()) for key in parse(spec)]


def describe(spec: str) -> str:
    return " + ".join(labels(spec)) or "None"


def overlaps(first: str, second: str) -> bool:
    """True when pressing one shortcut also completes the other, e.g. `<ctrl_r>` and `<ctrl>`."""
    one = {GENERIC.get(key, key) for key in parse(first)}
    other = {GENERIC.get(key, key) for key in parse(second)}
    return bool(one and other) and (one <= other or other <= one)


def modifier_mask(spec: str) -> int:
    mask = 0
    for key in parse(spec):
        mask |= MODIFIER_MASKS.get(GENERIC.get(key, key), 0)
    return mask


def grabs(spec: str, held_mask: int = 0) -> list[tuple[int, int]]:
    """X keysym and modifier mask pairs that hold back the spec's non-modifier key.

    A shortcut made of modifiers only returns nothing: modifiers type nothing in the focused app.
    """
    keys = parse(spec)
    mask = modifier_mask(spec) | held_mask
    pairs = []
    for key in keys:
        if key in MODIFIERS:
            continue
        if len(key) == 1:
            codepoint = ord(key)
            # Latin-1 keysyms equal their code point; others use the Unicode keysym range.
            pairs.append((codepoint if codepoint < 0x100 else 0x01000000 | codepoint, mask))
        else:
            pairs.append((XK.string_to_keysym(NAMED_KEYSYMS.get(key, key.upper())), mask))
            if key == "enter":
                pairs.append((XK.XK_KP_Enter, mask))
    return pairs
