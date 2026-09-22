"""End-to-end tests for media system integration"""

import pytest
import tempfile
import json
from pathlib import Path
from io import BytesIO

# Note: These tests are designed to run with pytest
# They test the media manager and bridges in realistic scenarios


class TestMediaSystemE2E:
    """End-to-end tests for media system"""

    @pytest.fixture
    def temp_media_dir(self):
        """Create temporary media directory"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def media_manager(self, temp_media_dir):
        """Create media manager instance"""
        from core.media.manager import MediaManager
        return MediaManager(root=temp_media_dir)

    def test_upload_video_file(self, media_manager, tmp_path):
        """Test uploading a video file"""
        # Create a fake video file
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"fake video content" * 1000)  # ~18KB

        # Upload
        media = media_manager.upload(
            file_path=str(video_file),
            tags=["test", "video"],
            description="Test video",
            is_public=True
        )

        # Verify
        assert media.media_id.startswith("media_")
        assert media.original_name == "test_video.mp4"
        assert media.mime_type == "video/mp4"
        assert media.is_public is True
        assert "test" in media.tags
        assert "video" in media.tags
        assert media.size_mb() > 0

    def test_upload_image_file(self, media_manager, tmp_path):
        """Test uploading an image file"""
        # Create a fake image file
        image_file = tmp_path / "test_image.jpg"
        image_file.write_bytes(b"\xff\xd8\xff" + b"fake jpeg" * 100)  # Fake JPEG header

        # Upload
        media = media_manager.upload(
            file_path=str(image_file),
            tags=["image", "test"],
            description="Test image"
        )

        # Verify
        assert media.mime_type == "image/jpeg"
        assert media.is_image() is True
        assert media.is_video() is False

    def test_list_media_with_filters(self, media_manager, tmp_path):
        """Test listing media with tag filters"""
        # Upload multiple files
        file1 = tmp_path / "video1.mp4"
        file1.write_bytes(b"fake video" * 100)
        media1 = media_manager.upload(file_path=str(file1), tags=["demo"])

        file2 = tmp_path / "video2.mp4"
        file2.write_bytes(b"fake video" * 100)
        media2 = media_manager.upload(file_path=str(file2), tags=["test"])

        # List all
        all_media = media_manager.list()
        assert len(all_media) == 2

        # Filter by tag
        demo_media = media_manager.list(tags=["demo"])
        assert len(demo_media) == 1
        assert demo_media[0].media_id == media1.media_id

    def test_delete_media(self, media_manager, tmp_path):
        """Test deleting a media file"""
        # Upload
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"fake video" * 100)
        media = media_manager.upload(file_path=str(video_file))

        # Delete
        media_manager.delete(media.media_id)

        # Verify deleted
        assert media_manager.get(media.media_id) is None

    def test_media_hash_calculation(self, media_manager, tmp_path):
        """Test SHA256 hash calculation"""
        # Upload
        video_file = tmp_path / "test_video.mp4"
        content = b"fixed content"
        video_file.write_bytes(content * 100)
        media = media_manager.upload(file_path=str(video_file))

        # Verify hash
        assert len(media.hash_sha256) == 64  # SHA256 hex is 64 chars
        assert media.hash_sha256.isalnum()

    def test_manifest_persistence(self, temp_media_dir, tmp_path):
        """Test manifest is persisted to disk"""
        from core.media.manager import MediaManager

        # Create manager and upload file
        manager1 = MediaManager(root=temp_media_dir)
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"fake video" * 100)
        media = manager1.upload(file_path=str(video_file), tags=["test"])

        # Create new manager (should load manifest from disk)
        manager2 = MediaManager(root=temp_media_dir)
        loaded_media = manager2.get(media.media_id)

        # Verify
        assert loaded_media is not None
        assert loaded_media.media_id == media.media_id
        assert loaded_media.original_name == media.original_name

    def test_discord_bridge_validation(self):
        """Test Discord bridge validates files"""
        from core.bridges.discord import DiscordBridge
        from core.media.models import MediaFile

        bridge = DiscordBridge()

        # Create large file (over Discord 25MB limit)
        large_media = MediaFile(
            media_id="test",
            original_name="large.mp4",
            file_path="/nonexistent/path",
            file_size_bytes=30 * 1024 * 1024,  # 30MB
            mime_type="video/mp4"
        )

        # Should fail due to size
        result = bridge.send(large_media, {"channel_id": "123"})
        assert result["status"] == "failed"
        assert "too large" in result["error"].lower()

    def test_console_bridge_always_available(self):
        """Test console bridge is always available"""
        from core.bridges.console import ConsoleBridge

        bridge = ConsoleBridge()
        assert bridge.can_send() is True

    def test_bridge_media_tracking(self, media_manager, tmp_path):
        """Test tracking media across bridges"""
        # Upload
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"fake video" * 100)
        media = media_manager.upload(file_path=str(video_file))

        # Track on Discord
        media_manager.update_bridge_id(media.media_id, "discord", "msg_12345")

        # Verify
        updated = media_manager.get(media.media_id)
        assert updated.bridge_ids.get("discord") == "msg_12345"


class TestMediaRoutes:
    """Test media console routes (requires FastAPI)"""

    @pytest.fixture
    def client(self):
        """Create test client"""
        try:
            from fastapi.testclient import TestClient
            from core.console.routes.media_routes import create_media_router
            from fastapi import FastAPI

            app = FastAPI()
            router = create_media_router()
            if router:
                app.include_router(router)
            return TestClient(app)
        except ImportError:
            pytest.skip("FastAPI not available")

    def test_list_media_endpoint(self, client):
        """Test GET /v1/console/media/list"""
        response = client.get("/v1/console/media/list")
        assert response.status_code == 200
        data = response.json()
        assert "total" in data
        assert "media" in data

    def test_upload_media_endpoint(self, client, tmp_path):
        """Test POST /v1/console/media/upload"""
        # Create a test file
        test_file = tmp_path / "test.mp4"
        test_file.write_bytes(b"fake video content" * 100)

        # Upload via HTTP
        with open(test_file, "rb") as f:
            response = client.post(
                "/v1/console/media/upload",
                files={"file": ("test.mp4", f, "video/mp4")},
                data={
                    "tags": ["test"],
                    "description": "Test video",
                    "is_public": "false"
                }
            )

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "uploaded"
        assert "media" in data
        assert "media_id" in data["media"]

    def test_stream_media_endpoint_404(self, client):
        """Test GET /v1/console/media/{media_id} with nonexistent media"""
        response = client.get("/v1/console/media/nonexistent")
        assert response.status_code == 404

    def test_metadata_endpoint_404(self, client):
        """Test GET /v1/console/media/{media_id}/metadata with nonexistent media"""
        response = client.get("/v1/console/media/nonexistent/metadata")
        assert response.status_code == 404

    def test_send_to_bridge_endpoint_404(self, client):
        """Test POST /v1/console/media/send-to-bridge with nonexistent media"""
        response = client.post(
            "/v1/console/media/send-to-bridge",
            json={
                "media_id": "nonexistent",
                "bridge": "discord",
                "config": {"channel_id": "123"}
            }
        )
        assert response.status_code == 404

    def test_delete_media_endpoint_404(self, client):
        """Test DELETE /v1/console/media/{media_id} with nonexistent media"""
        response = client.delete("/v1/console/media/nonexistent")
        assert response.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
