from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass

import cairo

from .accent import hex_to_rgb

RGB = tuple[float, float, float]
Rect = tuple[float, float, float, float]

CARD_FILL = (16 / 255, 16 / 255, 22 / 255, 0.97)
RING_WIDTH = 1.5
GLOW_REACH = 18
GLOW_STEP = 2
GLOW_ALPHA = 0.055
SLICES = 96

NEUTRAL = (0.0, 0.0, 0.78)
# (hue shift in degrees, lightness shift, saturation shift) around the accent color.
VARIANTS = (
    (0, 0.0, 0.0),
    (18, 0.12, -0.10),
    (-14, -0.18, 0.05),
    (8, 0.20, -0.20),
    (-22, -0.08, 0.0),
)


@dataclass(frozen=True)
class Palette:
    stops: tuple[RGB, ...]

    def at(self, position: float) -> RGB:
        """Interpolates the circular gradient at `position` in [0, 1)."""
        scaled = (position % 1.0) * len(self.stops)
        index = int(scaled)
        fraction = scaled - index
        start = self.stops[index % len(self.stops)]
        end = self.stops[(index + 1) % len(self.stops)]
        return (
            start[0] + (end[0] - start[0]) * fraction,
            start[1] + (end[1] - start[1]) * fraction,
            start[2] + (end[2] - start[2]) * fraction,
        )


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def palette(accent: str | None) -> Palette:
    if accent:
        hue, lightness, saturation = colorsys.rgb_to_hls(*hex_to_rgb(accent))
        lightness = min(max(lightness, 0.55), 0.75)
    else:
        hue, saturation, lightness = NEUTRAL
    stops = tuple(
        colorsys.hls_to_rgb(
            (hue + shift / 360) % 1.0,
            _clamp(lightness + light_shift),
            # A gray must stay gray: any saturation on hue 0 would tint it red.
            _clamp(saturation + saturation_shift) if accent else 0.0,
        )
        for shift, light_shift, saturation_shift in VARIANTS
    )
    return Palette(stops)


def rounded_rect(cr: cairo.Context, x: float, y: float, w: float, h: float, r: float) -> None:
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _conic(cr: cairo.Context, rect: Rect, colors: Palette, angle: float) -> cairo.Pattern:
    """Renders the conic gradient once, opaque, so overlapping slices leave no visible seams."""
    x, y, w, h = rect
    cx, cy = x + w / 2, y + h / 2
    reach = math.hypot(w, h) / 2 + GLOW_REACH + 1
    step = 2 * math.pi / SLICES
    cr.push_group()
    for index in range(SLICES):
        cr.set_source_rgb(*colors.at(index / SLICES))
        cr.move_to(cx, cy)
        cr.arc(cx, cy, reach, angle + index * step, angle + (index + 1) * step + 0.01)
        cr.close_path()
        cr.fill()
    return cr.pop_group()


def _paint_band(
    cr: cairo.Context,
    rect: Rect,
    radius: float,
    outset: float,
    inset: float,
    gradient: cairo.Pattern,
    alpha: float,
) -> None:
    x, y, w, h = rect
    cr.save()
    cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
    rounded_rect(cr, x - outset, y - outset, w + 2 * outset, h + 2 * outset, radius + outset)
    rounded_rect(cr, x + inset, y + inset, w - 2 * inset, h - 2 * inset, max(radius - inset, 0))
    cr.clip()
    cr.set_source(gradient)
    cr.paint_with_alpha(alpha)
    cr.restore()


def draw_card(
    cr: cairo.Context, rect: Rect, radius: float, colors: Palette, angle: float, glow: float
) -> None:
    """Draws glow, ring and card fill; the glow never extends past GLOW_REACH pixels."""
    x, y, w, h = rect
    gradient = _conic(cr, rect, colors, angle)
    if glow > 0:
        for outset in range(GLOW_REACH, 0, -GLOW_STEP):
            _paint_band(cr, rect, radius, outset, 0, gradient, GLOW_ALPHA * glow)
    _paint_band(cr, rect, radius, 0, RING_WIDTH, gradient, 1.0)
    cr.set_source_rgba(*CARD_FILL)
    inner = radius - RING_WIDTH
    rounded_rect(cr, x + RING_WIDTH, y + RING_WIDTH, w - 2 * RING_WIDTH, h - 2 * RING_WIDTH, inner)
    cr.fill()
