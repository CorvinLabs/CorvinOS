"""Voice Synthesizer implementation: TTS + timing measurement + feedback emission."""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import time
from pathlib import Path
from typing import Optional, Any
from datetime import datetime
from dataclasses import asdict

import sys
# Imported by the package's REAL path. A sys.path.insert of <repo>/core/skills
# plus `from os_skills...` loads video_producer/types.py a SECOND time under a
# second module name, so the dataclasses here and the ones the rest of the
# codebase holds are different classes and isinstance() is False across the
# seam (2026-09-20 review).
from core.skills.os_skills.video_producer.types import Scene, Storyboard
from core.learning.learning_events import EventType
from core.skills.workers._learning_emit import build_event_emitter, emit_worker_event

logger = logging.getLogger(__name__)

# Same defaults as corvin_operator/bridges/shared/adapter.py::_EDGE_TTS_VOICES
# (a smaller subset -- video_producer only needs a default per top-level
# language code, not the bridge's full locale table).
_EDGE_TTS_VOICES = {
    "en": "en-US-AriaNeural",
    "de": "de-DE-KatjaNeural",
    "es": "es-ES-ElviraNeural",
    "fr": "fr-FR-DeniseNeural",
}


class VoiceSynthesizer:
    """Worker for voice synthesis from narration text."""

    def __init__(self, workdir: str | Path, tenant_id: str = "_default"):
        """Initialize with working directory."""
        self.workdir = Path(workdir)
        self.audio_dir = self.workdir / "audio"
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.lexicon: dict[str, str] = {}
        self.tenant_id = tenant_id
        self.event_emitter = build_event_emitter(tenant_id)

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

            # 4. Write audio file (.mp3 -- video_assembler's FilterGraph/
            # _load_timings glob for audio_dir/{scene_id}.mp3)
            audio_path = self.audio_dir / f"{scene.id}.mp3"
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
        lang: str = "en",
    ) -> bytes:
        """
        Call TTS engine to generate audio.

        edge-tts (Microsoft Neural TTS over HTTPS, no API key) -- the same
        engine + calling convention already used by the voice bridge
        (corvin_operator/bridges/shared/adapter.py::_try_edge_tts). Real MP3
        bytes out, not synthetic silence.

        Disabled under the EU local-only egress guarantee
        (CORVIN_TTS_LOCAL_ONLY=1) -- narration text must not leave the host
        under that mode. There is currently no local (Piper/espeak-ng)
        fallback wired here; a disabled/failed synthesis raises so the
        caller's existing except-and-count-as-failed path handles it, rather
        than silently writing fabricated audio.
        """
        import os as _os

        if _os.environ.get("CORVIN_TTS_LOCAL_ONLY") == "1":
            raise RuntimeError(
                "edge-tts disabled under CORVIN_TTS_LOCAL_ONLY=1 and no local "
                "TTS engine is wired for video_producer yet"
            )

        import edge_tts

        voice = _EDGE_TTS_VOICES.get(lang.lower(), "en-US-AriaNeural")

        with tempfile.TemporaryDirectory() as tmpdir:
            mp3_path = Path(tmpdir) / f"{scene_id}.mp3"
            communicate = edge_tts.Communicate(narration_text, voice)
            await asyncio.wait_for(communicate.save(str(mp3_path)), timeout=30)

            if not mp3_path.exists() or mp3_path.stat().st_size == 0:
                raise RuntimeError(f"edge-tts produced no audio for scene {scene_id}")

            return mp3_path.read_bytes()

    async def _add_silence(
        self,
        audio_data: bytes,
        lead_in_ms: int = 500,
        tail_ms: int = 200,
    ) -> bytes:
        """Insert lead-in and tail silence via a real ffmpeg re-encode.

        ``audio_data`` is a real MP3 stream now (not raw PCM), so silence
        can no longer be byte-concatenated onto it -- that produced a file
        whose header described audio that wasn't there. ``adelay`` shifts
        the whole stream by ``lead_in_ms``; ``apad`` extends the tail.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            in_path = Path(tmpdir) / "in.mp3"
            out_path = Path(tmpdir) / "out.mp3"
            in_path.write_bytes(audio_data)

            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", str(in_path),
                "-af", f"adelay={lead_in_ms}|{lead_in_ms},apad=pad_dur={tail_ms / 1000}",
                str(out_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0 or not out_path.exists():
                raise RuntimeError(
                    f"ffmpeg silence-padding failed: {stderr.decode(errors='replace')[-300:]}"
                )
            return out_path.read_bytes()

    async def _write_audio_file(
        self,
        audio_data: bytes,
        path: Path,
    ) -> None:
        """Write real MP3 bytes to disk."""
        path.write_bytes(audio_data)

    async def _measure_audio_duration(self, audio_path: Path) -> float:
        """Measure REAL audio duration (ms) via ffprobe -- never estimated
        from file size, which assumed a raw-PCM format this file never was."""
        import ffmpeg

        try:
            probe = ffmpeg.probe(str(audio_path))
            duration_s = float(probe.get("format", {}).get("duration", 0.0))
            return duration_s * 1000
        except Exception as e:
            logger.warning(f"Failed to probe audio duration for {audio_path}: {e}")
            return 0.0

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

        emit_worker_event(
            self.event_emitter,
            event_type=EventType.SCENE_RENDERED,
            skill_id="os.video_producer.voice_synthesizer",
            tenant_id=self.tenant_id,
            signal={"milestone": "scene_rendered", **event_data},
        )
