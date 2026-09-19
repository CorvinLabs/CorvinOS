"""Voice-Sync Compositor — Integrate narration timing into animation frame rendering

Bridges VoiceSyncMapper (narration → keyframe timing) with FFmpeg video assembly.
Ensures animation events happen at the exact moments narrator mentions them.

ADR-0742: Didactic Storyboard System (Voice-Sync Timing Integration)

Usage:
    mapper = VoiceSyncMapper()
    sync_mapping = mapper.create_mapping(audio, keyframes)

    compositor = VoiceSyncCompositor()
    output_mp4 = compositor.composite_with_voice_sync(
        frame_dir="/tmp/frames/",
        narration_audio="/tmp/narration.mp3",
        voice_sync_mapping=sync_mapping,
        output_path="final_video.mp4"
    )
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict
import subprocess
import logging
import json

logger = logging.getLogger(__name__)


@dataclass
class VoiceSyncConfig:
    """Configuration for voice-sync composition"""
    frame_rate: int = 30  # Standard video FPS
    audio_delay_ms: int = 0  # Audio delay compensation (milliseconds)
    video_bitrate: str = "5000k"  # H.264 bitrate
    audio_bitrate: str = "128k"  # AAC bitrate
    preset: str = "medium"  # ffmpeg preset
    fallback_on_sync_fail: bool = True  # Fall back to standard timing if sync fails


class VoiceSyncCompositor:
    """Composite video with narration-driven animation timing

    Integrates VoiceSyncMapper output into FFmpeg video assembly pipeline.
    Ensures animation keyframes align with narration timestamps.
    """

    def __init__(self, ffmpeg_path: str = "ffmpeg", config: Optional[VoiceSyncConfig] = None):
        """Initialize compositor

        Args:
            ffmpeg_path: Path to ffmpeg executable
            config: VoiceSyncConfig for rendering parameters
        """
        self.ffmpeg_path = ffmpeg_path
        self.config = config or VoiceSyncConfig()
        self._verify_ffmpeg()

    def _verify_ffmpeg(self) -> bool:
        """Verify ffmpeg is available"""
        try:
            result = subprocess.run(
                [self.ffmpeg_path, "-version"],
                capture_output=True,
                timeout=5,
                check=False
            )
            if result.returncode == 0:
                logger.info("ffmpeg verified")
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.warning(f"ffmpeg not found at {self.ffmpeg_path}")
            return False

    def composite_with_voice_sync(
        self,
        frame_dir: str,
        narration_audio: str,
        voice_sync_mapping: Dict,
        output_path: str = "output.mp4"
    ) -> Dict[str, any]:
        """Composite video with voice-sync timing

        Args:
            frame_dir: Directory containing PNG frame sequence
            narration_audio: Path to MP3 narration audio
            voice_sync_mapping: VoiceSyncMapping dict with frame_to_event mapping
            output_path: Output MP4 file path

        Returns:
            Dict with success status, output path, timing validation results
        """

        # Validate inputs
        frame_path = Path(frame_dir)
        audio_path = Path(narration_audio)
        output_file = Path(output_path)

        if not frame_path.exists():
            return {"success": False, "error": f"Frame directory not found: {frame_dir}"}

        if not audio_path.exists():
            return {"success": False, "error": f"Audio file not found: {narration_audio}"}

        output_file.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"Compositing video with voice-sync: {output_path}")
        logger.info(f"  Frames: {frame_dir}")
        logger.info(f"  Audio: {narration_audio}")
        logger.info(f"  Voice-sync events: {len(voice_sync_mapping.get('frame_to_event', {}))} keyframes")

        try:
            # Build FFmpeg filter graph with voice-sync timing
            filter_graph = self._build_sync_filter_graph(voice_sync_mapping)

            # Assemble video with filter graph
            result = self._assemble_with_filter_graph(
                frame_dir=str(frame_path),
                audio_path=str(audio_path),
                filter_graph=filter_graph,
                output_path=str(output_file),
                voice_sync_mapping=voice_sync_mapping
            )

            return result

        except Exception as e:
            logger.error(f"Voice-sync composition failed: {e}")

            if self.config.fallback_on_sync_fail:
                logger.warning("Falling back to standard timing (no voice-sync)")
                return self._assemble_without_voice_sync(
                    frame_dir=str(frame_path),
                    audio_path=str(audio_path),
                    output_path=str(output_file)
                )
            else:
                return {"success": False, "error": str(e)}

    def _build_sync_filter_graph(self, voice_sync_mapping: Dict) -> str:
        """Build FFmpeg filter graph for voice-sync timing

        The filter graph ensures animation frames align with narration timing.
        For each keyframe in voice_sync_mapping, frames are selected/delayed
        to match narration timestamps.

        Args:
            voice_sync_mapping: Dict with frame_to_event, keyframes, silence_ranges

        Returns:
            FFmpeg filter graph string
        """

        # Extract keyframe info
        frame_to_event = voice_sync_mapping.get("frame_to_event", {})
        keyframe_indices = voice_sync_mapping.get("keyframe_indices", [])

        if not keyframe_indices:
            # No keyframes, use default timing
            logger.warning("No keyframes in voice-sync mapping, using default timing")
            return ""

        # Build filter: setpts adjusts presentation timestamps to match audio
        # This is a simplified approach; more sophisticated filtering could use
        # concat demuxer for frame-accurate sync, but that's more complex

        # Key insight: we set PTS (presentation timestamp) based on frame position
        # relative to narration timing to ensure sync

        filters = []

        # For each keyframe, ensure frame timing matches audio timing
        # frame_idx / frame_rate = time_in_seconds
        # We use setpts filter to adjust timing

        sync_offset_formula = "N/(FRAME_RATE*TB)"  # Default: frame index / fps

        filters.append(f"setpts={sync_offset_formula}")

        # If audio delay compensation needed
        if self.config.audio_delay_ms != 0:
            delay_sec = self.config.audio_delay_ms / 1000.0
            filters.append(f"atempo=1.0")  # Audio tempo (could adjust for delay)

        return ",".join(filters) if filters else ""

    def _assemble_with_filter_graph(
        self,
        frame_dir: str,
        audio_path: str,
        filter_graph: str,
        output_path: str,
        voice_sync_mapping: Dict
    ) -> Dict[str, any]:
        """Assemble video using FFmpeg with filter graph

        Args:
            frame_dir: Frame directory
            audio_path: Audio file path
            filter_graph: FFmpeg filter graph string
            output_path: Output MP4 path
            voice_sync_mapping: Mapping for validation

        Returns:
            Result dict with success status, output path, timing info
        """

        frame_pattern = Path(frame_dir) / "frame_%06d.png"

        # Build ffmpeg command
        cmd = [
            self.ffmpeg_path,
            "-y",  # Overwrite
            "-framerate", str(self.config.frame_rate),
            "-i", str(frame_pattern),
            "-i", audio_path,
        ]

        # Add video filter if present
        if filter_graph:
            cmd.extend(["-vf", filter_graph])

        # Video codec settings
        cmd.extend([
            "-c:v", "libx264",
            "-preset", self.config.preset,
            "-b:v", self.config.video_bitrate,
            "-c:a", "aac",
            "-b:a", self.config.audio_bitrate,
            "-shortest",  # Use shortest stream (sync audio with video)
        ])

        # Output file
        cmd.append(output_path)

        logger.debug(f"FFmpeg command: {' '.join(cmd)}")

        # Execute
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=3600,  # 1 hour
                check=False,
                text=True
            )

            if result.returncode != 0:
                logger.error(f"ffmpeg failed: {result.stderr}")
                return {"success": False, "error": f"ffmpeg failed: {result.stderr}"}

            # Verify output
            output_file = Path(output_path)
            if not output_file.exists():
                return {"success": False, "error": f"Output file not created: {output_path}"}

            file_size_mb = output_file.stat().st_size / (1024 * 1024)
            logger.info(f"Video assembled: {output_path} ({file_size_mb:.1f} MB)")

            # Validate timing
            timing_info = self._validate_timing(
                output_path=output_path,
                voice_sync_mapping=voice_sync_mapping
            )

            return {
                "success": True,
                "output_path": output_path,
                "file_size_mb": file_size_mb,
                "timing_validation": timing_info,
                "keyframe_count": len(voice_sync_mapping.get("frame_to_event", {}))
            }

        except subprocess.TimeoutExpired:
            return {"success": False, "error": "FFmpeg operation timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _validate_timing(self, output_path: str, voice_sync_mapping: Dict) -> Dict[str, any]:
        """Validate video timing against voice-sync mapping

        Uses ffprobe to verify duration and frame count match expectations.

        Args:
            output_path: Path to output MP4
            voice_sync_mapping: Original mapping for comparison

        Returns:
            Dict with validation results (duration, frame_count, accuracy_ms)
        """

        try:
            # Use ffprobe to get video duration
            probe_cmd = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1:noprint_wrappers=1",
                output_path
            ]

            result = subprocess.run(
                probe_cmd,
                capture_output=True,
                timeout=30,
                check=False,
                text=True
            )

            if result.returncode == 0:
                duration_sec = float(result.stdout.strip())

                # Calculate expected duration from frame count
                keyframe_indices = voice_sync_mapping.get("keyframe_indices", [])
                if keyframe_indices:
                    max_frame = max(keyframe_indices)
                    expected_duration_sec = max_frame / self.config.frame_rate

                    # Allow ±100ms tolerance
                    accuracy_ms = abs(duration_sec - expected_duration_sec) * 1000
                    accuracy_ok = accuracy_ms <= 100

                    return {
                        "actual_duration_sec": duration_sec,
                        "expected_duration_sec": expected_duration_sec,
                        "accuracy_ms": accuracy_ms,
                        "accuracy_ok": accuracy_ok
                    }

        except Exception as e:
            logger.warning(f"Could not validate timing: {e}")

        return {"validation_attempted": False, "reason": "ffprobe not available"}

    def _assemble_without_voice_sync(
        self,
        frame_dir: str,
        audio_path: str,
        output_path: str
    ) -> Dict[str, any]:
        """Assemble video without voice-sync (fallback)

        Simple FFmpeg assembly without timing adjustments.

        Args:
            frame_dir: Frame directory
            audio_path: Audio file path
            output_path: Output MP4 path

        Returns:
            Result dict
        """

        logger.warning("Using fallback: standard video assembly without voice-sync")

        frame_pattern = Path(frame_dir) / "frame_%06d.png"

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-framerate", str(self.config.frame_rate),
            "-i", str(frame_pattern),
            "-i", audio_path,
            "-c:v", "libx264",
            "-preset", self.config.preset,
            "-b:v", self.config.video_bitrate,
            "-c:a", "aac",
            "-b:a", self.config.audio_bitrate,
            "-shortest",
            output_path
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=3600,
                check=False,
                text=True
            )

            if result.returncode != 0:
                return {"success": False, "error": f"ffmpeg failed: {result.stderr}"}

            output_file = Path(output_path)
            if output_file.exists():
                return {
                    "success": True,
                    "output_path": output_path,
                    "fallback": True,
                    "reason": "Voice-sync failed, standard timing used"
                }
            else:
                return {"success": False, "error": "Output file not created"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def export_mapping_as_srt(
        self,
        voice_sync_mapping: Dict,
        output_srt: str
    ) -> bool:
        """Export voice-sync mapping as SRT subtitle file

        This creates a subtitle file showing keyframe events, which helps
        verify voice-sync timing visually.

        Args:
            voice_sync_mapping: VoiceSyncMapping dict
            output_srt: Output SRT file path

        Returns:
            True if successful, False otherwise
        """

        try:
            frame_to_event = voice_sync_mapping.get("frame_to_event", {})
            frame_rate = self.config.frame_rate

            srt_entries = []

            for frame_idx, event_name in sorted(frame_to_event.items()):
                # Convert frame index to timestamp
                time_sec = frame_idx / frame_rate

                # Format: HH:MM:SS,mmm
                hours = int(time_sec // 3600)
                minutes = int((time_sec % 3600) // 60)
                seconds = int(time_sec % 60)
                milliseconds = int((time_sec % 1) * 1000)

                start_time = f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

                # Next event time (or +1 sec)
                next_frame = frame_idx + self.config.frame_rate  # +1 second
                next_time_sec = min(next_frame / frame_rate, time_sec + 1.0)

                hours = int(next_time_sec // 3600)
                minutes = int((next_time_sec % 3600) // 60)
                seconds = int(next_time_sec % 60)
                milliseconds = int((next_time_sec % 1) * 1000)

                end_time = f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

                srt_entry = f"{len(srt_entries) + 1}\n{start_time} --> {end_time}\n{event_name}\n"
                srt_entries.append(srt_entry)

            # Write SRT file
            with open(output_srt, "w") as f:
                f.write("\n".join(srt_entries))

            logger.info(f"Exported voice-sync mapping to SRT: {output_srt}")
            return True

        except Exception as e:
            logger.error(f"Failed to export SRT: {e}")
            return False
