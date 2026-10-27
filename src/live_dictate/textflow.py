from __future__ import annotations

import difflib
import string
from html import escape

FADE_IN_S = 0.3
FRESH_S = 1.2
FRESH_COLOR = "#ffffff"
STRIKE_S = 0.35
VANISH_S = 0.45
REMOVAL_S = STRIKE_S + VANISH_S
REMOVED_COLOR = "#f472b6"

_PUNCTUATION = string.punctuation + "«»\u201c\u201d\u2019\u2026"


def _normalize(word: str) -> str:
    return word.strip(_PUNCTUATION).lower()


def removed_word_indices(raw: str, clean: str) -> set[int]:
    """Indices of the words of `raw` that the cleanup dropped or rewrote.

    Punctuation and case are ignored, so "matin" -> "matin," is not a change.
    """
    raw_words = [_normalize(word) for word in raw.split()]
    clean_words = [_normalize(word) for word in clean.split()]
    matcher = difflib.SequenceMatcher(None, raw_words, clean_words, autojunk=False)
    removed: set[int] = set()
    for tag, start, end, _, _ in matcher.get_opcodes():
        if tag in ("delete", "replace"):
            removed.update(range(start, end))
    return removed


def first_word_of(words: list[str], kept_text: str) -> int:
    """Maps the tail kept by `last_lines` back to the index of its first word."""
    dropped = len(" ".join(words)) - len(kept_text)
    offset = 0
    for index, word in enumerate(words):
        if offset >= dropped:
            return index
        offset += len(word) + 1
    return len(words)


def _alpha(value: float) -> str:
    # Pango rejects alpha="0%".
    return f"{max(1, round(value * 100))}%"


class LiveWords:
    """Tracks when each word of the live transcript appeared, to fade new words in."""

    def __init__(self) -> None:
        self.words: list[str] = []
        self._arrivals: list[float] = []

    def update(self, text: str, now: float) -> None:
        new_words = text.split()
        kept = 0
        for old, new in zip(self.words, new_words, strict=False):
            if old != new:
                break
            kept += 1
        self._arrivals = self._arrivals[:kept] + [now] * (len(new_words) - kept)
        self.words = new_words

    def markup(self, first: int, now: float) -> str:
        parts = []
        for word, arrival in zip(self.words[first:], self._arrivals[first:], strict=True):
            age = now - arrival
            if age >= FRESH_S:
                parts.append(escape(word))
            elif age >= FADE_IN_S:
                parts.append(f'<span foreground="{FRESH_COLOR}">{escape(word)}</span>')
            else:
                alpha = _alpha(age / FADE_IN_S)
                parts.append(
                    f'<span foreground="{FRESH_COLOR}" alpha="{alpha}">{escape(word)}</span>'
                )
        return " ".join(parts)


def removal_markup(words: list[str], removed: set[int], first: int, elapsed: float) -> str:
    """Removed words turn pink and get struck through, then fade out."""
    strike = min(1.0, elapsed / STRIKE_S)
    vanish = min(1.0, max(0.0, elapsed - STRIKE_S) / VANISH_S)
    parts = []
    for index in range(first, len(words)):
        word = escape(words[index])
        if index not in removed:
            parts.append(word)
            continue
        attributes = f'foreground="{REMOVED_COLOR}" alpha="{_alpha(1 - vanish)}"'
        if strike >= 1.0:
            attributes += f' strikethrough="true" strikethrough_color="{REMOVED_COLOR}"'
        parts.append(f"<span {attributes}>{word}</span>")
    return " ".join(parts)
