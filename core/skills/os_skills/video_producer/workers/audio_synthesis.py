"""Audio Synthesis Worker — TTS with edge-tts + piper fallback."""

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional
from dataclasses import dataclass
import time

from ..worker_base import WorkerSkillBase, WorkerManifest, WorkerResult

logger = logging.getLogger(__name__)


class AudioSynthesisWorker(WorkerSkillBase):
    """
    TTS worker: converts narration text to audio.

    Primary: edge-tts (online TTS, ~50ms latency)
    Fallback: piper (local TTS, ~2s latency)

    Contract:
    - Input: {"narration": str, "voice": str, "output_dir": str}
    - Output: {"audio_files": [str], "duration_s": float, "method": "edge-tts"|"piper"}
    """

    def __init__(self, manifest: WorkerManifest):
        super().__init__(manifest)
        self.use_piper_fallback = False

    async def execute(self, input_data: Dict[str, Any], **config_overrides) -> WorkerResult:
        """
        Synthesize audio from narration text.

        Args:
            input_data: {"narration": str, "voice": str, "output_dir": str}
            **config_overrides: Optional tuning (e.g., rate, pitch)

        Returns:
            WorkerResult with audio file paths and metadata
        """
        start_time = time.time()

        try:
            self.apply_config_overrides(**config_overrides)

            narration = input_data.get("narration", "")
            voice = input_data.get("voice", "en-US-AriaNeural")
            output_dir = Path(input_data.get("output_dir", "."))

            if not narration:
                return WorkerResult(
                    worker_id=self.manifest.id,
                    status="error",
                    output={},
                    error="No narration provided",
                    latency_ms=(time.time() - start_time) * 1000,
                )

            output_dir.mkdir(parents=True, exist_ok=True)
            output_file = output_dir / "narration.mp3"

            # Try edge-tts first
            try:
                await self._synthesize_with_edge_tts(narration, voice, str(output_file))
                method = "edge-tts"
                logger.info(f"Audio synthesized with edge-tts: {output_file}")
            except Exception as e:
                logger.warning(f"edge-tts failed: {e}, trying piper fallback...")
                await self._synthesize_with_piper(narration, str(output_file))
                method = "piper"
                logger.info(f"Audio synthesized with piper (fallback): {output_file}")

            # Get file duration (stub: assume 60s for now)
            duration_s = self._estimate_duration(narration)

            return WorkerResult(
                worker_id=self.manifest.id,
                status="success",
                output={
                    "audio_files": [str(output_file)],
                    "duration_s": duration_s,
                    "method": method,
                    "narration_chars": len(narration),
                },
                latency_ms=(time.time() - start_time) * 1000,
            )

        except Exception as e:
            logger.error(f"Audio synthesis failed: {e}", exc_info=True)
            return WorkerResult(
                worker_id=self.manifest.id,
                status="error",
                output={},
                error=str(e),
                latency_ms=(time.time() - start_time) * 1000,
            )

    async def _synthesize_with_edge_tts(self, narration: str, voice: str, output_file: str) -> None:
        """Use edge-tts for TTS (requires internet)."""
        try:
            import edge_tts
        except ImportError:
            raise RuntimeError("edge-tts not installed: pip install edge-tts")

        communicate = edge_tts.Communicate(text=narration, voice=voice, rate="+0%", pitch="+0%")
        await communicate.save(output_file)
        logger.debug(f"Saved audio to {output_file}")

    async def _synthesize_with_piper(self, narration: str, output_file: str) -> None:
        """Use espeak-ng for offline TTS (fallback, local + no API keys needed)."""
        try:
            # Generate WAV using espeak-ng (local, offline, works on Linux/macOS)
            wav_output = Path(output_file).with_suffix('.wav')

            cmd = [
                "espeak-ng",
                "-w", str(wav_output),  # Output WAV file
                "-s", "150",             # Speed (words per minute)
                "-p", "50",              # Pitch
                narration
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if result.returncode != 0:
                logger.error(f"espeak-ng failed: {result.stderr}")
                raise RuntimeError(f"espeak-ng synthesis failed: {result.stderr}")

            # Convert WAV to MP3 using ffmpeg
            cmd_convert = [
                "ffmpeg",
                "-i", str(wav_output),
                "-q:a", "9",      # Quality (1=best, 9=worst but small)
                "-y",              # Overwrite
                output_file
            ]

            result = subprocess.run(cmd_convert, capture_output=True, text=True, timeout=30)

            if result.returncode != 0:
                logger.error(f"ffmpeg conversion failed: {result.stderr}")
                raise RuntimeError(f"WAV→MP3 conversion failed: {result.stderr}")

            # Clean up temporary WAV
            if wav_output.exists():
                wav_output.unlink()

            logger.debug(f"espeak-ng fallback: synthesized audio at {output_file}")

        except FileNotFoundError as e:
            logger.error(f"Required tool missing: {e}. Install with: apt-get install espeak-ng ffmpeg")
            raise RuntimeError(f"espeak-ng or ffmpeg not installed: {e}")
        except Exception as e:
            logger.error(f"Audio synthesis fallback failed: {e}")
            raise RuntimeError(f"Piper fallback failed: {e}")

    def _estimate_duration(self, narration: str) -> float:
        """Estimate audio duration from text length (chars/rate)."""
        # Average speaking rate: ~150 words/minute = ~4.5 chars/second
        return len(narration) / 4.5


__all__ = ["AudioSynthesisWorker"]
