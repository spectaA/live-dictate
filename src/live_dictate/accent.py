from __future__ import annotations

import re

HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

# libadwaita's palette for the accent-color key introduced in GNOME 47.
GNOME_ACCENTS = {
    "blue": "#3584e4",
    "teal": "#2190a4",
    "green": "#3a944a",
    "yellow": "#c88800",
    "orange": "#ed5b00",
    "red": "#e62d42",
    "pink": "#d56199",
    "purple": "#9141ac",
    "slate": "#6f8396",
}


def system_accent() -> str | None:
    """Returns the GNOME 47+ accent color, or None on older desktops.

    GTK theme colors are deliberately ignored: themes such as Pop name a cyan color
    `accent_color` while users perceive their theme as orange.
    """
    from gi.repository import Gio

    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup("org.gnome.desktop.interface", True) if source else None
    if schema is None or not schema.has_key("accent-color"):
        return None
    name = Gio.Settings.new("org.gnome.desktop.interface").get_string("accent-color")
    return GNOME_ACCENTS.get(name)


def resolve_accent(configured: str) -> str | None:
    """Returns the accent to use, or None for the neutral look."""
    return configured or system_accent()


def hex_to_rgb(color: str) -> tuple[float, float, float]:
    if not HEX_COLOR.match(color):
        raise ValueError(f"expected a #rrggbb color, got {color!r}")
    return (int(color[1:3], 16) / 255, int(color[3:5], 16) / 255, int(color[5:7], 16) / 255)
