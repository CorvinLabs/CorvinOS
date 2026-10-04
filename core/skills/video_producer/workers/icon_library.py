"""Icon Library for Video Producer frames (CONCEPT-0093 Phase 1).

Renders a small, bundled set of inline SVG icons to transparent PNGs via
Playwright — no network dependency (no icon CDN, no font download) and no
cairosvg (not installed in this environment; Playwright's Chromium already
is, and screenshot_capturer.py already depends on it, so this reuses an
existing dependency instead of adding a new one).

Each icon is a self-contained SVG path string (stroke-based, Lucide-style:
24x24 viewBox, 2px stroke, round caps) rendered onto a transparent
background at the requested pixel size and color, then PNG-encoded.
"""

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright

# 24x24 viewBox stroke paths (Lucide-style: round caps/joins, 2px stroke).
# Self-authored, not copied from a licensed icon set — these are simple
# enough (network nodes, boxes, arrows) that there is exactly one
# reasonable way to draw them.
ICONS: dict[str, str] = {
    "network": (
        '<circle cx="12" cy="4" r="2.5"/>'
        '<circle cx="5" cy="19" r="2.5"/>'
        '<circle cx="19" cy="19" r="2.5"/>'
        '<path d="M12 6.5 L6.5 17 M12 6.5 L17.5 17 M7.5 19 L16.5 19"/>'
    ),
    "database": (
        '<ellipse cx="12" cy="5" rx="8" ry="3"/>'
        '<path d="M4 5 V19 C4 20.5 7.5 22 12 22 C16.5 22 20 20.5 20 19 V5"/>'
        '<path d="M4 12 C4 13.5 7.5 15 12 15 C16.5 15 20 13.5 20 12"/>'
    ),
    "link": (
        '<path d="M9 17 H7 a5 5 0 0 1 0 -10 h2"/>'
        '<path d="M15 7 h2 a5 5 0 0 1 0 10 h-2"/>'
        '<path d="M8 12 H16"/>'
    ),
    "search": (
        '<circle cx="10.5" cy="10.5" r="7"/>'
        '<path d="M20 20 L15.5 15.5"/>'
    ),
    "check-circle": (
        '<circle cx="12" cy="12" r="9"/>'
        '<path d="M8 12.5 L10.8 15.3 L16 9.5"/>'
    ),
    "git-branch": (
        '<circle cx="6" cy="5" r="2.2"/>'
        '<circle cx="6" cy="19" r="2.2"/>'
        '<circle cx="18" cy="12" r="2.2"/>'
        '<path d="M6 7.2 V16.8"/>'
        '<path d="M6 10 C6 14 10 14 15.8 12.6"/>'
    ),
    "shield": (
        '<path d="M12 2.5 L20 6 V12 C20 17 16.5 20.5 12 21.5 '
        'C7.5 20.5 4 17 4 12 V6 Z"/>'
        '<path d="M8.5 12 L11 14.5 L15.5 9.5"/>'
    ),
    "zap": (
        '<path d="M13 2 L5 13.5 H11 L10 22 L19 10 H13 Z"/>'
    ),
    "arrow-right": (
        '<path d="M4 12 H20 M14 6 L20 12 L14 18"/>'
    ),
    "layers": (
        '<path d="M12 2.5 L21.5 8 L12 13.5 L2.5 8 Z"/>'
        '<path d="M2.5 13 L12 18.5 L21.5 13"/>'
        '<path d="M2.5 18 L12 23.5 L21.5 18"/>'
    ),
}

_cache: dict[tuple, str] = {}


def render_icon(name: str, size: int = 128, color: str = "#3B82F6",
                 out_dir: Optional[str] = None) -> str:
    """Render one bundled icon to a transparent PNG, return its path.

    Cached per (name, size, color) for the lifetime of the process — a
    video with 10 scenes reusing the same 3 icons renders each combination
    once, not ten times.
    """
    if name not in ICONS:
        raise ValueError(f"Unknown icon {name!r}; available: {sorted(ICONS)}")

    cache_key = (name, size, color)
    if cache_key in _cache and os.path.exists(_cache[cache_key]):
        return _cache[cache_key]

    out_dir = out_dir or tempfile.mkdtemp(prefix="video_icons_")
    os.makedirs(out_dir, exist_ok=True)
    digest = hashlib.sha256(f"{name}{size}{color}".encode()).hexdigest()[:10]
    out_path = str(Path(out_dir) / f"icon_{name}_{digest}.png")

    svg_body = ICONS[name]
    html = f"""<!DOCTYPE html>
<html><head><style>
  html, body {{ margin:0; padding:0; background: transparent; }}
  svg {{ display:block; }}
</style></head>
<body>
  <svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none"
       stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    {svg_body}
  </svg>
</body></html>"""

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": size, "height": size})
            page.set_content(html)
            page.screenshot(path=out_path, omit_background=True)
        finally:
            browser.close()

    _cache[cache_key] = out_path
    return out_path


def available_icons() -> list[str]:
    return sorted(ICONS)
