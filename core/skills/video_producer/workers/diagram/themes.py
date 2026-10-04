"""Corvin-Labs visual themes for the diagram renderer.

Colors sourced from the CorvinOS-PPT-Vorlage.pptx branding (dark navy
background, gold accent) already established as the project's house style.
"""
from __future__ import annotations

THEMES: dict[str, dict[str, str]] = {
    "corvin-dark": {
        "bg": "#0B0E14",
        "surface": "#141922",
        "surface_border": "#262E3A",
        "accent": "#F0A830",
        "accent_dim": "#8A6422",
        "text": "#F2F2F2",
        "text_muted": "#9AA3AE",
        "teal": "#2FA8A0",
        "font": "'Calibri', 'Arial', sans-serif",
        "mono": "'Courier New', monospace",
    },
    "corvin-light": {
        "bg": "#FFFFFF",
        "surface": "#F4F5F7",
        "surface_border": "#D8DCE2",
        "accent": "#C97E12",
        "accent_dim": "#E8C691",
        "text": "#141922",
        "text_muted": "#5B6570",
        "teal": "#1E7A74",
        "font": "'Calibri', 'Arial', sans-serif",
        "mono": "'Courier New', monospace",
    },
}


def get_theme(name: str) -> dict[str, str]:
    if name not in THEMES:
        raise ValueError(f"Unknown theme {name!r}; available: {sorted(THEMES)}")
    return THEMES[name]
