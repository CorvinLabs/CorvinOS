"""A small, license-free inline-SVG icon set (Heroicons-outline derived paths,
MIT licensed upstream). Each icon is a bare <path> list so callers can wrap it
in their own <svg> with whatever stroke color the current theme needs.
"""
from __future__ import annotations

# viewBox is 0 0 24 24 for every icon
ICONS: dict[str, str] = {
    "cpu": (
        '<rect x="6" y="6" width="12" height="12" rx="1.5"/>'
        '<rect x="9" y="9" width="6" height="6" rx="1"/>'
        '<path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3"/>'
    ),
    "mic": (
        '<rect x="9" y="2" width="6" height="12" rx="3"/>'
        '<path d="M5 11a7 7 0 0 0 14 0M12 18v4M8 22h8"/>'
    ),
    "camera": (
        '<path d="M4 8h3l2-3h6l2 3h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z"/>'
        '<circle cx="12" cy="13" r="3.5"/>'
    ),
    "film": (
        '<rect x="3" y="4" width="18" height="16" rx="1"/>'
        '<path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>'
    ),
    "check": (
        '<circle cx="12" cy="12" r="9"/>'
        '<path d="M8 12.5l2.5 2.5L16 9.5"/>'
    ),
    "upload": (
        '<path d="M12 16V4M7 9l5-5 5 5"/>'
        '<path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>'
    ),
    "layers": (
        '<path d="M12 3l9 5-9 5-9-5 9-5z"/>'
        '<path d="M3 13l9 5 9-5M3 9v4M21 9v4"/>'
    ),
    "shield": (
        '<path d="M12 2l8 3v6c0 5-3.5 8.5-8 11-4.5-2.5-8-6-8-11V5l8-3z"/>'
        '<path d="M9 12l2 2 4-4"/>'
    ),
    "gear": (
        '<circle cx="12" cy="12" r="3"/>'
        '<path d="M19.4 13a7.6 7.6 0 0 0 0-2l2-1.5-2-3.5-2.4 1a7.7 7.7 0 0 0-1.7-1l-.4-2.6H9.1l-.4 2.6a7.7 7.7 0 0 0-1.7 1l-2.4-1-2 3.5L4.6 11a7.6 7.6 0 0 0 0 2l-2 1.5 2 3.5 2.4-1c.5.4 1.1.8 1.7 1l.4 2.6h5.8l.4-2.6c.6-.2 1.2-.6 1.7-1l2.4 1 2-3.5z"/>'
    ),
}


def icon_svg(name: str, *, size: int = 28, color: str = "currentColor", stroke_width: float = 1.6) -> str:
    if name not in ICONS:
        raise ValueError(f"Unknown icon {name!r}; available: {sorted(ICONS)}")
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="{stroke_width}" stroke-linecap="round" '
        f'stroke-linejoin="round">{ICONS[name]}</svg>'
    )
