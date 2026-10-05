"""Branded intro sequences for Video Producer output (ADR-2213).

Each produced video opens with a short (~3-5s), uniquely-animated reveal of
the Corvin mark (`assets/brand/logo-mark-color.svg` — the only Corvin symbol
asset found in the repo; nothing here invents a logo), then crossfades into
the main video. Multiple animation variants exist so consecutive videos don't
look identical (the operator's "like the Simpsons" reference: same brand
mark, different opening each time), while staying visually consistent (same
mark, same dark theme, same color).

Rendering uses Playwright's native video recording (record_video_dir) rather
than frame-by-frame PNG export + reassembly: a CSS animation plays in real
time in a real browser and Chromium's own video sink captures it, which is
both simpler and more accurate than hand-sampling frame timings for a CSS
easing curve.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
LOGO_MARK_SVG_PATH = _REPO_ROOT / "assets" / "brand" / "logo-mark-color.svg"

# Same dark theme as diagram/themes.py's "corvin-dark" — intros and diagrams
# must share one background color or the crossfade shows a visible seam.
BG_COLOR = "#0B0E14"

DEFAULT_DURATION_S = 3.5
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080


class IntroRenderError(RuntimeError):
    """The intro sequence could not be rendered — caller should fail closed
    (skip the intro, use the main video alone) rather than crash the job."""


@dataclass(frozen=True)
class IntroVariant:
    id: str
    label: str
    css: str  # keyframe animation CSS; must animate #mark and finish within DEFAULT_DURATION_S


def _read_logo_svg() -> str:
    if not LOGO_MARK_SVG_PATH.exists():
        raise IntroRenderError(f"logo mark asset not found at {LOGO_MARK_SVG_PATH}")
    return LOGO_MARK_SVG_PATH.read_text(encoding="utf-8")


# Each variant animates the SAME mark (brand consistency) with a DIFFERENT
# motion (the "always different" requirement). All finish by 3.2s, leaving a
# ~0.3s hold before the crossfade into the main video starts.
VARIANTS: list[IntroVariant] = [
    IntroVariant(
        id="scale_pulse",
        label="Scale Pulse",
        css="""
            #mark { opacity: 0; transform: scale(0); }
            @keyframes v_scale_pulse {
                0%   { opacity: 0; transform: scale(0); }
                60%  { opacity: 1; transform: scale(1.15); }
                80%  { opacity: 1; transform: scale(0.95); }
                100% { opacity: 1; transform: scale(1); }
            }
            #mark { animation: v_scale_pulse 1.6s cubic-bezier(0.34, 1.56, 0.64, 1) 0.2s forwards; }
        """,
    ),
    IntroVariant(
        id="slide_diagonal",
        label="Slide Diagonal",
        css="""
            #mark { opacity: 0; transform: translate(-220px, 220px) scale(0.6); }
            @keyframes v_slide_diagonal {
                0%   { opacity: 0; transform: translate(-220px, 220px) scale(0.6); }
                100% { opacity: 1; transform: translate(0, 0) scale(1); }
            }
            #mark { animation: v_slide_diagonal 1.8s cubic-bezier(0.22, 1, 0.36, 1) 0.2s forwards; }
        """,
    ),
    IntroVariant(
        id="rotate_in",
        label="Rotate In",
        css="""
            #mark { opacity: 0; transform: rotate(-200deg) scale(0.3); }
            @keyframes v_rotate_in {
                0%   { opacity: 0; transform: rotate(-200deg) scale(0.3); }
                70%  { opacity: 1; transform: rotate(10deg) scale(1.05); }
                100% { opacity: 1; transform: rotate(0deg) scale(1); }
            }
            #mark { animation: v_rotate_in 1.9s cubic-bezier(0.33, 1, 0.68, 1) 0.2s forwards; }
        """,
    ),
    IntroVariant(
        id="glitch_reveal",
        label="Glitch Reveal",
        css="""
            #mark { opacity: 0; transform: translateX(0); }
            @keyframes v_glitch_reveal {
                0%   { opacity: 0; transform: translateX(0); }
                10%  { opacity: 1; transform: translateX(-30px); }
                20%  { opacity: 0.3; transform: translateX(25px); }
                30%  { opacity: 1; transform: translateX(-15px); }
                40%  { opacity: 0.5; transform: translateX(10px); }
                50%  { opacity: 1; transform: translateX(-5px); }
                100% { opacity: 1; transform: translateX(0); }
            }
            #mark { animation: v_glitch_reveal 1.4s steps(1, end) 0.2s forwards; }
        """,
    ),
    IntroVariant(
        id="spiral_zoom",
        label="Spiral Zoom",
        css="""
            #mark { opacity: 0; transform: scale(4) rotate(90deg); }
            @keyframes v_spiral_zoom {
                0%   { opacity: 0; transform: scale(4) rotate(90deg); }
                100% { opacity: 1; transform: scale(1) rotate(0deg); }
            }
            #mark { animation: v_spiral_zoom 2.0s cubic-bezier(0.16, 1, 0.3, 1) 0.2s forwards; }
        """,
    ),
]

_VARIANTS_BY_ID = {v.id: v for v in VARIANTS}


def pick_variant(seed: str) -> IntroVariant:
    """Deterministic variant choice from a job id / topic string, so the same
    job always renders the same intro (reproducible) but different jobs
    spread across all variants (the "always different" requirement) without
    needing external state to track "which one did we use last"."""
    idx = sum(ord(c) for c in seed) % len(VARIANTS)
    return VARIANTS[idx]


def _page_html(variant: IntroVariant, width: int, height: int) -> str:
    svg = _read_logo_svg()
    # The source SVG uses currentColor + a prefers-color-scheme media query
    # for light/dark; force the dark-theme color directly since the intro's
    # background is always BG_COLOR here, not the viewer's OS theme.
    svg = svg.replace("color:#11161D", "color:#EEF1F5")
    mark_size = min(width, height) * 0.4
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{ width: {width}px; height: {height}px; background: {BG_COLOR}; overflow: hidden; }}
  #stage {{ width: {width}px; height: {height}px; display: flex; align-items: center; justify-content: center; }}
  #mark {{ width: {mark_size}px; height: {mark_size}px; }}
  #mark svg {{ width: 100%; height: 100%; display: block; }}
  {variant.css}
</style></head>
<body>
  <div id="stage"><div id="mark">{svg}</div></div>
</body></html>"""


def render_intro(
    variant: IntroVariant,
    out_dir: str,
    duration_s: float = DEFAULT_DURATION_S,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> str:
    """Render one intro variant to an MP4 file inside out_dir, return its path.

    Raises IntroRenderError on any failure (missing asset, Playwright
    unavailable, ffmpeg conversion failure) — callers decide whether to skip
    the intro or propagate, this function never silently returns a bad path.
    """
    try:
        from playwright.sync_api import Error as PlaywrightError, sync_playwright
    except ImportError as e:
        raise IntroRenderError(f"playwright not available: {e}") from e

    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)
    video_staging = out_dir_path / f".intro_staging_{variant.id}"
    video_staging.mkdir(exist_ok=True)

    html = _page_html(variant, width, height)
    html_file = video_staging / "intro.html"
    html_file.write_text(html, encoding="utf-8")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    record_video_dir=str(video_staging),
                    record_video_size={"width": width, "height": height},
                )
                page = context.new_page()
                page.goto(html_file.as_uri())
                page.wait_for_timeout(int(duration_s * 1000))
                video_handle = page.video
                page.close()
                context.close()
                if video_handle is None:
                    raise IntroRenderError("Playwright produced no video handle")
                webm_path = video_handle.path()
            finally:
                browser.close()
    except PlaywrightError as e:
        shutil.rmtree(video_staging, ignore_errors=True)
        raise IntroRenderError(f"rendering failed: {e}") from e

    mp4_path = str(out_dir_path / f"intro_{variant.id}.mp4")
    try:
        result = subprocess.run(
            # The recording starts BEFORE goto() and runs until close(), so
            # it is duration_s plus page-load time — more under CPU load
            # (measured 4.52 s for a 2 s request). The animation starts at
            # load, so keep exactly the LAST duration_s seconds.
            ["ffmpeg", "-y", "-sseof", f"-{duration_s:.3f}", "-i", webm_path, "-t", f"{duration_s:.3f}",
             "-c:v", "libx264", "-preset", "medium",
             "-crf", "18", "-pix_fmt", "yuv420p", "-an", mp4_path],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            raise IntroRenderError(f"webm->mp4 conversion failed: {result.stderr}")
    finally:
        shutil.rmtree(video_staging, ignore_errors=True)

    return mp4_path


def prepend_intro_with_fade(
    intro_path: str,
    main_video_path: str,
    output_path: str,
    fade_duration_s: float = 0.6,
) -> str:
    """Concatenate intro_path + main_video_path with a crossfade at the
    join, writing output_path. Uses ffmpeg's xfade filter (video) and
    acrossfade (audio) — the intro has no audio track (-an above), so its
    silence crossfades into the main video's narration rather than cutting
    in abruptly.

    Raises IntroRenderError on ffmpeg failure; caller decides whether to
    fall back to the main video without an intro.
    """
    intro_duration = _probe_duration(intro_path)
    if intro_duration <= 0:
        raise IntroRenderError(f"could not determine intro duration for {intro_path}")

    offset = max(intro_duration - fade_duration_s, 0.0)

    # The intro has no audio track; the main video's narration must start at
    # the same point its video starts appearing (the crossfade's offset), not
    # at 0 — adelay shifts audio forward in time (silence inserted at the
    # START). apad was tried first and is wrong here: it pads at the END,
    # which left the combined clip ~1.5s longer than intro+main-fade and was
    # caught by this file's own duration-assertion test, not by inspection.
    delay_ms = int(offset * 1000)
    cmd = [
        "ffmpeg", "-y",
        "-i", intro_path,
        "-i", main_video_path,
        "-filter_complex",
        f"[0:v][1:v]xfade=transition=fade:duration={fade_duration_s}:offset={offset}[v];"
        f"[1:a]adelay=delays={delay_ms}|{delay_ms}[a]",
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium",
        "-b:v", "600k", "-nal-hrd", "cbr",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    # Re-encodes the WHOLE main video: a fixed 120 s timeout killed this on
    # any video longer than a few minutes. Budget scales with its length.
    main_duration = _probe_duration(main_video_path)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=max(120.0, 3.0 * main_duration + 60.0))
    except subprocess.TimeoutExpired as e:
        raise IntroRenderError(f"intro+main crossfade timed out: {e}") from e
    if result.returncode != 0:
        raise IntroRenderError(f"intro+main crossfade failed: {result.stderr}")
    return output_path


def _probe_duration(path: str) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return 0.0
