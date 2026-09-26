"""Phase 6b: Video Producer Worker Integration E2E Tests

15+ tests covering all 4 workers (TTS, Screenshot, FFmpeg, YouTube).
Ref: ADR-0206 Phase 6 Milestone 2

Goal: 75+ tests total (Phase 6a: 10 + Phase 6b: 15+ = 25+ new)
"""
import pytest
from core.skills.video_producer.workers import (
    TTSWorker, ScreenshotWorker, FFmpegWorker, YouTubeWorker,
    WorkerResult, get_worker, WORKERS
)


class TestTTSWorker:
    """TTS Worker Tests (Google Cloud)"""

    @pytest.fixture
    def tts_worker(self):
        return TTSWorker()

    def test_tts_worker_init(self, tts_worker):
        """Test TTS worker initialization."""
        assert tts_worker.worker_id == "tts"
        assert tts_worker.execution_count == 0
        assert tts_worker.last_result is None

    def test_tts_worker_execute_success(self, tts_worker):
        """Test successful TTS synthesis."""
        result = tts_worker.execute({
            "text": "Hello, world",
            "voice": "en-US-Neural2-C",
            "output_format": "mp3",
            "frame_id": "frame-001"
        })

        assert result.status == "completed"
        assert result.worker_id == "tts"
        assert result.frame_id == "frame-001"
        assert result.output is not None
        assert "audio_path" in result.output
        assert result.output["voice"] == "en-US-Neural2-C"
        assert result.execution_count == 1

    def test_tts_worker_missing_text(self, tts_worker):
        """Test TTS with missing text input."""
        result = tts_worker.execute({
            "text": "",
            "voice": "en-US-Neural2-C",
            "frame_id": "frame-bad"
        })

        assert result.status == "failed"
        assert "Missing text input" in result.error

    def test_tts_worker_health_check(self, tts_worker):
        """Test TTS worker health check."""
        health = tts_worker.health_check()

        assert health["worker_id"] == "tts"
        assert health["status"] == "healthy"
        assert health["execution_count"] == 0

    def test_tts_worker_multiple_executions(self, tts_worker):
        """Test multiple TTS executions."""
        for i in range(3):
            result = tts_worker.execute({
                "text": f"Text {i}",
                "frame_id": f"frame-{i:03d}"
            })
            assert result.status == "completed"

        assert tts_worker.execution_count == 3
        assert tts_worker.last_result.frame_id == "frame-002"


class TestScreenshotWorker:
    """Screenshot Worker Tests"""

    @pytest.fixture
    def screenshot_worker(self):
        return ScreenshotWorker()

    def test_screenshot_worker_init(self, screenshot_worker):
        """Test screenshot worker initialization."""
        assert screenshot_worker.worker_id == "screenshot"
        assert screenshot_worker.execution_count == 0

    def test_screenshot_worker_execute_success(self, screenshot_worker):
        """Test successful screenshot capture."""
        result = screenshot_worker.execute({
            "url": "https://example.com",
            "format": "png",
            "width": 1920,
            "height": 1080,
            "frame_id": "frame-001"
        })

        assert result.status == "completed"
        assert result.worker_id == "screenshot"
        assert result.output is not None
        assert "image_path" in result.output
        assert result.output["resolution"] == "1920x1080"
        assert result.output["format"] == "png"

    def test_screenshot_worker_url_variants(self, screenshot_worker):
        """Test screenshot with different URLs."""
        urls = ["https://google.com", "https://github.com", ""]

        for url in urls:
            result = screenshot_worker.execute({
                "url": url,
                "frame_id": "frame-test"
            })
            assert result.status == "completed"

    def test_screenshot_worker_formats(self, screenshot_worker):
        """Test screenshot with different formats."""
        for fmt in ["png", "jpg"]:
            result = screenshot_worker.execute({
                "url": "test",
                "format": fmt,
                "frame_id": f"frame-{fmt}"
            })
            assert result.status == "completed"
            assert result.output["format"] == fmt


class TestFFmpegWorker:
    """FFmpeg Worker Tests"""

    @pytest.fixture
    def ffmpeg_worker(self):
        return FFmpegWorker()

    def test_ffmpeg_worker_init(self, ffmpeg_worker):
        """Test FFmpeg worker initialization."""
        assert ffmpeg_worker.worker_id == "ffmpeg"

    def test_ffmpeg_worker_execute_success(self, ffmpeg_worker):
        """Test successful FFmpeg encoding."""
        result = ffmpeg_worker.execute({
            "input_paths": ["audio.mp3", "video.mp4"],
            "output_path": "/tmp/output.mp4",
            "codec": "h264",
            "bitrate": "2M",
            "frame_id": "frame-001"
        })

        assert result.status == "completed"
        assert result.worker_id == "ffmpeg"
        assert result.output["codec"] == "h264"
        assert result.output["bitrate"] == "2M"
        assert result.output["input_count"] == 2

    def test_ffmpeg_worker_missing_inputs(self, ffmpeg_worker):
        """Test FFmpeg with missing input paths."""
        result = ffmpeg_worker.execute({
            "input_paths": [],
            "frame_id": "frame-bad"
        })

        assert result.status == "failed"
        assert "Missing input paths" in result.error

    def test_ffmpeg_worker_codecs(self, ffmpeg_worker):
        """Test FFmpeg with different codecs."""
        for codec in ["h264", "h265", "vp9"]:
            result = ffmpeg_worker.execute({
                "input_paths": ["test.mp4"],
                "codec": codec,
                "frame_id": f"frame-{codec}"
            })
            assert result.status == "completed"
            assert result.output["codec"] == codec


class TestYouTubeWorker:
    """YouTube Worker Tests"""

    @pytest.fixture
    def youtube_worker(self):
        return YouTubeWorker()

    def test_youtube_worker_init(self, youtube_worker):
        """Test YouTube worker initialization."""
        assert youtube_worker.worker_id == "youtube"

    def test_youtube_worker_execute_success(self, youtube_worker):
        """Test successful YouTube upload."""
        result = youtube_worker.execute({
            "video_path": "/tmp/final.mp4",
            "title": "Test Video",
            "description": "A test video",
            "visibility": "private",
            "frame_id": "frame-001"
        })

        assert result.status == "completed"
        assert result.worker_id == "youtube"
        assert "video_id" in result.output
        assert result.output["title"] == "Test Video"
        assert result.output["visibility"] == "private"
        assert "youtube.com" in result.output["url"]

    def test_youtube_worker_missing_video_path(self, youtube_worker):
        """Test YouTube with missing video path."""
        result = youtube_worker.execute({
            "video_path": "",
            "title": "Test",
            "frame_id": "frame-bad"
        })

        assert result.status == "failed"
        assert "Missing video path" in result.error

    def test_youtube_worker_visibility_modes(self, youtube_worker):
        """Test YouTube with different visibility modes."""
        for visibility in ["private", "unlisted", "public"]:
            result = youtube_worker.execute({
                "video_path": "/tmp/test.mp4",
                "title": f"Test {visibility}",
                "visibility": visibility,
                "frame_id": f"frame-{visibility}"
            })
            assert result.status == "completed"
            assert result.output["visibility"] == visibility


class TestWorkerRegistry:
    """Worker Registry Tests"""

    def test_worker_registry_has_all_types(self):
        """Test that registry has all 4 worker types."""
        assert "tts" in WORKERS
        assert "screenshot" in WORKERS
        assert "ffmpeg" in WORKERS
        assert "youtube" in WORKERS
        assert len(WORKERS) == 4

    def test_get_worker_success(self):
        """Test get_worker function for all types."""
        for worker_type in ["tts", "screenshot", "ffmpeg", "youtube"]:
            worker = get_worker(worker_type)
            assert worker.worker_id == worker_type

    def test_get_worker_invalid_type(self):
        """Test get_worker with invalid type."""
        with pytest.raises(ValueError, match="Unknown worker type"):
            get_worker("invalid_worker")


class TestWorkerResult:
    """WorkerResult Tests"""

    def test_worker_result_immutable(self):
        """Test that WorkerResult is immutable."""
        result = WorkerResult(
            worker_id="tts",
            frame_id="frame-001",
            status="completed",
            output={"path": "test.mp3"}
        )

        # Should raise error on modification
        with pytest.raises((AttributeError, TypeError)):
            result.status = "failed"

    def test_worker_result_timestamp(self):
        """Test that WorkerResult has timestamp."""
        result = WorkerResult(
            worker_id="tts",
            frame_id="frame-001",
            status="completed"
        )

        assert result.created_at is not None
        assert "T" in result.created_at  # ISO format

    def test_worker_result_error_cases(self):
        """Test WorkerResult error handling."""
        result = WorkerResult(
            worker_id="tts",
            frame_id="frame-001",
            status="failed",
            error="Connection timeout"
        )

        assert result.status == "failed"
        assert result.error == "Connection timeout"
        assert result.output is None


class TestWorkerIntegration:
    """Integration Tests (Multiple Workers)"""

    def test_orchestrator_with_workers(self):
        """Test orchestrator integration with real workers."""
        from core.skills.video_producer.orchestrator import (
            VideoOrchestrator, StoryboardFrame
        )

        orchestrator = VideoOrchestrator("integration-test")

        # Create storyboard with all worker types
        frames = [
            StoryboardFrame(
                frame_id="frame-tts",
                timestamp=0.0,
                description="TTS frame",
                worker_type="tts",
                worker_input={"text": "Hello"},
                created_at="2026-09-26T00:00:00"
            ),
            StoryboardFrame(
                frame_id="frame-ss",
                timestamp=2.0,
                description="Screenshot frame",
                worker_type="screenshot",
                worker_input={"url": "https://example.com"},
                created_at="2026-09-26T00:00:00"
            ),
        ]

        for frame in frames:
            orchestrator.add_frame(frame)

        commands = orchestrator.build_execution_plan()
        assert len(commands) == 2
        assert commands[0].frame.worker_type == "tts"
        assert commands[1].frame.worker_type == "screenshot"

    def test_worker_execution_latency(self):
        """Test worker execution latency is reasonable."""
        workers = [TTSWorker(), ScreenshotWorker(), FFmpegWorker(), YouTubeWorker()]

        for worker in workers:
            result = worker.execute({
                "text": "test" if worker.worker_id == "tts" else "",
                "url": "test" if worker.worker_id == "screenshot" else "",
                "input_paths": ["test"] if worker.worker_id == "ffmpeg" else [],
                "video_path": "test.mp4" if worker.worker_id == "youtube" else "",
                "frame_id": f"frame-{worker.worker_id}"
            })

            # Latency should be < 100ms for stubs
            assert result.duration_ms < 100, f"{worker.worker_id} took too long"


# Phase 6b Test Summary
# ====================
# ✅ TTS Worker (5 tests)
# ✅ Screenshot Worker (4 tests)
# ✅ FFmpeg Worker (4 tests)
# ✅ YouTube Worker (4 tests)
# ✅ Worker Registry (3 tests)
# ✅ WorkerResult (3 tests)
# ✅ Integration (2 tests)
#
# Total: 25 new tests for Phase 6b
# Expected total (Phase 6a + 6b): 10 + 25 = 35+ new tests
# Combined with Phase 5 baseline (55+): 55 + 35 = 90+ tests expected
# Goal: 75+ ✅
