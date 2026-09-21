"""E2E Test: Video Producer Phase 2 (Complete Pipeline)

Tests: LLM spec generation → frame rendering → audio synthesis → video composition
"""

import pytest
import os
import asyncio
from pathlib import Path

from core.skills.video_producer_skill_2_0.llm_synthesis import SpecGenerator, validate_and_raise
from core.skills.video_producer_skill_2_0.executor import execute_video_spec
from core.skills.video_producer_skill_2_0.spec_schema import (
    VideoSpec, GenerationMetadata, VideoMetadata, Scene, VisualElement,
    estimate_tts_duration_ms,
)


class TestVideoProducerPhase2:
    """Phase 2: Complete pipeline E2E tests"""

    @pytest.fixture
    def output_dir(self, tmp_path):
        """Temporary output directory"""
        return str(tmp_path / "video_output")

    def test_phase2_complete_pipeline_short_video(self, output_dir):
        """Test complete pipeline: spec → MP4 (10 seconds)"""

        # Create a simple spec (no LLM)
        spec = VideoSpec(
            generation_metadata=GenerationMetadata(
                llm_model="claude-opus-5",
                prompt_hash="abc123",
                request_hash="def456",
            ),
            video_metadata=VideoMetadata(
                duration_seconds=10,
                fps=30,
                resolution=(1920, 1080),
                language="de",
            ),
            scenes=[
                Scene(
                    id="scene_001",
                    type="slide",
                    duration_seconds=5,
                    elements=[
                        VisualElement(
                            type="text",
                            text="ACS RUNNER",
                            size=72,
                            color="#4287F5",
                            x=100,
                            y=250,
                        ),
                    ],
                    narration="ACS Runner Demo.",
                    narration_duration_ms=estimate_tts_duration_ms("ACS Runner Demo."),
                ),
                Scene(
                    id="scene_002",
                    type="slide",
                    duration_seconds=5,
                    elements=[
                        VisualElement(
                            type="text",
                            text="Smart LLM Routing",
                            size=48,
                            color="#34D399",
                            x=100,
                            y=400,
                        ),
                    ],
                    narration="Smart LLM routing saves costs.",
                    narration_duration_ms=estimate_tts_duration_ms("Smart LLM routing saves costs."),
                ),
            ],
        )

        # Validate spec
        validate_and_raise(spec)
        assert len(spec.scenes) == 2
        print(f"✅ Spec validated: {len(spec.scenes)} scenes")

        # Execute pipeline
        mp4_file, stats = execute_video_spec(spec, output_mp4=f"{output_dir}/test_video.mp4")

        # Verify output
        assert os.path.exists(mp4_file), f"MP4 file not created: {mp4_file}"
        assert stats["status"] == "SUCCESS"
        assert stats["duration_seconds"] == 10
        assert stats["file_size_mb"] > 0.1  # At least 100KB

        print(f"✅ Video created: {mp4_file}")
        print(f"📊 Stats: {stats}")

    def test_frame_rendering_correct_count(self, output_dir):
        """Test frame rendering produces correct frame count"""
        from core.skills.video_producer_skill_2_0.renderers import FrameRenderer

        spec = VideoSpec(
            generation_metadata=GenerationMetadata(
                llm_model="test",
                prompt_hash="test",
                request_hash="test",
            ),
            video_metadata=VideoMetadata(
                duration_seconds=5,
                fps=30,
            ),
            scenes=[
                Scene(
                    id="test",
                    type="slide",
                    duration_seconds=5,
                    elements=[],
                    narration="Test",
                    narration_duration_ms=1000,
                ),
            ],
        )

        renderer = FrameRenderer(output_dir=output_dir)
        frames = renderer.render_spec(spec)

        expected_frames = 5 * 30  # 5 seconds @ 30 fps = 150 frames
        assert len(frames) == expected_frames, f"Expected {expected_frames} frames, got {len(frames)}"

        for frame_file in frames:
            assert os.path.exists(frame_file), f"Frame not created: {frame_file}"

        print(f"✅ Frame rendering: {len(frames)} frames")

    def test_audio_rendering(self, output_dir):
        """Test audio rendering via OpenAI TTS"""
        from core.skills.video_producer_skill_2_0.renderers import AudioRenderer

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            pytest.skip("OPENAI_API_KEY not set")

        renderer = AudioRenderer(api_key=api_key)
        audio_file = renderer.render_narration(
            "Dies ist ein Testfür die Audioausgabe.",
            language="de",
            output_path=f"{output_dir}/test_audio.mp3",
        )

        assert os.path.exists(audio_file), f"Audio file not created: {audio_file}"

        file_size = os.path.getsize(audio_file)
        assert file_size > 1000, f"Audio file too small: {file_size} bytes"

        print(f"✅ Audio rendering: {file_size} bytes")

    def test_narration_duration_validation(self):
        """Test narration duration ±2s tolerance"""
        from core.skills.video_producer_skill_2_0.llm_synthesis import validate_spec

        # Valid: narration within ±2s
        spec_valid = VideoSpec(
            generation_metadata=GenerationMetadata(
                llm_model="test",
                prompt_hash="test",
                request_hash="test",
            ),
            video_metadata=VideoMetadata(duration_seconds=10),
            scenes=[
                Scene(
                    id="test",
                    type="slide",
                    duration_seconds=10,
                    elements=[],
                    narration="Test narration",
                    narration_duration_ms=10000,  # Exactly 10 seconds
                ),
            ],
        )

        is_valid, errors = validate_spec(spec_valid)
        assert is_valid, f"Valid spec rejected: {errors}"
        print(f"✅ Valid narration timing accepted")

        # Invalid: narration outside ±2s tolerance
        spec_invalid = VideoSpec(
            generation_metadata=GenerationMetadata(
                llm_model="test",
                prompt_hash="test",
                request_hash="test",
            ),
            video_metadata=VideoMetadata(duration_seconds=10),
            scenes=[
                Scene(
                    id="test",
                    type="slide",
                    duration_seconds=10,
                    elements=[],
                    narration="Test narration",
                    narration_duration_ms=20000,  # 20 seconds (±10s error > ±2s tolerance)
                ),
            ],
        )

        is_valid, errors = validate_spec(spec_invalid)
        assert not is_valid, "Invalid spec not rejected"
        assert len(errors) > 0, "No validation errors reported"
        print(f"✅ Invalid narration timing rejected: {errors[0]}")


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
