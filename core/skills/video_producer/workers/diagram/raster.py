"""Compiled diagram (HTML per step) -> PNG frames, via Playwright/Chromium.

Shared by DiagramRendererWorker and the tools/diagram_renderer CLI so the
browser hardening and the content gate exist exactly once.

Defence in depth on top of the compiler's input validation: the page runs
with JavaScript DISABLED and every network request it would make is aborted,
so even a value that slipped past validation cannot execute script or reach
a host (L35) — the document is self-contained by construction.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from .compiler import Compiled

MIN_UNIQUE_COLORS = 12


class DiagramRenderError(RuntimeError):
    """A frame rendered but failed the content gate, or the browser failed."""


def assert_not_solid(png_path: Path, min_unique_colors: int = MIN_UNIQUE_COLORS) -> None:
    """Refuse a near-solid frame (the same heuristic ADR-0712's validator uses
    to flag "add visual content"), applied before the frame ships."""
    from PIL import Image

    with Image.open(png_path) as im:
        im = im.convert("RGB").resize((192, 108))
        colors = im.getcolors(maxcolors=192 * 108)
    n_unique = len(colors) if colors else 0
    if n_unique < min_unique_colors:
        raise DiagramRenderError(
            f"{png_path.name}: only {n_unique} distinct colors after downsampling "
            f"(< {min_unique_colors}) — looks like a near-solid frame, refusing to ship it"
        )


def render_compiled(
    browser, compiled: Compiled, canvas: dict, out_dir: Path,
    min_unique_colors: int = MIN_UNIQUE_COLORS,
) -> List[Path]:
    """Render every step of ``compiled`` into ``out_dir`` (must already exist)."""
    context = browser.new_context(
        viewport={"width": int(canvas["w"]), "height": int(canvas["h"])},
        java_script_enabled=False,
    )
    try:
        context.route("**/*", lambda route: route.abort())
        page = context.new_page()
        page.set_default_timeout(15_000)
        written: List[Path] = []
        for i, html in enumerate(compiled.html_by_step, start=1):
            page.set_content(html, wait_until="load")
            out_path = out_dir / f"frame_{i:02d}.png"
            page.screenshot(path=str(out_path))
            assert_not_solid(out_path, min_unique_colors)
            written.append(out_path)
        return written
    finally:
        context.close()
