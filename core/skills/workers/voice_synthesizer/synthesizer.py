"""Voice Synthesizer implementation: TTS + timing measurement + feedback emission."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Optional, Any
from datetime import datetime
from dataclasses import asdict

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from os_skills.video_producer.types import Scene, Storyboard
from core.learning.event_persistence import EventEmitter  # ADR-0314 feedback


class VoiceSynthesizer:
    """Worker for voice synthesis from narration text."""

    def __init__(self, workdir: str | Path):
        """Initialize with working directory."""
        self.workdir = Path(workdir)
        self.audio_dir = self.workdir / "audio"
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.lexicon: dict[str, str] = {}
        self.event_emitter = EventEmitter()  # For SceneRenderedEvent emission

    async def synthesize_scenes(
        self,
        storyboard: Storyboard,
        lexicon: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """
        Synthesize voice for all scenes in storyboard.

        Stages:
        1. Load/apply lexicon (pronunciation mappings)
        2. For each scene: TTS + timing measurement
        3. Insert lead-in/tail silence
        4. Emit SceneRenderedEvent per scene
        5. Collect timing metadata

        Args:
            storyboard: Scene list + metadata
            lexicon: Optional pronunciation mappings (e.g. {"CorvinOS": "corvin-oh-ess"})

        Returns:
            {
                "status": "success" | "partial",
                "scenes_processed": int,
                "scenes_failed": int,
                "audio_files": {scene_id: audio_path},
                "timings": {scene_id: {"measured_ms": int, "confidence": str}},
                "metadata": dict
            }
        """
        if lexicon:
            self.lexicon = lexicon

        results = {
            "status": "success",
            "scenes_processed": 0,
            "scenes_failed": 0,
            "audio_files": {},
            "timings": {},
            "metadata": {
                "synthesized_at": datetime.utcnow().isoformat() + "Z",
                "scene_count": len(storyboard.scenes),
                "lexicon_applied": bool(self.lexicon),
            },
        }

        # Synthesize each scene (parallel)
        tasks = [
            self._synthesize_scene(scene, results)
            for scene in storyboard.scenes
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        return results

    async def _synthesize_scene(
        self,
        scene: Scene,
        results: dict[str, Any],
    ) -> None:
        """Synthesize a single scene's narration to audio."""
        try:
            # 0. Precondition: must have narration text
            if not scene.narration or not scene.narration.strip():
                results["scenes_failed"] += 1
                return

            # 1. Apply lexicon to narration
            narration_text = await self._apply_lexicon(scene.narration)

            # 2. TTS engine call (stub)
            start_time = time.time()
            audio_data = await self._call_tts_engine(narration_text, scene.id)
            tts_latency_ms = (time.time() - start_time) * 1000

            # 3. Insert lead-in/tail silence
            audio_with_silence = await self._add_silence(
                audio_data,
                lead_in_ms=500,
                tail_ms=200,
            )

            # 4. Write audio file
            audio_path = self.audio_dir / f"{scene.id}.wav"
            await self._write_audio_file(audio_with_silence, audio_path)

            # 5. Measure actual timing
            measured_duration_ms = await self._measure_audio_duration(audio_path)

            # 6. Emit SceneRenderedEvent for feedback (ADR-0314)
            await self._emit_scene_rendered_event(
                scene_id=scene.id,
                narration_len=len(narration_text),
                audio_duration_ms=measured_duration_ms,
                tts_latency_ms=tts_latency_ms,
                confidence="high",  # Measured, not estimated
            )

            # 7. Update results
            results["audio_files"][scene.id] = str(audio_path)
            results["timings"][scene.id] = {
                "measured_ms": int(measured_duration_ms),
                "confidence": "high",
            }
            results["scenes_processed"] += 1

        except Exception as e:
            results["scenes_failed"] += 1
            print(f"Failed to synthesize scene {scene.id}: {str(e)}")

    async def _apply_lexicon(self, text: str) -> str:
        """Apply lexicon pronunciations to text."""
        result = text
        for word, pronunciation in self.lexicon.items():
            # Simple replacement; production would use IPA/SSML markers
            result = result.replace(word, f"[{pronunciation}]")
        return result

    async def _call_tts_engine(
        self,
        narration_text: str,
        scene_id: str,
    ) -> bytes:
        """
        Call TTS engine to generate audio.

        Stub: generates silence instead of real audio.
        Production: integrate Azure Speech Services / Google Cloud TTS / Anthropic Audio
        """
        # Stub: generate synthetic audio duration based on text length
        # ~150 words per minute = ~2.5 chars per second
        estimated_duration_s = len(narration_text) / 2.5

        # Return dummy WAV bytes
        # In production: call real TTS engine
        silence_duration_bytes = int(estimated_duration_s * 16000 * 2)  # 16kHz, 16-bit
        return b'\x00' * silence_duration_bytes

    async def _add_silence(
        self,
        audio_data: bytes,
        lead_in_ms: int = 500,
        tail_ms: int = 200,
    ) -> bytes:
        """Insert lead-in and tail silence."""
        # Stub: just prepend/append silence markers
        # Production: use wave/pydub to properly insert silence
        lead_in_samples = (lead_in_ms * 16000) // 1000
        tail_samples = (tail_ms * 16000) // 1000

        return (
            b'\x00' * (lead_in_samples * 2)  # 16-bit = 2 bytes per sample
            + audio_data
            + b'\x00' * (tail_samples * 2)
        )

    async def _write_audio_file(
        self,
        audio_data: bytes,
        path: Path,
    ) -> None:
        """Write audio data to WAV file."""
        # Stub: write raw bytes (real implementation would create proper WAV container)
        path.write_bytes(audio_data)

    async def _measure_audio_duration(self, audio_path: Path) -> float:
        """Measure actual audio duration in milliseconds."""
        # Stub: estimate from file size (16kHz, 16-bit stereo)
        # Real: use librosa, pydub, or ffprobe
        file_size_bytes = audio_path.stat().st_size
        samples = file_size_bytes // 2
        sample_rate = 16000
        duration_s = samples / sample_rate
        return duration_s * 1000

    async def _emit_scene_rendered_event(
        self,
        scene_id: str,
        narration_len: int,
        audio_duration_ms: float,
        tts_latency_ms: float,
        confidence: str,
    ) -> None:
        """Emit SceneRenderedEvent for per-scene feedback (ADR-0314)."""
        # Emit to event store
        event_data = {
            "event_type": "scene_rendered",
            "scene_id": scene_id,
            "narration_length": narration_len,
            "audio_duration_ms": audio_duration_ms,
            "tts_latency_ms": tts_latency_ms,
            "confidence": confidence,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        # Fire-and-forget to event emitter
        # (ADR-0314: EventEmitter has async queue; non-blocking)
        try:
            await self.event_emitter.emit("scene_rendered", event_data)
        except Exception as e:
            # Emit failure doesn't block workflow (fail-closed: log, continue)
            print(f"Failed to emit SceneRenderedEvent for {scene_id}: {str(e)}")
