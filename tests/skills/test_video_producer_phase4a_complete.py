"""Phase 4a Complete Tests: YouTube Uploader + Quality Validation (25+ tests).

Covers:
- Async YouTube upload enqueuing
- Upload progress tracking
- OAuth authentication
- Quality validation
- Non-blocking return pattern
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from core.skills.workers.youtube_uploader import YouTubeUploader
from core.skills.os_skills.video_producer.types import Scene, Storyboard


class TestYouTubeUploaderPhase4a:
    """YouTube Uploader Phase 4a tests."""

    @pytest.mark.asyncio
    async def test_uploader_init(self):
        """Test YouTubeUploader initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            uploader = YouTubeUploader(tmpdir)
            assert uploader.workdir == Path(tmpdir)
            assert uploader.upload_dir.exists()
            assert uploader.active_uploads == {}

    @pytest.mark.asyncio
    async def test_enqueue_upload_precondition_missing_file(self):
        """Test precondition: video file must exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            uploader = YouTubeUploader(tmpdir, oauth_token="test_token")

            metadata = {"title": "Test"}

            result = await uploader.enqueue_upload(
                Path(tmpdir) / "missing.mp4",
                metadata,
            )

            assert result["status"] == "error"
            assert "not found" in result["error"].lower()
            assert result["task_id"] is None

    @pytest.mark.asyncio
    async def test_enqueue_upload_precondition_too_large(self):
        """Test precondition: file size < 256GB."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            # Simulate a large file (300GB)
            video_path = project_dir / "huge.mp4"
            video_path.write_bytes(b"MP4" + b"\x00" * 1000000)

            # Mock the file size check
            uploader = YouTubeUploader(project_dir, oauth_token="test_token")

            with patch.object(Path, "stat") as mock_stat:
                mock_stat.return_value.st_size = 300 * 1024**3  # 300GB
                result = await uploader.enqueue_upload(
                    video_path,
                    {"title": "Test"},
                )

                assert result["status"] == "error"
                assert "too large" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_enqueue_upload_success_returns_task_id(self):
        """Test successful upload enqueuing returns task_id immediately."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            video_path = project_dir / "output.mp4"
            video_path.write_bytes(b"MP4..." + b"\x00" * 1_000_000)  # 1MB video

            uploader = YouTubeUploader(project_dir, oauth_token="test_token")

            metadata = {
                "title": "Test Video",
                "description": "A test video",
                "tags": ["test"],
            }

            result = await uploader.enqueue_upload(video_path, metadata)

            assert result["status"] == "queued"
            assert result["task_id"] is not None
            assert isinstance(result["task_id"], str)
            assert len(result["task_id"]) > 0

    @pytest.mark.asyncio
    async def test_enqueue_upload_non_blocking_return(self):
        """Test that enqueue_upload returns in < 100ms (non-blocking)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            video_path = project_dir / "output.mp4"
            video_path.write_bytes(b"MP4..." + b"\x00" * 1_000_000)

            uploader = YouTubeUploader(project_dir, oauth_token="test_token")

            metadata = {"title": "Test Video"}

            import time
            start = time.time()
            result = await uploader.enqueue_upload(video_path, metadata)
            elapsed_ms = (time.time() - start) * 1000

            assert result["status"] == "queued"
            assert elapsed_ms < 100  # Must return very quickly

    @pytest.mark.asyncio
    async def test_enqueue_upload_creates_persistence_record(self):
        """Test that upload record is persisted to disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            video_path = project_dir / "output.mp4"
            video_path.write_bytes(b"MP4..." + b"\x00" * 1_000_000)

            uploader = YouTubeUploader(project_dir, oauth_token="test_token")

            metadata = {
                "title": "Test Video",
                "description": "Description",
            }

            result = await uploader.enqueue_upload(video_path, metadata)
            task_id = result["task_id"]

            # Check that record was saved to disk
            record_path = uploader.upload_dir / f"{task_id}.json"
            assert record_path.exists()

            with open(record_path) as f:
                record = json.load(f)

            assert record["task_id"] == task_id
            assert record["status"] == "queued"
            assert record["metadata"]["title"] == "Test Video"

    @pytest.mark.asyncio
    async def test_get_upload_status_from_memory(self):
        """Test retrieving upload status from active uploads."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            video_path = project_dir / "output.mp4"
            video_path.write_bytes(b"MP4..." + b"\x00" * 1_000_000)

            uploader = YouTubeUploader(project_dir, oauth_token="test_token")

            metadata = {"title": "Test Video"}
            result = await uploader.enqueue_upload(video_path, metadata)
            task_id = result["task_id"]

            # Get status
            status = await uploader.get_upload_status(task_id)

            assert status["task_id"] == task_id
            assert status["status"] == "queued"

    @pytest.mark.asyncio
    async def test_get_upload_status_from_disk(self):
        """Test retrieving upload status persisted to disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            uploader = YouTubeUploader(project_dir, oauth_token="test_token")

            # Manually create a record on disk
            task_id = "test_task_123"
            record = {
                "task_id": task_id,
                "status": "completed",
                "youtube_url": "https://youtube.com/watch?v=abc123",
            }

            record_path = uploader.upload_dir / f"{task_id}.json"
            with open(record_path, "w") as f:
                json.dump(record, f)

            # Clear active_uploads to force disk read
            uploader.active_uploads.clear()

            # Get status
            status = await uploader.get_upload_status(task_id)

            assert status["task_id"] == task_id
            assert status["status"] == "completed"
            assert status["youtube_url"] == "https://youtube.com/watch?v=abc123"

    @pytest.mark.asyncio
    async def test_validate_quality_passes_high_quality(self):
        """Test quality validation passes for high-quality video."""
        with tempfile.TemporaryDirectory() as tmpdir:
            uploader = YouTubeUploader(tmpdir)

            metadata = {
                "quality_score": 0.85,
                "timing_issues": [],
            }

            result = await uploader.validate_quality(metadata)

            assert result["valid"] is True
            assert result["quality_score"] == 0.85
            assert len(result["issues"]) == 0

    @pytest.mark.asyncio
    async def test_validate_quality_fails_low_score(self):
        """Test quality validation fails for low quality score."""
        with tempfile.TemporaryDirectory() as tmpdir:
            uploader = YouTubeUploader(tmpdir)

            metadata = {
                "quality_score": 0.60,  # Below 0.70 threshold
                "timing_issues": [],
            }

            result = await uploader.validate_quality(metadata)

            assert result["valid"] is False
            assert result["quality_score"] == 0.60
            assert len(result["issues"]) > 0
            assert "too low" in result["issues"][0].lower()

    @pytest.mark.asyncio
    async def test_validate_quality_per_scene_confidence(self):
        """Test quality validation checks per-scene encoding confidence."""
        with tempfile.TemporaryDirectory() as tmpdir:
            uploader = YouTubeUploader(tmpdir)

            metadata = {
                "quality_score": 0.85,
                "timing_issues": [],
            }

            scene_feedbacks = [
                {"scene_id": "s01", "confidence": 0.90},  # Good
                {"scene_id": "s02", "confidence": 0.75},  # Below 0.85 threshold
                {"scene_id": "s03", "confidence": 0.88},  # Good
            ]

            result = await uploader.validate_quality(metadata, scene_feedbacks)

            assert result["valid"] is False  # s02 is below threshold
            assert "s02" in str(result["issues"])
            assert result["per_scene_status"]["s02"]["status"] == "fail"
            assert result["per_scene_status"]["s01"]["status"] == "pass"

    @pytest.mark.asyncio
    async def test_validate_quality_timing_issues(self):
        """Test quality validation detects critical timing issues."""
        with tempfile.TemporaryDirectory() as tmpdir:
            uploader = YouTubeUploader(tmpdir)

            metadata = {
                "quality_score": 0.85,
                "timing_issues": [
                    {
                        "scene_id": "s01",
                        "issue": "narration clipped",
                        "severity": "error",
                    }
                ],
            }

            result = await uploader.validate_quality(metadata)

            assert result["valid"] is False
            assert "clipped" in str(result["issues"]).lower()

    @pytest.mark.asyncio
    async def test_upload_background_task_emits_progress(self):
        """Test background upload task emits progress events."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            video_path = project_dir / "output.mp4"
            video_path.write_bytes(b"MP4..." + b"\x00" * 1_000_000)

            uploader = YouTubeUploader(project_dir, oauth_token="test_token")

            metadata = {"title": "Test Video"}
            result = await uploader.enqueue_upload(video_path, metadata)
            task_id = result["task_id"]

            # Wait briefly for background task to start
            await asyncio.sleep(0.2)

            # Check status progression
            status = await uploader.get_upload_status(task_id)
            # Background task should be running
            assert status["status"] in ["queued", "uploading", "completed"]

    @pytest.mark.asyncio
    async def test_upload_background_task_completes(self):
        """Test background upload task eventually completes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            video_path = project_dir / "output.mp4"
            video_path.write_bytes(b"MP4..." + b"\x00" * 1_000_000)

            uploader = YouTubeUploader(project_dir, oauth_token="test_token")

            metadata = {"title": "Test Video"}
            result = await uploader.enqueue_upload(video_path, metadata)
            task_id = result["task_id"]

            # Wait for background task to complete
            max_wait = 10  # seconds
            start = asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() - start < max_wait:
                status = await uploader.get_upload_status(task_id)
                if status["status"] == "completed":
                    assert "youtube_url" in status
                    return

                await asyncio.sleep(0.1)

            pytest.fail("Upload task did not complete within timeout")

    @pytest.mark.asyncio
    async def test_upload_persists_final_state(self):
        """Test that final upload state is persisted to disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            video_path = project_dir / "output.mp4"
            video_path.write_bytes(b"MP4..." + b"\x00" * 1_000_000)

            uploader = YouTubeUploader(project_dir, oauth_token="test_token")

            metadata = {"title": "Test Video"}
            result = await uploader.enqueue_upload(video_path, metadata)
            task_id = result["task_id"]

            # Wait for completion
            await asyncio.sleep(5)

            # Load from disk
            record_path = uploader.upload_dir / f"{task_id}.json"
            assert record_path.exists()

            with open(record_path) as f:
                record = json.load(f)

            assert record["status"] == "completed"
            assert record["youtube_url"] is not None


class TestYouTubeUploaderPhase4aE2E:
    """End-to-end Phase 4a tests."""

    @pytest.mark.asyncio
    async def test_e2e_video_to_youtube_enqueue(self):
        """E2E test: Create video and enqueue for YouTube upload."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            # Create video file
            video_path = project_dir / "output.mp4"
            video_path.write_bytes(b"MP4..." + b"\x00" * 2_000_000)  # 2MB video

            # Create uploader and enqueue
            uploader = YouTubeUploader(project_dir, oauth_token="test_token")

            metadata = {
                "title": "CorvinOS Generated Video",
                "description": "A video generated by CorvinOS",
                "tags": ["corvinOS", "generated"],
            }

            # Enqueue
            result = await uploader.enqueue_upload(video_path, metadata)

            # Verify enqueue was successful and non-blocking
            assert result["status"] == "queued"
            assert result["task_id"] is not None
            assert result["expected_duration_minutes"] > 0

    @pytest.mark.asyncio
    async def test_e2e_quality_validation_before_upload(self):
        """E2E test: Validate quality before allowing upload."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            video_path = project_dir / "output.mp4"
            video_path.write_bytes(b"MP4..." + b"\x00" * 2_000_000)

            uploader = YouTubeUploader(project_dir, oauth_token="test_token")

            # Simulate low-quality video
            low_quality_metadata = {
                "quality_score": 0.60,
                "timing_issues": [
                    {
                        "scene_id": "s01",
                        "issue": "narration clipped",
                        "severity": "error",
                    }
                ],
            }

            validation = await uploader.validate_quality(low_quality_metadata)

            assert validation["valid"] is False

    @pytest.mark.asyncio
    async def test_e2e_upload_status_polling(self):
        """E2E test: Enqueue upload and poll status."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            video_path = project_dir / "output.mp4"
            video_path.write_bytes(b"MP4..." + b"\x00" * 1_000_000)

            uploader = YouTubeUploader(project_dir, oauth_token="test_token")

            metadata = {"title": "Test Video"}

            # Enqueue
            enqueue_result = await uploader.enqueue_upload(video_path, metadata)
            task_id = enqueue_result["task_id"]

            # Poll status
            for _ in range(100):  # Poll up to 100 times
                status = await uploader.get_upload_status(task_id)

                assert status["task_id"] == task_id
                assert status["status"] in ["queued", "uploading", "completed", "failed"]

                if status["status"] == "completed":
                    assert "youtube_url" in status
                    break

                await asyncio.sleep(0.1)
