"""Line icons after Lucide (ISC license), drawn with cairo on a 24-unit grid."""

from __future__ import annotations

import math
from collections.abc import Callable

import cairo

from .aura import rounded_rect

RGB = tuple[float, float, float]
STROKE_WIDTH = 2.0


def _mic(cr: cairo.Context) -> None:
    cr.new_sub_path()
    cr.arc(12, 5, 3, math.pi, 2 * math.pi)
    cr.arc(12, 12, 3, 0, math.pi)
    cr.close_path()
    cr.move_to(19, 10)
    cr.line_to(19, 12)
    cr.arc(12, 12, 7, 0, math.pi)
    cr.line_to(5, 10)
    cr.move_to(12, 19)
    cr.line_to(12, 22)


def _lock(cr: cairo.Context) -> None:
    rounded_rect(cr, 3, 11, 18, 11, 2)
    cr.move_to(7, 11)
    cr.line_to(7, 7)
    cr.arc(12, 7, 5, math.pi, 2 * math.pi)
    cr.line_to(17, 11)


def _sparkles(cr: cairo.Context) -> None:
    points = ((12, 2.5), (21.5, 12), (12, 21.5), (2.5, 12))
    cr.move_to(*points[0])
    for index in range(4):
        x0, y0 = points[index]
        x1, y1 = points[(index + 1) % 4]
        # Both control points sit near the center, which pinches the sides inwards.
        cx, cy = 12 + (x0 + x1 - 24) * 0.12, 12 + (y0 + y1 - 24) * 0.12
        cr.curve_to(cx, cy, cx, cy, x1, y1)
    cr.close_path()


def _check(cr: cairo.Context) -> None:
    cr.move_to(4, 12)
    cr.line_to(9, 17)
    cr.line_to(20, 6)


def _alert(cr: cairo.Context) -> None:
    cr.move_to(12, 3.5)
    cr.line_to(21.5, 20)
    cr.line_to(2.5, 20)
    cr.close_path()
    cr.move_to(12, 9.5)
    cr.line_to(12, 13.5)
    cr.move_to(12, 16.8)
    cr.line_to(12, 16.9)


def _sliders(cr: cairo.Context) -> None:
    cr.move_to(20, 7)
    cr.line_to(11, 7)
    cr.move_to(14, 17)
    cr.line_to(5, 17)
    cr.new_sub_path()
    cr.arc(17, 17, 3, 0, 2 * math.pi)
    cr.new_sub_path()
    cr.arc(7, 7, 3, 0, 2 * math.pi)


def _enter(cr: cairo.Context) -> None:
    cr.move_to(9, 10)
    cr.line_to(4, 15)
    cr.line_to(9, 20)
    cr.move_to(20, 4)
    cr.line_to(20, 11)
    cr.arc(16, 11, 4, 0, math.pi / 2)
    cr.line_to(4, 15)


ICONS: dict[str, Callable[[cairo.Context], None]] = {
    "mic": _mic,
    "lock": _lock,
    "sparkles": _sparkles,
    "check": _check,
    "alert": _alert,
    "sliders": _sliders,
    "enter": _enter,
}

# Path length of the check mark, for drawing it progressively.
CHECK_LENGTH = math.hypot(11, 11) + math.hypot(5, 5)


def draw_icon(
    cr: cairo.Context,
    name: str,
    x: float,
    y: float,
    size: float,
    color: RGB,
    alpha: float = 1.0,
    progress: float = 1.0,
) -> None:
    """Strokes icon `name` in a `size` square at (x, y); `progress` < 1 draws part of the path."""
    cr.save()
    cr.translate(x, y)
    cr.scale(size / 24, size / 24)
    cr.new_path()
    ICONS[name](cr)
    cr.set_line_width(STROKE_WIDTH)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    if progress < 1:
        length = CHECK_LENGTH if name == "check" else 100.0
        cr.set_dash([length * progress, length], 0)
    cr.set_source_rgba(*color, alpha)
    cr.stroke()
    cr.restore()
