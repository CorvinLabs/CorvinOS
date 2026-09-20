"""
Phase 4: Particle Effects Overlay
Generates particle flow effects that overlay on the entire video
"""

import os
import random
from pathlib import Path
from datetime import datetime
from typing import Dict, Any
import logging

from base_utils import BasePhase, PhaseOutput

logger = logging.getLogger(__name__)


class ParticleEffectsPhase(BasePhase):
    """Generate particle effect overlays using FFmpeg"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config, "Phase4_Particles")
        self.phase_config = config.get("phases", {}).get("phase4_particles", {})
        self.video_config = config.get("video", {})

    def create_particle_video(self) -> bool:
        """Create particle effects video using FFmpeg drawtext and custom filters"""

        width = self.video_config.get("width", 1920)
        height = self.video_config.get("height", 1080)
        duration = self.phase_config.get("duration_seconds", 51)

        # Use FFmpeg's color source with animated noise/glow effect
        # This creates floating particles using multiple layers

        filter_str = (
            f"color=c=#0f1320:s={width}x{height}:d={duration},"
            f"geq=r='clip(255-abs(x-w/2)*10-abs(y-h/2)*10,0,255)':g='clip(255-abs(x-w/2)*10-abs(y-h/2)*10,0,255)':b='clip(255-abs(x-w/2)*10-abs(y-h/2)*10,0,255)',"
            f"fps=25"
        )

        # Actually, use a simpler approach: generate noise and apply it
        # Create perlin-noise-like particle effect
        filter_str_simple = (
            f"color=c=#0f1320:s={width}x{height}:d={duration},"
            f"noise=alls=4:allf=t,"
            f"threshold=0.7,"
            f"scale={width}:{height}"
        )

        output_file = self.phase_config.get("output_video", "./output/phase4_particles.mp4")
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            "ffmpeg",
            "-f", "lavfi",
            "-i", filter_str_simple,
            "-c:v", "libx264",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-y",
            output_file
        ]

        success, output = self.run_command(cmd)

        if success:
            self.log(f"Created particle effects: {output_file}")
        else:
            # Fallback to solid color if filter fails
            self.log("Particle filter failed, creating solid overlay fallback", "warning")
            cmd_fallback = [
                "ffmpeg",
                "-f", "lavfi",
                "-i", f"color=c=#0f1320:s={width}x{height}:d={duration}",
                "-c:v", "libx264",
                "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-y",
                output_file
            ]
            success, _ = self.run_command(cmd_fallback)

        return success

    def execute(self) -> PhaseOutput:
        """Execute Phase 4: Particle Effects"""

        start_time = datetime.now()

        try:
            self.log("Starting Phase 4: Particle Effects")

            success = self.create_particle_video()

            if not success:
                return PhaseOutput(
                    phase_name="Phase 4: Particles",
                    video_file="",
                    frame_count=0,
                    duration_seconds=0,
                    status="error",
                    error_message="Particle effect generation failed"
                )

            duration = self.phase_config.get("duration_seconds", 51)
            frame_count = self.phase_config.get("frame_count", 1275)
            output_file = self.phase_config.get("output_video", "./output/phase4_particles.mp4")

            if not os.path.exists(output_file):
                return PhaseOutput(
                    phase_name="Phase 4: Particles",
                    video_file="",
                    frame_count=0,
                    duration_seconds=0,
                    status="error",
                    error_message="Output file not created"
                )

            duration_actual = (datetime.now() - start_time).total_seconds()

            self.log(f"Phase 4 complete: {output_file} ({duration_actual:.1f}s)")

            return PhaseOutput(
                phase_name="Phase 4: Particles",
                video_file=output_file,
                frame_count=frame_count,
                duration_seconds=duration,
                status="success",
                metadata={"duration": duration_actual}
            )

        except Exception as e:
            self.log(f"Phase 4 execution failed: {str(e)}", "error")
            return PhaseOutput(
                phase_name="Phase 4: Particles",
                video_file="",
                frame_count=0,
                duration_seconds=0,
                status="error",
                error_message=str(e)
            )
