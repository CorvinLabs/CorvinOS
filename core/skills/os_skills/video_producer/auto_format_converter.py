"""
Auto Format Converter — Autonomous Format Conversion

Features:
- Auto-detect optimal codec (H.264 preferred, VP9 fallback)
- Auto-calculate optimal bitrate
- Resolution preservation or upscaling
- Audio codec selection (AAC preferred)
- FFmpeg command auto-generation
- Compliance-first hardening (ADR-0720)

Compliance:
- ADR-0692: Video Producer Orchestration
- ADR-0720: Fail-closed hardening
- ADR-0232: Audit trail
"""

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ConversionParams:
    """Format conversion parameters."""
    video_codec: str  # "h264" | "vp9"
    bitrate_kbps: int
    resolution: str  # "1920x1080" | "preserve"
    audio_codec: str  # "aac" | "opus"
    audio_bitrate_kbps: int
    preset: str  # "fast" | "medium" | "slow"
    crf: Optional[int] = None  # Quality (0-51, lower=better)


@dataclass
class ConversionResult:
    """Result of format conversion."""
    success: bool
    output_file: Optional[str]
    input_codec: str
    output_codec: str
    bitrate_kbps: int
    duration_sec: Optional[float]
    errors: List[str]


#: H.264 bits per pixel per frame, by resolution tier (upper pixel bound).
_BPP_TIERS = (
    (854 * 480, 0.10),     # SD (480p)
    (1280 * 720, 0.09),    # HD (720p)
    (1920 * 1080, 0.08),   # FHD (1080p)
    (3840 * 2160, 0.07),   # 4K
)
_BPP_8K = 0.06
_MIN_KBPS, _MAX_KBPS = 500, 80_000


def optimal_bitrate_kbps(resolution: str, fps: float) -> int:
    """Target H.264 video bitrate in kbps: ``pixels * fps * bpp / 1000``.

    ``bpp`` (bits per pixel per frame) falls from 0.10 at SD to 0.06 at 8K,
    giving ~0.9 Mbps at 480p30, ~5 Mbps at 1080p30 and ~17 Mbps at 2160p30,
    clamped to [500, 80 000] kbps.

    The old factor (0.3–1.0 bpp) was 5-10x too high and ran into its 20 Mbps
    cap already at 1080p30, so 1080p, 1440p and 4K all got the SAME bitrate.
    """
    try:
        width, height = map(int, str(resolution).split("x"))
    except (ValueError, AttributeError):
        logger.warning(f"Invalid resolution: {resolution}, using default")
        width, height = 1920, 1080
    pixels = max(0, width) * max(0, height)
    bpp = next((b for bound, b in _BPP_TIERS if pixels <= bound), _BPP_8K)
    bitrate = int(pixels * max(float(fps), 0.0) * bpp / 1000)
    return max(_MIN_KBPS, min(_MAX_KBPS, bitrate))


class AutoFormatConverter:
    """
    Autonomous format conversion. Auto-detect optimal codec & bitrate.

    Input: Any format → Output: H.264 MP4 with optimal settings
    """

    def __init__(self, tenant_id: str = "_default"):
        """Initialize converter.

        Args:
            tenant_id: Tenant scoping for audit trail
        """
        self.tenant_id = tenant_id
        self.audit_events: List[Dict] = []

    async def convert_to_mp4(
        self,
        input_file: str,
        output_file: str,
        input_metadata: Optional[Dict] = None,
    ) -> ConversionResult:
        """
        Auto-detect optimal settings and convert to MP4.

        Auto-detects:
        - Best video codec (H.264 preferred, VP9 fallback)
        - Optimal bitrate (auto-calculate from input resolution)
        - Resolution preservation or upscaling
        - Audio codec (AAC preferred)

        Args:
            input_file: Input video file (any format)
            output_file: Output MP4 path
            input_metadata: Optional pre-detected metadata

        Returns:
            ConversionResult
        """
        self._log_audit("conversion_start", {
            "input_file": input_file,
            "output_file": output_file,
        })

        try:
            # Parse input resolution if metadata available
            if input_metadata:
                resolution = input_metadata.get("resolution", "1920x1080")
                fps = input_metadata.get("fps", 30)
                duration_sec = input_metadata.get("duration_sec", 0)
                input_codec = input_metadata.get("video_codec", "unknown")
            else:
                resolution = "1920x1080"  # Default
                fps = 30
                duration_sec = 0
                input_codec = "unknown"

            # Auto-detect optimal codec
            params = self._auto_select_codec_and_bitrate(resolution, fps)

            # Generate ffmpeg command
            cmd = self._generate_ffmpeg_command(
                input_file,
                output_file,
                params,
            )

            # Execute conversion
            logger.info(f"Executing: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3600,  # 1 hour timeout
            )

            if result.returncode != 0:
                error_msg = result.stderr or "Unknown error"
                logger.error(f"FFmpeg conversion failed: {error_msg}")
                self._log_audit("conversion_error", {
                    "error": error_msg,
                    "return_code": result.returncode,
                })
                return ConversionResult(
                    success=False,
                    output_file=None,
                    input_codec=input_codec,
                    output_codec=params.video_codec,
                    bitrate_kbps=params.bitrate_kbps,
                    duration_sec=None,
                    errors=[error_msg],
                )

            # Verify output file exists
            if not Path(output_file).exists():
                error_msg = "Output file was not created"
                logger.error(error_msg)
                self._log_audit("conversion_output_missing", {})
                return ConversionResult(
                    success=False,
                    output_file=None,
                    input_codec=input_codec,
                    output_codec=params.video_codec,
                    bitrate_kbps=params.bitrate_kbps,
                    duration_sec=None,
                    errors=[error_msg],
                )

            self._log_audit("conversion_complete", {
                "output_file": output_file,
                "codec": params.video_codec,
                "bitrate_kbps": params.bitrate_kbps,
            })

            return ConversionResult(
                success=True,
                output_file=output_file,
                input_codec=input_codec,
                output_codec=params.video_codec,
                bitrate_kbps=params.bitrate_kbps,
                duration_sec=duration_sec,
                errors=[],
            )

        except subprocess.TimeoutExpired:
            logger.error("Conversion timeout (>1 hour)")
            self._log_audit("conversion_timeout", {})
            return ConversionResult(
                success=False,
                output_file=None,
                input_codec="unknown",
                output_codec="h264",
                bitrate_kbps=0,
                duration_sec=None,
                errors=["Conversion timeout exceeded"],
            )
        except Exception as e:
            logger.error(f"Conversion error: {e}", exc_info=True)
            self._log_audit("conversion_exception", {"error": str(e)})
            return ConversionResult(
                success=False,
                output_file=None,
                input_codec="unknown",
                output_codec="h264",
                bitrate_kbps=0,
                duration_sec=None,
                errors=[str(e)],
            )

    def _auto_select_codec_and_bitrate(
        self,
        resolution: str,
        fps: float,
    ) -> ConversionParams:
        """Auto-detect optimal codec and bitrate.

        Returns ConversionParams with selected codec and bitrate.
        """
        bitrate = self._calculate_optimal_bitrate(resolution, fps)

        # H.264 is preferred (wider compatibility)
        return ConversionParams(
            video_codec="h264",
            bitrate_kbps=bitrate,
            resolution=resolution,
            audio_codec="aac",
            audio_bitrate_kbps=256,
            preset="medium",
            crf=None,  # Use bitrate-based quality
        )

    def _calculate_optimal_bitrate(self, resolution: str, fps: float) -> int:
        """Auto-calculate bitrate in kbps — see :func:`optimal_bitrate_kbps`."""
        bitrate = optimal_bitrate_kbps(resolution, fps)
        logger.info(f"Auto-selected bitrate: {bitrate} kbps (resolution={resolution}, fps={fps})")
        return bitrate

    def _generate_ffmpeg_command(
        self,
        input_file: str,
        output_file: str,
        params: ConversionParams,
    ) -> List[str]:
        """Generate optimal ffmpeg command string.

        Args:
            input_file: Input video path
            output_file: Output MP4 path
            params: Conversion parameters

        Returns:
            ffmpeg command as list (for subprocess.run)
        """
        cmd = [
            "ffmpeg",
            "-i", input_file,
            "-c:v", "libx264",  # H.264 codec
            "-preset", params.preset,  # fast/medium/slow
            "-b:v", f"{params.bitrate_kbps}k",  # Video bitrate
            "-c:a", "aac",  # AAC audio codec
            "-b:a", f"{params.audio_bitrate_kbps}k",  # Audio bitrate
            "-movflags", "+faststart",  # Enable streaming
            "-y",  # Overwrite output
            output_file,
        ]

        logger.info(f"Generated ffmpeg command: {' '.join(cmd)}")
        return cmd

    def _log_audit(self, event_type: str, data: Dict) -> None:
        """Log audit event."""
        event = {
            "event_type": f"format_converter_{event_type}",
            "tenant_id": self.tenant_id,
            "data": data,
        }
        self.audit_events.append(event)
