"""
Phase 5: Final Composition & Color Grading
Assembles all video segments, blends particle effects, adds audio, applies color grading
"""

import os
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List
import logging
import tempfile

from base_utils import BasePhase, PhaseOutput, FFmpegHelper

logger = logging.getLogger(__name__)


class CompositorPhase(BasePhase):
    """Composite all video segments into final output"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config, "Phase5_Compositor")
        self.phase_config = config.get("phases", {}).get("phase5_compositor", {})
        self.video_config = config.get("video", {})
        self.audio_config = config.get("audio", {})
        self.ffmpeg = FFmpegHelper(config)
        self.output_config = config.get("output", {})

    def concatenate_segments(
        self,
        segments: List[Dict[str, Any]]
    ) -> str:
        """Concatenate video segments into single master video"""

        self.log("Concatenating video segments...")

        # Create concat demuxer file
        concat_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)

        try:
            for segment in segments:
                source_file = segment.get("source")
                if source_file and os.path.exists(source_file):
                    concat_file.write(f"file '{source_file}'\n")
                    self.log(f"  Added: {segment.get('name')} ({source_file})")

            concat_file.close()

            master_video = self.phase_config.get("master_video", "./output/corvinos_video_master.mp4")
            Path(master_video).parent.mkdir(parents=True, exist_ok=True)

            cmd = [
                "ffmpeg",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_file.name,
                "-c:v", "libx264",
                "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-y",
                master_video
            ]

            success, output = self.run_command(cmd)

            if success:
                self.log(f"Concatenation complete: {master_video}")
            else:
                self.log("Concatenation failed", "error")
                master_video = ""

            return master_video

        finally:
            if os.path.exists(concat_file.name):
                os.unlink(concat_file.name)

    def blend_particles(
        self,
        base_video: str,
        particle_video: str
    ) -> str:
        """Blend particle effects over base video"""

        self.log("Blending particle effects...")

        blend_output = "./output/corvinos_with_particles.mp4"
        Path(blend_output).parent.mkdir(parents=True, exist_ok=True)

        blend_mode = self.phase_config.get("blend_mode", "lighten")
        opacity = self.phase_config.get("particle_opacity", 0.7)

        # FFmpeg filter for blending
        blend_filters = {
            "lighten": "blend=lighten",
            "screen": "blend=screen",
            "add": "blend=addition",
            "overlay": f"overlay=0:0:alpha=continuous:format=yuv444"
        }

        filter_str = blend_filters.get(blend_mode, "blend=lighten")

        # Scale both videos to same resolution
        w = self.video_config.get("width", 1920)
        h = self.video_config.get("height", 1080)

        cmd = [
            "ffmpeg",
            "-i", base_video,
            "-i", particle_video,
            "-filter_complex",
            f"[0]scale={w}:{h}[v0];[1]scale={w}:{h}[v1];[v0][v1]{filter_str}[out]",
            "-map", "[out]",
            "-c:v", "libx264",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-y",
            blend_output
        ]

        success, _ = self.run_command(cmd)

        if success:
            self.log(f"Particle blending complete: {blend_output}")
            return blend_output
        else:
            self.log("Particle blending failed, using base video", "warning")
            return base_video

    def add_audio(
        self,
        video_file: str,
        audio_file: str
    ) -> str:
        """Add narration audio to video"""

        self.log(f"Adding audio: {audio_file}")

        # Check if audio file exists
        if not os.path.exists(audio_file):
            self.log(f"Audio file not found: {audio_file}", "warning")
            return video_file

        audio_with_sound = "./output/corvinos_with_audio.mp4"
        Path(audio_with_sound).parent.mkdir(parents=True, exist_ok=True)

        audio_bitrate = self.audio_config.get("bitrate", "192k")

        cmd = [
            "ffmpeg",
            "-i", video_file,
            "-i", audio_file,
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", audio_bitrate,
            "-shortest",
            "-y",
            audio_with_sound
        ]

        success, _ = self.run_command(cmd)

        if success:
            self.log(f"Audio added: {audio_with_sound}")
            return audio_with_sound
        else:
            self.log("Audio addition failed, continuing without audio", "warning")
            return video_file

    def apply_color_grading(
        self,
        input_video: str
    ) -> str:
        """Apply color grading to final video"""

        self.log("Applying color grading...")

        color_graded = "./output/corvinos_color_graded.mp4"
        Path(color_graded).parent.mkdir(parents=True, exist_ok=True)

        # Simple color grading using FFmpeg filters
        # Enhance saturation and apply color balance for CorvinOS theme

        filter_str = (
            "eq=saturation=1.3:brightness=0.05,"  # Slightly increase saturation
            "colorbalance=rl=0.1:gm=0.05:by=0.1,"  # Add warm tones
            "hue=s=1.1"  # Enhance color intensity
        )

        cmd = [
            "ffmpeg",
            "-i", input_video,
            "-vf", filter_str,
            "-c:v", "libx264",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-y",
            color_graded
        ]

        success, _ = self.run_command(cmd)

        if success:
            self.log(f"Color grading applied: {color_graded}")
            return color_graded
        else:
            self.log("Color grading failed, using previous version", "warning")
            return input_video

    def get_video_segments(self) -> List[Dict[str, Any]]:
        """Collect all video segments from previous phases"""

        segments = []

        # Phase 1: Blender Intro
        phase1_output = "./output/phase1_blender_intro.mp4"
        if os.path.exists(phase1_output):
            segments.append({
                "name": "Intro (Blender)",
                "source": phase1_output,
                "start": 0,
                "duration": 5
            })

        # Phase 2: Manim - Four Pillars
        phase2_four_pillars = "./output/phase2_four_pillars.mp4"
        if os.path.exists(phase2_four_pillars):
            segments.append({
                "name": "Four Pillars (Manim)",
                "source": phase2_four_pillars,
                "start": 5,
                "duration": 6
            })

        # Phase 2: Manim - Architecture
        phase2_architecture = "./output/phase2_architecture.mp4"
        if os.path.exists(phase2_architecture):
            segments.append({
                "name": "Architecture (Manim)",
                "source": phase2_architecture,
                "start": 11,
                "duration": 6
            })

        # Phase 3: SVG Flows
        for flow_name in ["healthcare", "finance", "government"]:
            flow_file = f"./output/phase3_{flow_name}_flow.mp4"
            if os.path.exists(flow_file):
                segments.append({
                    "name": f"{flow_name.title()} Flow (SVG)",
                    "source": flow_file,
                    "duration": 8
                })

        self.log(f"Found {len(segments)} video segments")
        return segments

    def execute(self) -> PhaseOutput:
        """Execute Phase 5: Final Composition"""

        start_time = datetime.now()

        try:
            self.log("Starting Phase 5: Final Composition & Grading")

            # Step 1: Get all segments
            segments = self.get_video_segments()

            if not segments:
                return PhaseOutput(
                    phase_name="Phase 5: Compositor",
                    video_file="",
                    frame_count=0,
                    duration_seconds=0,
                    status="error",
                    error_message="No video segments found"
                )

            # Step 2: Concatenate segments
            master_video = self.concatenate_segments(segments)
            if not master_video or not os.path.exists(master_video):
                return PhaseOutput(
                    phase_name="Phase 5: Compositor",
                    video_file="",
                    frame_count=0,
                    duration_seconds=0,
                    status="error",
                    error_message="Concatenation failed"
                )

            # Step 3: Blend particles
            particle_video = "./output/phase4_particles.mp4"
            if os.path.exists(particle_video):
                composited_video = self.blend_particles(master_video, particle_video)
            else:
                self.log("Particle video not found, skipping particle blend", "warning")
                composited_video = master_video

            # Step 4: Add audio
            audio_file = self.audio_config.get("narration_file", "/tmp/narration_espeak_natural.aac")
            final_with_audio = self.add_audio(composited_video, audio_file)

            # Step 5: Color grade
            final_output = self.apply_color_grading(final_with_audio)

            # Step 6: Move to final location
            final_location = self.output_config.get("file", "/home/shumway/projects/CorvinOS/outputs/corvinos_premium_final.mp4")
            Path(final_location).parent.mkdir(parents=True, exist_ok=True)

            if os.path.exists(final_output) and final_output != final_location:
                import shutil
                shutil.copy2(final_output, final_location)
                self.log(f"Moved final video: {final_location}")

            # Verify final output
            if not os.path.exists(final_location):
                return PhaseOutput(
                    phase_name="Phase 5: Compositor",
                    video_file="",
                    frame_count=0,
                    duration_seconds=0,
                    status="error",
                    error_message="Final output file not created"
                )

            duration_actual = (datetime.now() - start_time).total_seconds()

            # Get file size
            file_size_mb = os.path.getsize(final_location) / (1024 * 1024)

            self.log(f"Phase 5 complete: {final_location} ({file_size_mb:.1f} MB, {duration_actual:.1f}s)")

            return PhaseOutput(
                phase_name="Phase 5: Compositor",
                video_file=final_location,
                frame_count=int(51 * 25),  # 51 seconds at 25fps
                duration_seconds=51,
                status="success",
                metadata={
                    "file_size_mb": file_size_mb,
                    "duration_actual": duration_actual,
                    "segments_count": len(segments)
                }
            )

        except Exception as e:
            self.log(f"Phase 5 execution failed: {str(e)}", "error")
            return PhaseOutput(
                phase_name="Phase 5: Compositor",
                video_file="",
                frame_count=0,
                duration_seconds=0,
                status="error",
                error_message=str(e)
            )
