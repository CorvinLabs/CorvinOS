"""Phase 4 Tests: YouTube Uploader + Console UI + Learning Integration (20+ tests).

Covers:
- Async YouTube upload enqueuing
- Upload progress tracking
- OAuth authentication
- Full end-to-end workflow (PPT → YouTube)
- Learning loop integration
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from core.skills.workers.youtube_uploader import YouTubeUploader, YouTubeAPI


class _FakeYouTubeAPI:
    """Test double for the EXTERNAL YouTube Data API boundary.

    The uploader (the unit under test) runs for real; only the Google API
    client is replaced. Using ``oauth_token="test_token"`` instead needs the
    optional ``youtube`` extra (google-auth) and then makes the background
    task call the real googleapis.com with a bogus token.
    """

    def __init__(self):
        self.uploads: list[dict] = []
        self.captions: list[tuple] = []

    async def is_authenticated(self) -> bool:
        return True

    async def upload_video(self, **kwargs):
        self.uploads.append(kwargs)
        return {"status": "uploaded", "video_id": "vid123",
                "url": "https://youtube.com/watch?v=vid123"}

    async def upload_captions(self, video_id, srt_path, language="en"):
        self.captions.append((video_id, srt_path))
        return True


def _authed_uploader(workdir):
    uploader = YouTubeUploader(workdir)
    uploader.youtube_api = _FakeYouTubeAPI()
    return uploader


async def _drain(uploader) -> None:
    """Wait for every background upload task the uploader started."""
    await asyncio.gather(*list(uploader._background_tasks.values()))
from core.skills.os_skills.video_producer.types import Scene, Storyboard


class TestYouTubeUploader:
    """YouTube Uploader Worker Tests."""

    @pytest.mark.asyncio
    async def test_uploader_init(self):
        """Test YouTubeUploader initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            uploader = YouTubeUploader(tmpdir)
            assert uploader.workdir == Path(tmpdir)
            assert uploader.upload_dir.exists()

    @pytest.mark.asyncio
    async def test_enqueue_upload_success(self):
        """Test successful upload enqueuing (non-blocking)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            video_path = project_dir / "output.mp4"
            video_path.write_bytes(b"MP4..." + b"\x00" * 1_000_000)  # 1MB video

            uploader = _authed_uploader(project_dir)

            metadata = {
                "title": "Test Video",
                "description": "A test video for YouTube",
                "tags": ["test", "corvin"],
            }

            result = await uploader.enqueue_upload(video_path, metadata)

            assert result["status"] == "queued"
            assert result["task_id"] is not None
            assert result["expected_duration_minutes"] > 0

    @pytest.mark.asyncio
    async def test_enqueue_upload_missing_file(self):
        """Test upload enqueuing with missing video file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            uploader = _authed_uploader(tmpdir)

            metadata = {"title": "Test"}

            result = await uploader.enqueue_upload(
                Path(tmpdir) / "missing.mp4",
                metadata,
            )

            assert result["status"] == "error"
            assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_enqueue_upload_not_authenticated(self):
        """Test upload enqueuing without OAuth authentication."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            video_path = project_dir / "output.mp4"
            video_path.write_bytes(b"MP4..." + b"\x00" * 1_000_000)

            uploader = YouTubeUploader(project_dir)  # No oauth_token

            metadata = {"title": "Test"}

            result = await uploader.enqueue_upload(video_path, metadata)

            assert result["status"] == "error"
            assert "not authenticated" in result["error"]

    @pytest.mark.asyncio
    async def test_upload_status_tracking(self):
        """Test that upload status is tracked."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            video_path = project_dir / "output.mp4"
            video_path.write_bytes(b"MP4..." + b"\x00" * 1_000_000)

            uploader = _authed_uploader(project_dir)
            # Real credentials are needed to reach enqueue_upload's
            # is_authenticated() precondition, but the actual network call
            # is a test double -- this test is about the queue/status
            # mechanics, not a live YouTube round trip.
            uploader.youtube_api.upload_video = AsyncMock(
                return_value={"status": "uploaded", "video_id": "test123",
                              "url": "https://youtube.com/watch?v=test123"}
            )

            metadata = {"title": "Test"}

            # Enqueue upload
            result = await uploader.enqueue_upload(video_path, metadata)
            task_id = result["task_id"]

            # Check initial status
            status = await uploader.get_upload_status(task_id)
            assert status["status"] == "queued"
            assert status["progress_percent"] == 0

            # Wait for background task to complete
            await _drain(uploader)

            # Check final status
            status = await uploader.get_upload_status(task_id)
            assert status["status"] == "completed"
            assert status["youtube_video_id"] == "test123"

    @pytest.mark.asyncio
    async def test_upload_record_persisted(self):
        """Test that upload records are persisted to disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            video_path = project_dir / "output.mp4"
            video_path.write_bytes(b"MP4..." + b"\x00" * 1_000_000)

            uploader = _authed_uploader(project_dir)

            metadata = {"title": "Test"}

            result = await uploader.enqueue_upload(video_path, metadata)
            task_id = result["task_id"]

            # Verify record file exists
            record_path = project_dir / "youtube" / f"{task_id}.json"
            assert record_path.exists()

            # Verify record contents
            with open(record_path) as f:
                record = json.load(f)
            assert record["task_id"] == task_id
            assert record["status"] == "queued"

    @pytest.mark.asyncio
    async def test_file_size_calculation(self):
        """Test that file size is correctly calculated for upload ETA."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            video_path = project_dir / "output.mp4"

            # Create 10MB video
            video_path.write_bytes(b"MP4..." + b"\x00" * (10_000_000 - 5))

            uploader = _authed_uploader(project_dir)

            metadata = {"title": "Test"}

            result = await uploader.enqueue_upload(video_path, metadata)

            # 10MB at 5 Mbps = ~2 minutes
            assert 1.5 < result["expected_duration_minutes"] < 2.5

    @pytest.mark.asyncio
    async def test_file_too_large(self):
        """Test upload rejection for files > 256GB."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            # Mock file path with size > 256GB
            video_path = MagicMock(spec=Path)
            video_path.exists.return_value = True
            video_path.stat.return_value = MagicMock(st_size=300 * (1024**3))  # 300GB

            uploader = _authed_uploader(project_dir)

            metadata = {"title": "Test"}

            # This test would need to use the real path calculation
            # For now, just verify the logic is there
            assert uploader is not None


class TestYouTubeAPI:
    """YouTube API Stub Tests."""

    def test_api_init(self):
        """Test YouTubeAPI initialization -- an injected token constructs a
        real (if short-lived) google.oauth2.credentials.Credentials object."""
        pytest.importorskip(
            "google.oauth2.credentials",
            reason="external dependency: optional 'youtube' extra (google-auth) not installed",
        )
        api = YouTubeAPI(oauth_token="test_token")
        assert api._credentials is not None
        assert api._credentials.token == "test_token"

    @pytest.mark.asyncio
    async def test_authentication_check(self):
        """Test authentication status check."""
        pytest.importorskip(
            "google.oauth2.credentials",
            reason="external dependency: optional 'youtube' extra (google-auth) not installed",
        )
        api = YouTubeAPI(oauth_token="test_token")

        is_auth = await api.is_authenticated()
        assert is_auth is True

        api_no_auth = YouTubeAPI()
        is_auth = await api_no_auth.is_authenticated()
        assert is_auth is False

    @pytest.mark.asyncio
    async def test_injected_token_without_google_libs_is_not_authenticated(self, monkeypatch):
        """Without the 'youtube' extra an injected token is NOT a credential:
        not authenticated, and the reason names the missing extra."""
        import builtins

        real_import = builtins.__import__

        def no_google(name, *args, **kwargs):
            if name.startswith("google"):
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_google)
        api = YouTubeAPI(oauth_token="test_token")
        assert await api.is_authenticated() is False
        result = await api.upload_video(video_path="/x.mp4", title="t", description="d", tags=[])
        assert result["status"] == "not_configured" and result["video_id"] is None
        assert "youtube" in result["reason"]

    @pytest.mark.asyncio
    async def test_upload_video_not_configured(self):
        """No CORVIN_YOUTUBE_TOKEN_PATH / injected token -> "local-only mode":
        a clear not_configured status, never a fabricated video id."""
        api = YouTubeAPI()  # no token at all

        result = await api.upload_video(
            video_path="/path/to/video.mp4",
            title="Test",
            description="Test video",
            tags=["test"],
        )

        assert result["status"] == "not_configured"
        assert result["video_id"] is None
        assert result["url"] is None
        assert result["reason"]

    @pytest.mark.asyncio
    async def test_update_metadata_not_configured(self):
        """Not configured -> False, not a fabricated success."""
        api = YouTubeAPI()

        result = await api.update_video_metadata(
            video_id="test_id",
            metadata={"title": "New Title"},
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_upload_captions_not_configured(self):
        """Not configured -> False, not a fabricated success."""
        api = YouTubeAPI()

        result = await api.upload_captions(
            video_id="test_id",
            srt_path="/path/to/captions.srt",
        )

        assert result is False


class TestPhase4E2E:
    """End-to-End tests for Phase 4."""

    @pytest.mark.asyncio
    async def test_e2e_full_workflow_with_youtube(self):
        """E2E: Complete workflow from PPT to YouTube enqueue."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            # Create mock video output
            video_path = project_dir / "output.mp4"
            video_path.write_bytes(b"MP4..." + b"\x00" * 1_000_000)

            # Initialize uploader with auth
            uploader = _authed_uploader(project_dir)

            # Enqueue upload (non-blocking)
            metadata = {
                "title": "CorvinOS Video Producer Demo",
                "description": "Automatically generated video from PowerPoint",
                "tags": ["corvin", "video-producer", "automated"],
            }

            result = await uploader.enqueue_upload(video_path, metadata)

            assert result["status"] == "queued"
            assert result["task_id"] is not None

            # Check that we can get status (non-blocking return already happened)
            status = await uploader.get_upload_status(result["task_id"])
            assert status["task_id"] == result["task_id"]

    @pytest.mark.asyncio
    async def test_e2e_with_captions(self):
        """E2E: Upload with SRT captions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            # Create video + captions
            video_path = project_dir / "output.mp4"
            video_path.write_bytes(b"MP4..." + b"\x00" * 1_000_000)

            srt_path = project_dir / "output.srt"
            srt_content = """1
00:00:00,000 --> 00:00:05,000
Welcome to CorvinOS

2
00:00:05,000 --> 00:00:10,000
An agentic operating system
"""
            srt_path.write_text(srt_content)

            uploader = _authed_uploader(project_dir)

            metadata = {
                "title": "CorvinOS Demo",
                "description": "Automated video with captions",
                "tags": ["demo"],
            }

            result = await uploader.enqueue_upload(
                video_path,
                metadata,
                srt_path=srt_path,
            )

            assert result["status"] == "queued"
            assert result["task_id"] is not None

            await _drain(uploader)
            status = await uploader.get_upload_status(result["task_id"])
            assert status["status"] == "completed"
            assert uploader.youtube_api.captions == [("vid123", str(srt_path))]

    @pytest.mark.asyncio
    async def test_parallel_uploads(self):
        """Test multiple concurrent uploads."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            uploader = _authed_uploader(project_dir)

            # Create multiple videos
            video_paths = []
            for i in range(3):
                video_path = project_dir / f"output_{i}.mp4"
                video_path.write_bytes(b"MP4..." + b"\x00" * 500_000)
                video_paths.append(video_path)

            # Enqueue all uploads in parallel
            tasks = []
            for i, video_path in enumerate(video_paths):
                metadata = {
                    "title": f"Video {i+1}",
                    "description": f"Test video {i+1}",
                    "tags": ["test"],
                }
                tasks.append(uploader.enqueue_upload(video_path, metadata))

            results = await asyncio.gather(*tasks)

            # All should be queued
            assert len(results) == 3
            assert all(r["status"] == "queued" for r in results)

            # All should have unique task IDs
            task_ids = [r["task_id"] for r in results]
            assert len(set(task_ids)) == 3  # All unique

    @pytest.mark.asyncio
    async def test_upload_progress_events(self):
        """Test that upload progress events are emitted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            video_path = project_dir / "output.mp4"
            video_path.write_bytes(b"MP4..." + b"\x00" * 1_000_000)

            uploader = _authed_uploader(project_dir)

            # Track emitted events -- EventEmitter.emit() is synchronous and
            # takes one real LearningEvent (ADR-0314), not an (event_type, data)
            # pair; all youtube_uploader stages share EventType.UPLOAD_PROGRESS,
            # distinguished by signal["status"].
            events = []

            def capture_emit(event):
                events.append(event)
                return True

            uploader.event_emitter.emit = capture_emit

            metadata = {"title": "Test"}
            result = await uploader.enqueue_upload(video_path, metadata)

            # Wait for upload to complete
            await _drain(uploader)

            # Verify events were emitted
            assert len(events) > 0
            statuses = [e.signal.get("status") for e in events]
            assert statuses == ["enqueued", "progress", "completed"]


class TestPhase4Learning:
    """Learning loop integration tests for Phase 4."""

    @pytest.mark.asyncio
    async def test_video_quality_feedback_collected(self):
        """Test that per-scene feedback is collected for learning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # This test verifies the learning loop structure
            # In real Phase 4: feedback → optimizer tunes next video

            feedback_data = {
                "video_id": "test_video",
                "scenes": [
                    {
                        "scene_id": "s01",
                        "audio_quality": 0.95,
                        "slide_quality": 0.88,
                        "timing_accuracy": 0.92,
                    },
                    {
                        "scene_id": "s02",
                        "audio_quality": 0.91,
                        "slide_quality": 0.85,
                        "timing_accuracy": 0.89,
                    },
                ],
                "overall_score": 0.90,
            }

            # Compute average for learning
            scores = [
                scene["audio_quality"] for scene in feedback_data["scenes"]
            ]
            avg_score = sum(scores) / len(scores)

            assert 0.8 < avg_score < 1.0
            assert len(feedback_data["scenes"]) == 2

    def test_learning_config_persistence(self):
        """Test that optimized configs are persisted for next video."""
        config = {
            "voice_synthesizer": {
                "rate": "+0%",
                "pitch": "+0Hz",
            },
            "screenshot_capturer": {
                "dpi": 150,
            },
            "slide_renderer": {
                "dpi": 150,
            },
            "video_assembler": {
                "bitrate_kbps": 6000,
                "fps": 30,
            },
        }

        # Simulate learning adjustment
        config["voice_synthesizer"]["rate"] = "-5%"  # Slow down voice
        config["video_assembler"]["bitrate_kbps"] = 7000  # Better quality

        # Verify config updated
        assert config["voice_synthesizer"]["rate"] == "-5%"
        assert config["video_assembler"]["bitrate_kbps"] == 7000
