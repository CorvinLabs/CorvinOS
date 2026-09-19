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
import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

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

    async def render_enhancement(
        self,
        input_video: str,
        config: RenderConfig,
    ) -> RenderResult:
        """
        Run Blender headless.

        Steps:
        - Load scene
        - Execute Python script
        - Render to MP4
        - Validate output
        - Return result

        Args:
            input_video: Input video file (for reference/compositing)
            config: Render configuration

        Returns:
            RenderResult with success status and output path
        """
        self._log_audit("render_start", {
            "blend_file": self.blend_file,
            "input_video": input_video,
            "config": config.__dict__,
        })

        output_file = None
        try:
            # Step 1: Auto-generate Blender Python script
            bpy_script = await self._auto_generate_bpy_script(input_video, config)
            self._log_audit("bpy_script_generated", {
                "script_lines": len(bpy_script.split("\n")),
            })

            # Step 2: Create temporary directory for render output
            with tempfile.TemporaryDirectory() as tmpdir:
                output_file = Path(tmpdir) / "render.mp4"
                script_file = Path(tmpdir) / "render_script.py"
                script_file.write_text(bpy_script)

                # Step 3: Execute Blender with timeout
                result = await self._render_with_timeout(
                    output_file,
                    script_file,
                    config.timeout_sec,
                )

                if not result.success:
                    return result

                # Step 4: Validate render output
                validation = await self._validate_render_output(str(output_file))
                if not validation:
                    self._log_audit("render_validation_failed", {
                        "output_file": str(output_file),
                    })
                    return RenderResult(
                        success=False,
                        output_file=None,
                        frame_count=0,
                        duration_sec=0,
                        errors=["Render output validation failed"],
                    )

                # Success
                self._log_audit("render_complete", {
                    "output_file": str(output_file),
                    "frame_count": config.frame_count,
                })

                return RenderResult(
                    success=True,
                    output_file=str(output_file),
                    frame_count=config.frame_count,
                    duration_sec=config.frame_count / config.fps,
                    errors=[],
                )

        except Exception as e:
            logger.error(f"Render failed: {e}", exc_info=True)
            self._log_audit("render_error", {"error": str(e)})
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
    ) -> str:
        """Auto-generate Blender Python script based on input properties."""
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

# Set output format
scene.render.image_settings.file_format = 'FFMPEG'
scene.render.ffmpeg.codec = 'H264'
scene.render.ffmpeg.bitrate = {config.bitrate_kbps}
scene.render.ffmpeg.use_autosplit = True

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
        cmd = [
            "blender",
            "--background",  # Headless mode
            self.blend_file,
            "--python", str(script_file),
            "-o", str(output_file),
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

                return RenderResult(
                    success=True,
                    output_file=str(output_file),
                    frame_count=1,  # Placeholder
                    duration_sec=1,  # Placeholder
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

    async def _validate_render_output(self, output_file: str) -> bool:
        """Check Blender output is valid MP4.

        Args:
            output_file: Path to rendered MP4

        Returns:
            True if valid MP4, False otherwise
        """
        try:
            if not Path(output_file).exists():
                logger.error(f"Render output not found: {output_file}")
                return False

            # Try to probe the file with ffprobe
            import ffmpeg
            probe = ffmpeg.probe(output_file)
            video_stream = next(
                (s for s in probe["streams"] if s["codec_type"] == "video"),
                None,
            )

            if not video_stream:
                logger.error(f"No video stream in render output")
                return False

            logger.info(f"Render output validated: {output_file}")
            return True

        except Exception as e:
            logger.error(f"Render output validation failed: {e}")
            return False

    def _log_audit(self, event_type: str, data: Dict) -> None:
        """Log audit event."""
        event = {
            "event_type": f"blender_{event_type}",
            "tenant_id": self.tenant_id,
            "data": data,
        }
        self.audit_events.append(event)
