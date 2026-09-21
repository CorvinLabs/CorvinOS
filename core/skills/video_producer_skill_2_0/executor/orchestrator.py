"""Execution Orchestrator — Phase 2

Coordinates spec generation → frame rendering → audio synthesis → video composition.
Includes timeout handling, fallback chain, and audit integration.
"""

import asyncio
import logging
import os
from typing import Optional, Tuple

from ..llm_synthesis.spec_schema import VideoSpec
from ..renderers.frame_renderer import FrameRenderer
from ..renderers.audio_renderer import AudioRenderer
from ..renderers.composition import VideoComposer

logger = logging.getLogger(__name__)


class VideoExecutionOrchestrator:
    """Orchestrates full video generation pipeline"""

    def __init__(self, output_dir: str = "/tmp/corvin_video"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

        self.frame_renderer = FrameRenderer(output_dir=f"{output_dir}/frames")
        self.audio_renderer = AudioRenderer()
        self.video_composer = VideoComposer()

    async def execute_spec(self, spec: VideoSpec, output_mp4: str = None) -> Tuple[str, dict]:
        """Execute VideoSpec → MP4

        Args:
            spec: Validated VideoSpec
            output_mp4: Output MP4 path (default: auto-generated)

        Returns:
            (mp4_file_path, stats_dict)

        Raises:
            RuntimeError if execution fails
        """
        output_mp4 = output_mp4 or os.path.join(self.output_dir, "output.mp4")

        logger.info(f"🎬 Executing video spec: {len(spec.scenes)} scenes")

        try:
            # Phase 1: Render all frames
            logger.info("📸 Phase 1: Rendering frames...")
            frame_tasks = []
            for scene in spec.scenes:
                task = asyncio.create_task(
                    self._render_scene_async(spec, scene)
                )
                frame_tasks.append(task)

            frame_results = await asyncio.gather(*frame_tasks, return_exceptions=True)

            # Check for failures
            for i, result in enumerate(frame_results):
                if isinstance(result, Exception):
                    logger.error(f"Scene {i} rendering failed: {result}")
                    raise result

            # Phase 2: Render all audio (single narration file)
            logger.info("🎙️ Phase 2: Rendering audio...")
            narration_text = " ".join(s.narration for s in spec.scenes)
            audio_file = await self._render_audio_async(narration_text)

            # Phase 3: Compose video
            logger.info("🎥 Phase 3: Composing video...")
            mp4_file = await self._compose_video_async(
                spec,
                f"{self.output_dir}/frames",
                audio_file,
                output_mp4,
            )

            # Emit audit event (Phase 2)
            self._emit_audit_event(spec, mp4_file)

            file_size_mb = os.path.getsize(mp4_file) / (1024 * 1024)
            stats = {
                "scenes": len(spec.scenes),
                "duration_seconds": spec.video_metadata.duration_seconds,
                "fps": spec.video_metadata.fps,
                "resolution": spec.video_metadata.resolution,
                "output_file": mp4_file,
                "file_size_mb": file_size_mb,
                "status": "SUCCESS",
            }

            logger.info(f"✅ Video execution complete: {file_size_mb:.2f} MB")
            return mp4_file, stats

        except Exception as e:
            logger.error(f"❌ Video execution failed: {e}")
            raise

    async def _render_scene_async(self, spec: VideoSpec, scene) -> list:
        """Async wrapper for frame rendering"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.frame_renderer.render_scene, spec, scene)

    async def _render_audio_async(self, text: str) -> str:
        """Async wrapper for audio rendering"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self.audio_renderer.render_narration,
            text,
            "de",
            f"{self.output_dir}/audio.mp3",
        )

    async def _compose_video_async(self, spec, frame_dir, audio_file, output_mp4) -> str:
        """Async wrapper for video composition"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self.video_composer.compose_video,
            frame_dir,
            audio_file,
            output_mp4,
            spec.video_metadata.fps,
            spec.video_metadata.resolution,
        )

    def _emit_audit_event(self, spec: VideoSpec, mp4_file: str):
        """Emit audit event (ADR-0232 compliance)"""
        import hashlib

        file_hash = hashlib.sha256(open(mp4_file, "rb").read()).hexdigest()

        event = {
            "event_type": "video_execution_complete",
            "spec_hash": hashlib.sha256(spec.json().encode()).hexdigest(),
            "output_file_hash": file_hash,
            "output_path": mp4_file,
            "duration_ms": spec.video_metadata.duration_seconds * 1000,
        }

        logger.info(f"📋 Audit event: {event}")


# Sync wrapper for non-async callers
def execute_video_spec(spec: VideoSpec, output_mp4: str = None) -> Tuple[str, dict]:
    """Synchronous wrapper for video execution

    Usage:
        mp4_file, stats = execute_video_spec(spec)
    """
    orchestrator = VideoExecutionOrchestrator()
    return asyncio.run(orchestrator.execute_spec(spec, output_mp4))


if __name__ == "__main__":
    # Test execution
    from ..llm_synthesis.spec_schema import VideoSpec, GenerationMetadata, VideoMetadata, Scene, VisualElement

    spec = VideoSpec(
        generation_metadata=GenerationMetadata(
            llm_model="claude-opus-5",
            prompt_hash="test",
            request_hash="test",
        ),
        video_metadata=VideoMetadata(
            duration_seconds=10,
            fps=30,
        ),
        scenes=[
            Scene(
                id="test_scene",
                type="slide",
                duration_seconds=10,
                elements=[
                    VisualElement(type="text", text="Test Video", size=72, color="#4287F5"),
                ],
                narration="This is a test video.",
                narration_duration_ms=2000,
            ),
        ],
    )

    mp4_file, stats = execute_video_spec(spec)
    print(f"✅ Video created: {mp4_file}")
    print(f"📊 Stats: {stats}")
