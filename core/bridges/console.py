"""Console bridge for media distribution (web UI integration)"""

from typing import Dict, Any
from pathlib import Path

from .base import BridgeBase
from core.media.models import MediaFile

class ConsoleBridge(BridgeBase):
    """Register media for console web UI display"""

    name = "console"

    def can_send(self) -> bool:
        """Console bridge is always available"""
        return True

    def send(self, media: MediaFile, config: Dict[str, Any]) -> Dict[str, Any]:
        """Register media for console display

        Returns URL that console can use for streaming/display
        """

        if not Path(media.file_path).exists():
            return {
                "status": "failed",
                "bridge": self.name,
                "error": f"Media file not found: {media.file_path}"
            }

        # Generate console URL for streaming
        console_url = f"/api/v1/media/{media.media_id}"

        return {
            "status": "registered",
            "bridge": self.name,
            "media_id": media.media_id,
            "url": console_url,
            "type": media.mime_type,
            "name": media.original_name,
            "size_mb": round(media.size_mb(), 1),
            "duration_sec": media.duration_seconds,
            "width": media.width,
            "height": media.height,
            "display_ready": True
        }
