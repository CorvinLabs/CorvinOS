"""Diagram Renderer Worker: drawn architecture diagrams + step animation.

Renders declarative diagram specs (box/arrow/grid/brace/highlight, absolute
or relative positioning, anchor-based arrows, progressive-reveal steps) to PNG
frames via Playwright+HTML/CSS/SVG. Result shape mirrors ScreenshotResult so
it fills the same job.screenshots_result slot the video_assembler consumes.

Diagram specs are NOT inferred from narration text (that would be guessing,
not sourcing — ADR-0692's "no invented content" applied to visuals). The
caller supplies one spec per scene; specs are treated as untrusted input
(compiler.normalize_spec validates every field; raster.py renders with
JavaScript off and all network requests aborted).

Fail-closed: no specs, an invalid spec, an unsafe job id, a browser error or
a frame that fails the content gate → success=False with NO frames, and
nothing half-written left behind (frames are rendered into a staging
directory and only swapped into place when every scene passed).
"""
from __future__ import annotations

import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .diagram.compiler import SpecError, compile_spec, normalize_spec
from .diagram.raster import MIN_UNIQUE_COLORS, DiagramRenderError, render_compiled

_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_EMBED_EDGE = 1600
MAX_EMBED_SOURCE_PIXELS = 24_000_000


def _research_images_for(job) -> Dict[str, dict]:
    """ADR-2221: turn the job's IMAGE_RESEARCH result into data URIs for the
    compiler. Every file is decoded and RE-ENCODED to PNG here, so only pixels
    reach the frame — a research_result pointing at a non-image file fails
    instead of being embedded."""
    import base64
    from io import BytesIO

    from PIL import Image

    research = getattr(job, "research_result", None)
    if research is None:
        return {}
    records = research.get("images") if isinstance(research, dict) else getattr(research, "images", None)
    out: Dict[str, dict] = {}
    for ref, rec in (records or {}).items():
        path, citation = rec.get("local_path"), rec.get("citation")
        if not path or not citation:
            raise SpecError(f"research image {ref!r} has no local file or no citation")
        try:
            with Image.open(path) as im:
                w, h = im.size
                if w * h > MAX_EMBED_SOURCE_PIXELS:
                    raise ValueError(f"{w}x{h} px exceeds {MAX_EMBED_SOURCE_PIXELS} px")
                im.draft("RGB", (MAX_EMBED_EDGE, MAX_EMBED_EDGE))  # JPEG: decode at reduced size
                im.thumbnail((MAX_EMBED_EDGE, MAX_EMBED_EDGE))      # shrink BEFORE the RGB copy
                im = im.convert("RGB")
                buf = BytesIO()
                im.save(buf, format="PNG")
        except Exception as e:
            raise SpecError(f"research image {ref!r} is not a decodable image: {e}") from e
        out[ref] = {
            "data_uri": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii"),
            "citation": citation,
        }
    return out


@dataclass
class DiagramRenderResult:
    """Same shape as ScreenshotResult by design."""
    screenshots: List[str]  # rendered PNG frames, flattened in (scene, step) order
    total_duration_seconds: float
    num_captured: int
    confidence: float  # share of requested scenes rendered and gate-passed: 1.0 or 0.0
    success: bool = True
    error: Optional[str] = None
    # Frames grouped per scene in scene order — the assembler times each
    # group to its own scene's narration instead of spreading all evenly.
    frames_by_scene: Optional[List[List[str]]] = None


def _failed(reason: str) -> DiagramRenderResult:
    return DiagramRenderResult(screenshots=[], total_duration_seconds=0.0, num_captured=0,
                               confidence=0.0, success=False, error=reason)


class DiagramRendererWorker:
    """Worker Skill: render declarative diagram specs to PNG frames."""

    def __init__(
        self,
        diagram_specs: Optional[Dict[int, dict]] = None,
        out_dir: Optional[str] = None,
        min_unique_colors: int = MIN_UNIQUE_COLORS,
    ):
        self.name = "diagram_renderer"
        self.version = "1.1.0"
        # No shared world-writable default: a private (0700) directory per
        # worker unless the caller names one.
        self.out_dir = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="diagram_renderer_"))
        self.min_unique_colors = min_unique_colors
        # {scene_index: spec}; set at construction because maestro calls
        # worker.execute(job) with no extra arguments.
        self.diagram_specs = diagram_specs or {}

    def execute(self, job) -> DiagramRenderResult:
        # ADR-2212: a worker instance registered once with MaestroOrchestrator
        # serves many jobs, so specs fixed at construction only work for a
        # single job. Per-job specs on VideoJob.diagram_specs take priority;
        # constructor specs remain for direct (non-Maestro) use and tests.
        specs = self.diagram_specs or getattr(job, "diagram_specs", None)
        if not specs:
            return _failed("no diagram specs configured — nothing to render")

        job_id = getattr(job, "job_id", None)
        if not isinstance(job_id, str) or not _JOB_ID_RE.match(job_id):
            return _failed("job id is not a safe directory name")

        # Validate and compile EVERY scene before rendering any of them.
        compiled = []
        try:
            research_images = _research_images_for(job)
            for raw_key, spec in specs.items():
                key = int(raw_key) if isinstance(raw_key, str) and raw_key.isdigit() else raw_key
                if isinstance(key, bool) or not isinstance(key, int) or not 0 <= key < 1000:
                    raise SpecError(f"scene key {raw_key!r} is not a scene index 0-999")
                norm = normalize_spec(spec)
                compiled.append((key, norm["canvas"], compile_spec(spec, images=research_images)))
        except SpecError as e:
            return _failed(f"invalid diagram spec: {e}")
        compiled.sort(key=lambda t: t[0])

        try:
            from playwright.sync_api import Error as PlaywrightError, sync_playwright
        except ImportError as e:
            return _failed(f"playwright not available: {e}")

        self.out_dir.mkdir(parents=True, exist_ok=True)
        final_dir = self.out_dir / job_id
        staging = self.out_dir / f".{job_id}.staging-{uuid.uuid4().hex[:8]}"
        staging.mkdir()
        try:
            frames: List[Path] = []
            groups: List[List[Path]] = []
            with sync_playwright() as p:
                browser = p.chromium.launch()
                try:
                    for key, canvas, comp in compiled:
                        scene_dir = staging / f"scene_{key:02d}"
                        scene_dir.mkdir()
                        scene_frames = render_compiled(browser, comp, canvas, scene_dir, self.min_unique_colors)
                        groups.append(scene_frames)
                        frames.extend(scene_frames)
                finally:
                    browser.close()
        except (DiagramRenderError, PlaywrightError, OSError) as e:
            shutil.rmtree(staging, ignore_errors=True)
            return _failed(f"rendering failed: {e}")

        # Every scene passed: replace any previous render of this job wholesale
        # (no stale frame_NN from an earlier, longer spec survives).
        if final_dir.exists():
            shutil.rmtree(final_dir)
        staging.rename(final_dir)
        rendered = [str(final_dir / f.relative_to(staging)) for f in frames]
        by_scene = [[str(final_dir / f.relative_to(staging)) for f in g] for g in groups]
        # Only a job whose scenes are 0..n-1 with a spec each maps 1:1 onto
        # the narration scenes; otherwise leave timing to the even spread.
        keys = [k for k, _, _ in compiled]
        narration = getattr(job, "narration", None) or []
        aligned = keys == list(range(len(narration)))
        return DiagramRenderResult(
            screenshots=rendered,
            total_duration_seconds=0.0,  # frames, not timed capture — the assembler assigns durations
            num_captured=len(rendered),
            confidence=1.0,
            frames_by_scene=by_scene if aligned else None,
        )
