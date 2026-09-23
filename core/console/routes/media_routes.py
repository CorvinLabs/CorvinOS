"""API routes for media management and distribution.

Endpoints:
  POST   /v1/console/media/upload              — Upload media file
  GET    /v1/console/media/{media_id}          — Stream media file
  GET    /v1/console/media/list                — List all media
  POST   /v1/console/media/send-to-bridge      — Send to Discord/other bridge
  GET    /v1/console/media/{media_id}/metadata — Get media metadata
  DELETE /v1/console/media/{media_id}          — Delete media file

Bridges supported: Discord, Console (display), Telegram (optional)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional
from io import BytesIO

try:
    from fastapi import APIRouter, HTTPException, UploadFile, File, Body, FileResponse
    from fastapi.responses import StreamingResponse
except ImportError:
    APIRouter = None

from core.media.manager import MediaManager
from core.media.models import MediaFile
from core.bridges.discord import DiscordBridge
from core.bridges.console import ConsoleBridge

log = logging.getLogger(__name__)


class MediaRouteManager:
    """Manages media operations for console API"""

    def __init__(self):
        """Initialize media manager and bridges"""
        self.media_manager = MediaManager()
        self.bridges = {
            "discord": DiscordBridge(),
            "console": ConsoleBridge(),
        }

    def upload_media(
        self,
        file: UploadFile,
        tags: list[str] = None,
        description: str = "",
        is_public: bool = False
    ) -> MediaFile:
        """Upload a media file

        Args:
            file: File to upload
            tags: Optional tags for categorization
            description: Optional description
            is_public: Make publicly accessible

        Returns:
            MediaFile with metadata
        """
        # Save upload to temp location
        temp_path = Path("/tmp") / file.filename
        try:
            with open(temp_path, "wb") as f:
                f.write(file.file.read())

            # Upload to media manager
            media = self.media_manager.upload(
                file_path=str(temp_path),
                tags=tags or [],
                description=description,
                is_public=is_public,
                uploaded_by="console"
            )

            return media
        finally:
            # Clean up temp file
            temp_path.unlink(missing_ok=True)

    def get_media(self, media_id: str) -> Optional[MediaFile]:
        """Get media by ID"""
        return self.media_manager.get(media_id)

    def list_media(self, tags: list[str] = None) -> list[MediaFile]:
        """List media files, optionally filtered by tags"""
        return self.media_manager.list(tags=tags)

    def delete_media(self, media_id: str) -> bool:
        """Delete media file

        Returns:
            True if deleted, False if not found
        """
        try:
            self.media_manager.delete(media_id)
            return True
        except ValueError:
            return False

    def send_to_bridge(
        self,
        media_id: str,
        bridge: str,
        config: dict[str, Any]
    ) -> dict[str, Any]:
        """Send media to a bridge (Discord, etc.)

        Args:
            media_id: Media identifier
            bridge: Bridge name ("discord", "console", "telegram")
            config: Bridge-specific config

        Returns:
            Send result
        """
        media = self.get_media(media_id)
        if not media:
            raise ValueError(f"Media not found: {media_id}")

        if bridge not in self.bridges:
            raise ValueError(f"Unknown bridge: {bridge}. Available: {list(self.bridges.keys())}")

        bridge_obj = self.bridges[bridge]
        if not bridge_obj.can_send():
            raise RuntimeError(f"Bridge '{bridge}' is not configured")

        result = bridge_obj.send(media, config)

        # Track bridge ID if send succeeded
        if result.get("status") in ["sent", "registered"] and "bridge_id" in result:
            self.media_manager.update_bridge_id(
                media_id,
                bridge,
                result["bridge_id"]
            )

        return result

    def stream_media(self, media_id: str) -> StreamingResponse:
        """Stream media file for playback

        Args:
            media_id: Media identifier

        Returns:
            StreamingResponse for the media file
        """
        media = self.get_media(media_id)
        if not media:
            raise ValueError(f"Media not found: {media_id}")

        file_path = Path(media.file_path)
        if not file_path.exists():
            raise ValueError(f"Media file not found: {media.file_path}")

        def iterate_file(file_path: Path, chunk_size: int = 1024 * 1024):
            """Iterate file in chunks"""
            with open(file_path, "rb") as f:
                while chunk := f.read(chunk_size):
                    yield chunk

        return StreamingResponse(
            iterate_file(file_path),
            media_type=media.mime_type,
            headers={
                "Content-Disposition": f"inline; filename={media.original_name}",
                "Content-Length": str(media.file_size_bytes),
            }
        )


# Global instance
_media_manager: Optional[MediaRouteManager] = None


def get_media_manager() -> MediaRouteManager:
    """Get or create media manager instance"""
    global _media_manager
    if _media_manager is None:
        _media_manager = MediaRouteManager()
    return _media_manager


def create_media_router() -> Any:
    """Create FastAPI router for media endpoints"""
    if APIRouter is None:
        log.warning("FastAPI not available, media routes unavailable")
        return None

    router = APIRouter(prefix="/v1/console/media", tags=["media"])
    media_mgr = get_media_manager()

    @router.post("/upload", status_code=201)
    async def upload_media(
        file: UploadFile = File(...),
        tags: list[str] = Body(None),
        description: str = Body(""),
        is_public: bool = Body(False),
    ) -> dict:
        """Upload a media file"""
        try:
            media = media_mgr.upload_media(
                file=file,
                tags=tags or [],
                description=description,
                is_public=is_public
            )

            return {
                "status": "uploaded",
                "media": {
                    "media_id": media.media_id,
                    "filename": media.original_name,
                    "size_mb": round(media.size_mb(), 1),
                    "mime_type": media.mime_type,
                    "created_at": media.created_at,
                    "url": f"/v1/console/media/{media.media_id}",
                    "metadata": {
                        "duration_seconds": media.duration_seconds,
                        "width": media.width,
                        "height": media.height,
                        "tags": media.tags,
                        "description": media.description,
                    }
                }
            }
        except Exception as e:
            log.error(f"Upload failed: {e}")
            raise HTTPException(status_code=400, detail={"error": "upload_failed", "reason": str(e)})

    @router.get("/{media_id}", status_code=200)
    async def stream_media(media_id: str):
        """Stream media file for playback"""
        try:
            return media_mgr.stream_media(media_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail={"error": "media_not_found", "reason": str(e)})
        except Exception as e:
            log.error(f"Stream failed: {e}")
            raise HTTPException(status_code=500, detail={"error": "stream_failed", "reason": str(e)})

    @router.get("/{media_id}/metadata", status_code=200)
    async def get_metadata(media_id: str) -> dict:
        """Get media metadata"""
        media = media_mgr.get_media(media_id)
        if not media:
            raise HTTPException(status_code=404, detail={"error": "media_not_found"})

        return {
            "media_id": media.media_id,
            "filename": media.original_name,
            "size_bytes": media.file_size_bytes,
            "size_mb": round(media.size_mb(), 1),
            "mime_type": media.mime_type,
            "created_at": media.created_at,
            "expires_at": media.expires_at,
            "is_public": media.is_public,
            "is_archived": media.is_archived,
            "uploaded_by": media.uploaded_by,
            "tags": media.tags,
            "description": media.description,
            "metadata": {
                "duration_seconds": media.duration_seconds,
                "width": media.width,
                "height": media.height,
            },
            "bridge_ids": media.bridge_ids,
        }

    @router.get("/list", status_code=200)
    async def list_media(tags: list[str] = None) -> dict:
        """List all media files"""
        media_list = media_mgr.list_media(tags=tags)

        return {
            "total": len(media_list),
            "media": [
                {
                    "media_id": m.media_id,
                    "filename": m.original_name,
                    "size_mb": round(m.size_mb(), 1),
                    "mime_type": m.mime_type,
                    "created_at": m.created_at,
                    "tags": m.tags,
                    "description": m.description,
                    "url": f"/v1/console/media/{m.media_id}",
                }
                for m in media_list
            ]
        }

    @router.post("/send-to-bridge", status_code=202)
    async def send_to_bridge(
        media_id: str = Body(...),
        bridge: str = Body(...),
        config: dict = Body(...),
    ) -> dict:
        """Send media to a bridge (Discord, etc.)

        Example config for Discord:
        {
            "channel_id": "123456789",
            "message": "Check this out!"
        }
        """
        try:
            result = media_mgr.send_to_bridge(media_id, bridge, config)
            return {
                "status": "sent",
                "bridge": bridge,
                "result": result,
            }
        except ValueError as e:
            raise HTTPException(status_code=404, detail={"error": "not_found", "reason": str(e)})
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail={"error": "bridge_error", "reason": str(e)})
        except Exception as e:
            log.error(f"Send to bridge failed: {e}")
            raise HTTPException(status_code=500, detail={"error": "send_failed", "reason": str(e)})

    @router.delete("/{media_id}", status_code=200)
    async def delete_media(media_id: str) -> dict:
        """Delete a media file"""
        try:
            if media_mgr.delete_media(media_id):
                return {"status": "deleted", "media_id": media_id}
            else:
                raise HTTPException(status_code=404, detail={"error": "media_not_found"})
        except Exception as e:
            log.error(f"Delete failed: {e}")
            raise HTTPException(status_code=500, detail={"error": "delete_failed", "reason": str(e)})

    return router


# Create router instance for import
router = create_media_router() if APIRouter else None
