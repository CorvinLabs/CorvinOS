"""E2E Test Suite for ADR-0720: Fail-Closed Validation Gates

Comprehensive test suite validating that all 4 fail-closed gates:
1. Content-Presence Gate (maestro.py)
2. Audio-Duration Gate (openai_tts_worker.py)
3. Visual-Content-Spec Gate (screenshot_capturer.py)
4. Final-Validation Gate (video_assembler.py)

Properly reject invalid content BEFORE production, fail-closed.
"""

import pytest
import tempfile
import os
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.skills.video_producer.maestro import MaestroOrchestrator, VideoJob, VideoJobPhase
from core.skills.video_producer.workers.openai_tts_worker import OpenAITTSWorker, VoiceResult
from core.skills.video_producer.workers.screenshot_capturer import ScreenshotCapturerWorker, ScreenshotResult
from core.skills.video_producer.workers.video_assembler import VideoAssemblerWorker, VideoResult

# Defined BEFORE the classes: `@pytest.mark.skipif(not HAS_PIL, ...)` is
# evaluated while the class body executes, i.e. at import time. This block sat
# at the bottom of the file, so collection raised NameError and none of the
# four ADR-0720 fail-closed gates was ever exercised (2026-09-20 review).
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:  # pragma: no cover - depends on the install
    Image = None  # type: ignore[assignment]
    HAS_PIL = False


class TestGate1ContentPresenceGate:
    """Test GATE 1: Content-Presence Gate in maestro.py"""

    def test_gate1_empty_narration_rejected(self):
        """Gate 1: Empty narration list should be rejected immediately"""
        orchestrator = MaestroOrchestrator()

        with pytest.raises(ValueError) as exc_info:
            orchestrator.create_job(
                topic="Test Video",
                duration=60,
                audience="technical",
                narration=[],  # EMPTY — should fail
                job_id="test_empty_narration"
            )

        assert "Content-Presence Gate FAILED" in str(exc_info.value)
        assert "Narration is empty" in str(exc_info.value)
        assert "test_empty_narration" not in orchestrator.list_jobs()

    def test_gate1_all_blank_scenes_rejected(self):
        """Gate 1: All blank scenes should be rejected"""
        orchestrator = MaestroOrchestrator()

        with pytest.raises(ValueError) as exc_info:
            orchestrator.create_job(
                topic="Test Video",
                duration=60,
                audience="technical",
                narration=["", "   ", "\t\n"],  # All blank
                job_id="test_blank_scenes"
            )

        assert "Content-Presence Gate FAILED" in str(exc_info.value)
        assert "empty out of 3 total" in str(exc_info.value)

    def test_gate1_insufficient_total_content_rejected(self):
        """Gate 1: Total content too short (<20 chars) should be rejected"""
        orchestrator = MaestroOrchestrator()

        with pytest.raises(ValueError) as exc_info:
            orchestrator.create_job(
                topic="Test Video",
                duration=60,
                audience="technical",
                narration=["Hi", "OK"],  # Only 4 chars total — too short
                job_id="test_short_content"
            )

        assert "Content-Presence Gate FAILED" in str(exc_info.value)
        assert "Total narration too short" in str(exc_info.value)

    def test_gate1_valid_narration_passes(self):
        """Gate 1: Valid narration should pass and create job"""
        orchestrator = MaestroOrchestrator()

        job_id = orchestrator.create_job(
            topic="What is CorvinOS?",
            duration=60,
            audience="beginners",
            narration=[
                "CorvinOS is a modern operating system built on agentic principles.",
                "It supports multiple workers and skill-based orchestration.",
                "This video will demonstrate the key features."
            ]
        )

        assert job_id
        assert job_id in orchestrator.list_jobs()

        # Verify audit event
        audit_log = orchestrator.get_audit_log()
        content_validation_events = [e for e in audit_log if e["event_type"] == "content_presence_validated"]
        assert len(content_validation_events) > 0
        assert content_validation_events[0]["details"]["status"] == "passed"


class TestGate2AudioDurationGate:
    """Test GATE 2: Audio-Duration Gate in openai_tts_worker.py"""

    def test_gate2_audio_too_short_rejected(self):
        """Gate 2: Audio duration < 1.0s should be rejected"""
        worker = OpenAITTSWorker()

        with pytest.raises(ValueError) as exc_info:
            worker._validate_audio_duration(total_duration=0.5)  # 500ms — too short

        assert "Audio-Duration Gate FAILED" in str(exc_info.value)
        assert "below minimum 1.0s" in str(exc_info.value)

    def test_gate2_zero_duration_rejected(self):
        """Gate 2: Zero or negative duration should be rejected"""
        worker = OpenAITTSWorker()

        with pytest.raises(ValueError) as exc_info:
            worker._validate_audio_duration(total_duration=0)

        assert "Audio-Duration Gate FAILED" in str(exc_info.value)
        # The gate reports the measured duration against the minimum; the
        # literal "duration is 0s" is a message that no longer exists.
        assert "0.00s" in str(exc_info.value)
        assert "below minimum" in str(exc_info.value)

    def test_gate2_whistle_tone_simulation(self):
        """Gate 2: Simulate whistle tone audio (very short) — should reject"""
        worker = OpenAITTSWorker()

        # Simulate TTS returning almost nothing
        with pytest.raises(ValueError):
            worker._validate_audio_duration(total_duration=0.1)  # 100ms whistle tone

    def test_gate2_valid_duration_passes(self):
        """Gate 2: Valid duration (1.0s–60s) should pass"""
        worker = OpenAITTSWorker()

        # Should not raise
        worker._validate_audio_duration(total_duration=5.0)  # 5 seconds — valid
        worker._validate_audio_duration(total_duration=30.0)  # 30 seconds — valid
        worker._validate_audio_duration(total_duration=60.0)  # 1 minute — valid


class TestGate3VisualContentSpecGate:
    """Test GATE 3: Visual-Content-Spec Gate in screenshot_capturer.py"""

    def test_gate3_no_screenshots_rejected(self):
        """Gate 3: Empty screenshot list should be rejected"""
        worker = ScreenshotCapturerWorker()

        with pytest.raises(ValueError) as exc_info:
            worker._validate_screenshot_content(screenshot_paths=[])

        assert "Visual-Content-Spec Gate FAILED" in str(exc_info.value)
        assert "No screenshots captured" in str(exc_info.value)

    def test_gate3_missing_screenshot_file_rejected(self):
        """Gate 3: Non-existent screenshot file should be rejected"""
        worker = ScreenshotCapturerWorker()

        with pytest.raises(ValueError) as exc_info:
            worker._validate_screenshot_content(screenshot_paths=["/tmp/nonexistent_screenshot.png"])

        assert "Visual-Content-Spec Gate FAILED" in str(exc_info.value)
        assert "not found" in str(exc_info.value).lower() or "File not found" in str(exc_info.value)

    @pytest.mark.skipif(not HAS_PIL, reason="PIL not available")
    def test_gate3_solid_color_screenshot_rejected(self):
        """Gate 3: Solid-color (placeholder) screenshot should be rejected"""
        worker = ScreenshotCapturerWorker()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a solid blue image (placeholder)
            from PIL import Image
            solid_img = Image.new('RGB', (1920, 1080), color='blue')
            screenshot_path = os.path.join(tmpdir, "solid_blue.png")
            solid_img.save(screenshot_path)

            with pytest.raises(ValueError) as exc_info:
                worker._validate_screenshot_content(screenshot_paths=[screenshot_path])

            assert "Visual-Content-Spec Gate FAILED" in str(exc_info.value)
            assert "Insufficient color diversity" in str(exc_info.value) or \
                   "Dominant color covers" in str(exc_info.value)

    def test_gate3_small_screenshot_file_rejected_fallback(self):
        """Gate 3 (fallback without PIL): Very small screenshot file should be rejected.

        The PIL-less branch has to be FORCED. With PIL installed the gate takes
        the advanced-analysis path and rejects the file for a different reason
        ("cannot identify image file"), which is still fail-closed but is not the
        branch this test is named after — so the size check went unexercised.
        """
        worker = ScreenshotCapturerWorker()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a tiny file (less than 1KB)
            tiny_path = os.path.join(tmpdir, "tiny.png")
            with open(tiny_path, "wb") as f:
                f.write(b"PNG" + b"\x00" * 100)  # ~103 bytes

            with patch(
                "core.skills.video_producer.workers.screenshot_capturer.HAS_PIL",
                False,
            ):
                with pytest.raises(ValueError) as exc_info:
                    worker._validate_screenshot_content(screenshot_paths=[tiny_path])

            assert "Visual-Content-Spec Gate FAILED" in str(exc_info.value)
            assert "suspiciously small" in str(exc_info.value).lower()

    @pytest.mark.skipif(not HAS_PIL, reason="PIL not available")
    def test_gate3_diverse_screenshot_passes(self):
        """Gate 3: Real screenshot with diverse colors should pass"""
        worker = ScreenshotCapturerWorker()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a diverse image (not solid color)
            from PIL import Image
            import random

            diverse_img = Image.new('RGB', (1920, 1080))
            pixels = diverse_img.load()

            # Create gradient with many colors
            for x in range(1920):
                for y in range(1080):
                    pixels[x, y] = (
                        int((x / 1920) * 255),
                        int((y / 1080) * 255),
                        random.randint(0, 255)
                    )

            screenshot_path = os.path.join(tmpdir, "diverse.png")
            diverse_img.save(screenshot_path)

            # Should not raise
            worker._validate_screenshot_content(screenshot_paths=[screenshot_path])


class TestGate4FinalValidationGate:
    """Test GATE 4: Final-Validation Gate in video_assembler.py"""

    def test_gate4_missing_video_file_rejected(self):
        """Gate 4: Non-existent video file should be rejected"""
        worker = VideoAssemblerWorker()

        with pytest.raises(ValueError) as exc_info:
            worker._validate_video_quality(
                output_path="/tmp/nonexistent_video.mp4",
                bitrate_kbps=2500,
                duration_seconds=60,
                codec="h264"
            )

        assert "Final-Validation Gate FAILED" in str(exc_info.value)
        assert "not created" in str(exc_info.value)

    def test_gate4_video_file_too_small_rejected(self):
        """Gate 4: Video file < 100KB should be rejected"""
        worker = VideoAssemblerWorker()

        with tempfile.TemporaryDirectory() as tmpdir:
            tiny_video = os.path.join(tmpdir, "tiny.mp4")

            # Create a tiny MP4 file (< 100KB)
            with open(tiny_video, "wb") as f:
                f.write(b"MP4" + b"\x00" * 10000)  # ~10KB

            with pytest.raises(ValueError) as exc_info:
                worker._validate_video_quality(
                    output_path=tiny_video,
                    bitrate_kbps=2500,
                    duration_seconds=60,
                    codec="h264"
                )

            assert "Final-Validation Gate FAILED" in str(exc_info.value)
            assert "too small" in str(exc_info.value).lower()

    def test_gate4_bitrate_too_low_rejected(self):
        """Gate 4: Video bitrate < 100 kbps should be rejected"""
        worker = VideoAssemblerWorker()

        with tempfile.TemporaryDirectory() as tmpdir:
            video_file = os.path.join(tmpdir, "low_bitrate.mp4")

            # Create dummy video file (>100KB to pass size check)
            with open(video_file, "wb") as f:
                f.write(b"MP4" + b"\x00" * 150000)

            with pytest.raises(ValueError) as exc_info:
                worker._validate_video_quality(
                    output_path=video_file,
                    bitrate_kbps=50,  # Too low
                    duration_seconds=60,
                    codec="h264"
                )

            assert "Final-Validation Gate FAILED" in str(exc_info.value)
            assert "bitrate too low" in str(exc_info.value).lower()

    def test_gate4_invalid_codec_rejected(self):
        """Gate 4: Invalid codec should be rejected"""
        worker = VideoAssemblerWorker()

        with tempfile.TemporaryDirectory() as tmpdir:
            video_file = os.path.join(tmpdir, "bad_codec.mp4")

            with open(video_file, "wb") as f:
                f.write(b"MP4" + b"\x00" * 150000)

            with pytest.raises(ValueError) as exc_info:
                worker._validate_video_quality(
                    output_path=video_file,
                    bitrate_kbps=2500,
                    duration_seconds=60,
                    codec="invalid_codec"  # Not in [h264, vp9, av1]
                )

            assert "Final-Validation Gate FAILED" in str(exc_info.value)
            assert "Invalid codec" in str(exc_info.value)

    def test_gate4_zero_duration_rejected(self):
        """Gate 4: Zero duration should be rejected"""
        worker = VideoAssemblerWorker()

        with tempfile.TemporaryDirectory() as tmpdir:
            video_file = os.path.join(tmpdir, "zero_duration.mp4")

            with open(video_file, "wb") as f:
                f.write(b"MP4" + b"\x00" * 150000)

            with pytest.raises(ValueError) as exc_info:
                worker._validate_video_quality(
                    output_path=video_file,
                    bitrate_kbps=2500,
                    duration_seconds=0,  # Invalid
                    codec="h264"
                )

            assert "Final-Validation Gate FAILED" in str(exc_info.value)
            assert "must be positive" in str(exc_info.value).lower()

    def test_gate4_duration_too_short_rejected(self):
        """Gate 4: Duration < 5s should be rejected"""
        worker = VideoAssemblerWorker()

        with tempfile.TemporaryDirectory() as tmpdir:
            video_file = os.path.join(tmpdir, "short_duration.mp4")

            with open(video_file, "wb") as f:
                f.write(b"MP4" + b"\x00" * 150000)

            with pytest.raises(ValueError) as exc_info:
                worker._validate_video_quality(
                    output_path=video_file,
                    bitrate_kbps=2500,
                    duration_seconds=2,  # Too short
                    codec="h264"
                )

            assert "Final-Validation Gate FAILED" in str(exc_info.value)
            assert "too short" in str(exc_info.value).lower()

    def test_gate4_duration_too_long_rejected(self):
        """Gate 4: Duration > 3600s (1 hour) should be rejected"""
        worker = VideoAssemblerWorker()

        with tempfile.TemporaryDirectory() as tmpdir:
            video_file = os.path.join(tmpdir, "long_duration.mp4")

            with open(video_file, "wb") as f:
                f.write(b"MP4" + b"\x00" * 150000)

            with pytest.raises(ValueError) as exc_info:
                worker._validate_video_quality(
                    output_path=video_file,
                    bitrate_kbps=2500,
                    duration_seconds=7200,  # 2 hours — too long
                    codec="h264"
                )

            assert "Final-Validation Gate FAILED" in str(exc_info.value)
            assert "too long" in str(exc_info.value).lower()

    def test_gate4_valid_video_parameters_pass(self):
        """Gate 4: Valid video parameters should pass validation"""
        worker = VideoAssemblerWorker()

        with tempfile.TemporaryDirectory() as tmpdir:
            video_file = os.path.join(tmpdir, "valid_video.mp4")

            with open(video_file, "wb") as f:
                f.write(b"MP4" + b"\x00" * 150000)

            # Should not raise
            worker._validate_video_quality(
                output_path=video_file,
                bitrate_kbps=2500,
                duration_seconds=60,
                codec="h264"
            )

            # Also test with VP9
            worker._validate_video_quality(
                output_path=video_file,
                bitrate_kbps=3000,
                duration_seconds=120,
                codec="vp9"
            )


class TestAllGatesHappyPath:
    """End-to-end happy path: all gates should pass with valid content"""

    def test_all_gates_pass_with_valid_content(self):
        """E2E: Valid narration + audio + screenshots should pass all gates"""
        orchestrator = MaestroOrchestrator()

        # GATE 1: Create job with valid narration
        job_id = orchestrator.create_job(
            topic="Video Production Test",
            duration=60,
            audience="technical",
            narration=[
                "This is a test of the video production pipeline.",
                "All gates should pass with valid content.",
                "The final video should be successfully produced."
            ]
        )

        assert job_id in orchestrator.list_jobs()

        # GATE 2: Audio duration validation
        tts_worker = OpenAITTSWorker()
        tts_worker._validate_audio_duration(total_duration=10.0)  # Pass

        # GATE 3: Screenshot validation (basic)
        screenshot_worker = ScreenshotCapturerWorker()
        # Note: Can't fully test without real Playwright, but framework is in place

        # GATE 4: Video validation
        video_worker = VideoAssemblerWorker()

        with tempfile.TemporaryDirectory() as tmpdir:
            video_file = os.path.join(tmpdir, "valid_output.mp4")

            # Create valid video file
            with open(video_file, "wb") as f:
                f.write(b"MP4" + b"\x00" * 150000)

            # Should pass all checks
            video_worker._validate_video_quality(
                output_path=video_file,
                bitrate_kbps=2500,
                duration_seconds=60,
                codec="h264"
            )


class TestGateAuditTrail:
    """Test that gates emit audit events for tracking"""

    def test_gate1_emits_audit_event_on_pass(self):
        """Gate 1 should emit audit event when validation passes"""
        orchestrator = MaestroOrchestrator()

        job_id = orchestrator.create_job(
            topic="Audit Test",
            duration=60,
            audience="technical",
            narration=["This is valid narration for testing audit trails."]
        )

        audit_log = orchestrator.get_audit_log()
        validation_events = [e for e in audit_log if e["event_type"] == "content_presence_validated"]

        assert len(validation_events) > 0
        assert validation_events[0]["details"]["status"] == "passed"
        assert validation_events[0]["details"]["num_scenes"] == 1



if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
