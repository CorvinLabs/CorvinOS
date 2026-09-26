"""
Blender Headless Orchestrator — Autonomous Blender Rendering

Features:
- Fully headless (no UI)
- Auto-detect rendering resolution from input
- Timeout protection (render takes too long → skip)
- Output format validation
- Python bpy script automation
- Audit trail integration (ADR-0232)
- Learning events (ADR-0314)

Compliance:
- ADR-0692: Video Producer Orchestration
- ADR-0232: Audit trail
- ADR-0314: Learning events
- ADR-0720: Fail-closed hardening
"""

import asyncio
import logging
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from core.paths import tenant_audit_chain
from core.skills.os_skills.video_producer import audit as blender_audit

logger = logging.getLogger(__name__)


@dataclass
class RenderConfig:
    """Configuration for headless Blender rendering."""
    resolution: str  # "1920x1080"
    fps: int
    frame_count: int
    output_codec: str  # "h264" | "vp9"
    bitrate_kbps: int
    timeout_sec: int = 600  # 10 minute timeout


@dataclass
class RenderResult:
    """Result of Blender rendering operation."""
    success: bool
    output_file: Optional[str]
    frame_count: int
    duration_sec: float
    errors: List[str]


class BlenderHeadlessOrchestrator:
    """
    Fully autonomous Blender rendering in headless mode.

    No UI, fully scripted Python bpy API, timeout-protected.
    """

    def __init__(self, blend_file: str, tenant_id: str = "_default"):
        """Initialize orchestrator.

        Args:
            blend_file: Path to .blend file to render
            tenant_id: Tenant scoping
        """
        self.blend_file = blend_file
        self.tenant_id = tenant_id
        self.audit_events: List[Dict] = []
        logger.info(f"Initialized BlenderHeadlessOrchestrator for {blend_file}")

    def _default_output_path(self) -> Path:
        """Durable render destination when the caller doesn't supply one.

        ``<corvin_home>/tenants/<tid>/global/video_producer/renders/render_<ts>.mp4``
        -- tenant-scoped per ADR-0007, alongside every other tenant-owned artifact.
        """
        from core.paths import tenant_home

        render_dir = tenant_home(self.tenant_id) / "global" / "video_producer" / "renders"
        return render_dir / f"render_{int(time.time() * 1000)}.mp4"

    async def render_enhancement(
        self,
        input_video: str,
        config: RenderConfig,
        output_path: Optional[str] = None,
    ) -> RenderResult:
        """
        Run Blender headless.

        Steps:
        - Load scene
        - Execute Python script
        - Render to MP4
        - Validate output (real ffprobe metadata, not the request echoed back)
        - Copy the render out of the temp dir to a durable path BEFORE it's deleted
        - Return result

        Args:
            input_video: Input video file (for reference/compositing)
            config: Render configuration
            output_path: Durable destination for the rendered MP4. Defaults to
                a tenant-scoped path under ``_default_output_path()`` when omitted.

        Returns:
            RenderResult with success status and output path
        """
        destination = Path(output_path) if output_path else self._default_output_path()

        try:
            # Inside the try: an audit-write failure (audit-first, see
            # _log_audit) must fail this render the same way every other
            # failure here does -- a RenderResult, never an uncaught exception.
            self._log_audit("render_start", {
                "blend_file": self.blend_file,
                "input_video": input_video,
                "resolution": config.resolution,
                "fps": config.fps,
                "frame_count": config.frame_count,
                "output_codec": config.output_codec,
                "bitrate_kbps": config.bitrate_kbps,
                "timeout_sec": config.timeout_sec,
            })

            destination.parent.mkdir(parents=True, exist_ok=True)

            # Step 1: Create temporary directory for render output
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_output = Path(tmpdir) / "render.mp4"
                script_file = Path(tmpdir) / "render_script.py"

                # Step 2: Auto-generate Blender Python script, targeting tmp_output
                bpy_script = await self._auto_generate_bpy_script(input_video, config, tmp_output)
                self._log_audit("bpy_script_generated", {
                    "script_lines": len(bpy_script.split("\n")),
                })
                script_file.write_text(bpy_script)

                # Step 3: Execute Blender with timeout
                result = await self._render_with_timeout(
                    tmp_output,
                    script_file,
                    config.timeout_sec,
                )

                if not result.success:
                    return result

                # Step 4: Validate render output + read REAL stream metadata
                probe = await self._validate_render_output(str(tmp_output))
                if probe is None:
                    self._log_audit("render_validation_failed", {
                        "output_file": str(tmp_output),
                    })
                    return RenderResult(
                        success=False,
                        output_file=None,
                        frame_count=0,
                        duration_sec=0,
                        errors=["Render output validation failed"],
                    )

                # Step 5: copy out of tmpdir BEFORE the `with` block deletes it --
                # returning a path inside tmpdir here would report success for a
                # file that no longer exists by the time the caller reads it.
                shutil.copy2(str(tmp_output), str(destination))

            # tmpdir is gone; `destination` is the durable, caller-visible file.
            self._log_audit("render_complete", {
                "output_file": str(destination),
                "frame_count": probe["frame_count"],
                "duration_sec": probe["duration_sec"],
            })

            return RenderResult(
                success=True,
                output_file=str(destination),
                frame_count=probe["frame_count"],
                duration_sec=probe["duration_sec"],
                errors=[],
            )

        except Exception as e:
            logger.error(f"Render failed: {e}", exc_info=True)
            self._log_audit("render_error", {"error_class": type(e).__name__})
            return RenderResult(
                success=False,
                output_file=None,
                frame_count=0,
                duration_sec=0,
                errors=[str(e)],
            )

    async def _auto_generate_bpy_script(
        self,
        input_video: str,
        config: RenderConfig,
        output_file: Path,
    ) -> str:
        """Auto-generate Blender Python script based on input properties.

        Args:
            input_video: Input video file (for reference/compositing)
            config: Render configuration
            output_file: Real target path for the rendered MP4. Must be set as
                ``scene.render.filepath`` here -- the CLI's own ``-o`` flag is a
                no-op for this invocation (Blender only applies ``-o``/``-a`` in
                combination with an explicit `-a` render-trigger flag; this script
                already renders via ``bpy.ops.render.render()``), so without this
                the file is written wherever the .blend's OWN saved
                render.filepath points, never to output_file.
        """
        width, height = map(int, config.resolution.split("x"))

        script = f"""
import bpy
import sys

# Suppress Blender startup messages
sys.argv = [sys.argv[0], '--background']

# Load the blend file
bpy.ops.wm.open_mainfile(filepath=r'{self.blend_file}')

# Auto-configure scene
scene = bpy.context.scene
scene.render.resolution_x = {width}
scene.render.resolution_y = {height}
scene.render.fps = {config.fps}
scene.frame_end = {config.frame_count}

# Denoising defaults to on (Cycles) and some headless Blender builds ship
# without OpenImageDenoise, which turns a missing optional feature into a
# hard render failure ("Build without OpenImageDenoiser"). This path has no
# interactive preview to denoise for anyway -- force it off unconditionally
# rather than depending on how the .blend file happens to be configured.
for view_layer in scene.view_layers:
    if hasattr(view_layer, "cycles"):
        view_layer.cycles.use_denoising = False

# Set output format
scene.render.filepath = r'{output_file}'
scene.render.image_settings.file_format = 'FFMPEG'
scene.render.ffmpeg.format = 'MPEG4'
scene.render.ffmpeg.codec = 'H264'
scene.render.ffmpeg.video_bitrate = {config.bitrate_kbps}
# audio_codec defaults to 'NONE' -- a .blend with a baked VSE sound strip
# (e.g. narration) would otherwise render silently without an audio stream,
# with nothing in the CLI/API surface indicating audio was dropped.
scene.render.ffmpeg.audio_codec = 'AAC'
# use_autosplit=True makes Blender append a "_000"-style segment suffix to the
# filename UNCONDITIONALLY (not only when a split actually occurs), so the
# real output lands at "<filepath>_000.mp4" while scene.render.filepath still
# reads "<filepath>.mp4" -- the caller's expected output_file then never
# exists. Off: the rendered file is exactly scene.render.filepath.
scene.render.ffmpeg.use_autosplit = False

# Render to video
bpy.ops.render.render(write_still=False, animation=True, scene=scene.name)

print("Blender render complete")
sys.exit(0)
"""
        return script

    async def _render_with_timeout(
        self,
        output_file: Path,
        script_file: Path,
        timeout_sec: int,
    ) -> RenderResult:
        """Execute Blender command with timeout protection.

        Args:
            output_file: Output MP4 path
            script_file: Python script path
            timeout_sec: Maximum render time (seconds)

        Returns:
            RenderResult
        """
        # NOTE: no "-o"/"-a" here. The generated script sets
        # scene.render.filepath = output_file and renders via
        # bpy.ops.render.render() itself; a trailing "-o" without "-a"/"-f" is a
        # silent no-op that used to make this cmd LOOK like it targeted
        # output_file while actually rendering wherever the .blend's own saved
        # render.filepath pointed.
        cmd = [
            "blender",
            "--background",  # Headless mode
            self.blend_file,
            "--python", str(script_file),
        ]

        try:
            logger.info(f"Executing: {' '.join(cmd)}")
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout_sec,
                )
                stdout_text = stdout.decode() if stdout else ""
                stderr_text = stderr.decode() if stderr else ""
                logger.info(f"Blender stdout: {stdout_text}")
                if stderr_text:
                    logger.warning(f"Blender stderr: {stderr_text}")

                if process.returncode != 0:
                    return RenderResult(
                        success=False,
                        output_file=None,
                        frame_count=0,
                        duration_sec=0,
                        errors=[f"Blender exited with code {process.returncode}"],
                    )

                # frame_count/duration_sec are populated later from a real
                # ffprobe of the durable output (see render_enhancement) --
                # this intermediate result only signals "the subprocess exited
                # 0", not what it actually produced.
                return RenderResult(
                    success=True,
                    output_file=str(output_file),
                    frame_count=0,
                    duration_sec=0,
                    errors=[],
                )

            except asyncio.TimeoutError:
                logger.error(f"Blender render timeout after {timeout_sec}s")
                process.kill()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
                return RenderResult(
                    success=False,
                    output_file=None,
                    frame_count=0,
                    duration_sec=0,
                    errors=[f"Render timeout after {timeout_sec}s"],
                )

        except Exception as e:
            logger.error(f"Blender execution failed: {e}")
            return RenderResult(
                success=False,
                output_file=None,
                frame_count=0,
                duration_sec=0,
                errors=[str(e)],
            )

    async def _validate_render_output(self, output_file: str) -> Optional[Dict]:
        """Check Blender output is a valid MP4 and read its real stream metadata.

        Args:
            output_file: Path to rendered MP4

        Returns:
            ``{"frame_count": int, "duration_sec": float}`` from a real ffprobe
            of the file if valid, ``None`` otherwise (missing file / no video
            stream / probe failure -- all fail-closed to "invalid").
        """
        try:
            if not Path(output_file).exists():
                logger.error(f"Render output not found: {output_file}")
                return None

            # Probe the file with ffprobe (via ffmpeg-python) for REAL metadata --
            # never trust the request config for what actually got rendered.
            import ffmpeg
            probe = ffmpeg.probe(output_file)
            video_stream = next(
                (s for s in probe["streams"] if s["codec_type"] == "video"),
                None,
            )

            if not video_stream:
                logger.error("No video stream in render output")
                return None

            duration_sec = float(probe.get("format", {}).get("duration", 0.0))

            try:
                frame_count = int(video_stream.get("nb_frames"))
            except (TypeError, ValueError):
                # Some containers don't store nb_frames; derive it from the
                # real duration and real frame rate instead of guessing.
                fps_raw = video_stream.get("r_frame_rate", "0/1")
                num, _, den = fps_raw.partition("/")
                fps = (float(num) / float(den)) if den and float(den) else 0.0
                frame_count = round(duration_sec * fps) if fps else 0

            logger.info(
                f"Render output validated: {output_file} "
                f"({frame_count} frames, {duration_sec:.2f}s)"
            )
            return {"frame_count": frame_count, "duration_sec": duration_sec}

        except Exception as e:
            logger.error(f"Render output validation failed: {e}")
            return None

    def _log_audit(self, event_type: str, data: Dict) -> None:
        """Log audit event: durable hash-chained write + in-process cache.

        Until this fix, this method only appended to ``self.audit_events`` --
        an in-memory list discarded with the object -- while the class
        docstring claimed ADR-0232/ADR-0314 audit-trail integration that did
        not exist. It now also writes into the real per-tenant chain
        (``core.paths.tenant_audit_chain``), audit-first: a write failure
        raises (see ``audit.emit``) rather than reporting a silently
        unaudited render as successful.
        """
        full_event_type = f"blender.{event_type}"
        event = {
            "event_type": full_event_type,
            "tenant_id": self.tenant_id,
            "data": data,
        }
        self.audit_events.append(event)

        blender_audit.emit(
            full_event_type,
            path=tenant_audit_chain(self.tenant_id),
            tenant_id=self.tenant_id,
            **data,
        )
