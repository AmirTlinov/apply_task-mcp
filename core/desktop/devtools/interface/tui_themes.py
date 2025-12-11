#!/usr/bin/env python3
"""TUI themes and styling."""

from typing import Dict

from prompt_toolkit.styles import Style


THEMES: Dict[str, Dict[str, str]] = {
    "dark-olive": {
        "": "#d7dfe6",  # без принудительной подложки
        "status.ok": "#9ad974 bold",
        "status.warn": "#e5c07b bold",
        "status.fail": "#e06c75 bold",
        "status.unknown": "#7a7f85",
        "text": "#d7dfe6",
        "text.dim": "#97a0a9",
        "text.dimmer": "#6d717a",
        "text.cont": "#8d95a0",  # заметно темнее для продолжений
        "selected": "bg:#3b3b3b #d7dfe6 bold",  # мягкий серый селект для моно-режима
        "selected.ok": "bg:#3b3b3b #9ad974 bold",
        "selected.warn": "bg:#3b3b3b #f0c674 bold",
        "selected.fail": "bg:#3b3b3b #ff6b6b bold",
        "selected.unknown": "bg:#3b3b3b #e8eaec bold",
        "header": "#ffb347 bold",
        "border": "#4b525a",
        "icon.check": "#9ad974 bold",
        "icon.warn": "#f9ac60 bold",
        "icon.fail": "#ff5156 bold",
    },
    "dark-contrast": {
        "": "#e8eaec",  # без черной подложки
        "status.ok": "#b8f171 bold",
        "status.warn": "#f0c674 bold",
        "status.fail": "#ff6b6b bold",
        "status.unknown": "#8a9097",
        "text": "#e8eaec",
        "text.dim": "#a7b0ba",
        "text.dimmer": "#6f757d",
        "text.cont": "#939aa4",
        "selected": "bg:#3d4047 #e8eaec bold",  # мягкий серый селект для моно-режима
        "selected.ok": "bg:#3d4047 #b8f171 bold",
        "selected.warn": "bg:#3d4047 #f0c674 bold",
        "selected.fail": "bg:#3d4047 #ff6b6b bold",
        "selected.unknown": "bg:#3d4047 #e8eaec bold",
        "header": "#ffb347 bold",
        "border": "#5a6169",
        "icon.check": "#b8f171 bold",
        "icon.warn": "#f9ac60 bold",
        "icon.fail": "#ff5156 bold",
    },
}

DEFAULT_THEME = "dark-olive"


def get_theme_palette(theme: str) -> Dict[str, str]:
    """Get theme palette, falling back to default if theme not found."""
    base = THEMES.get(theme)
    if not base:
        base = THEMES[DEFAULT_THEME]
    return dict(base)  # defensive copy


def build_style(theme: str) -> Style:
    """Build Style object from theme name."""
    palette = get_theme_palette(theme)
    return Style.from_dict(palette)
