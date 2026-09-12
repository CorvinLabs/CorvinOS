"""YouTube Uploader Worker: Async video upload to YouTube (Phase 4).

Stages:
1. OAuth authentication (YouTube API)
2. File validation (MP4 must exist and be accessible)
3. Async upload enqueue (fire-and-forget, non-blocking)
4. Metadata update (title, description, tags, thumbnail)
5. SRT captions upload
6. Progress tracking (via Task API)
7. Emit UploadProgressEvent (ADR-0314)
"""

from .uploader import YouTubeUploader
from .youtube_api import YouTubeAPI

__all__ = ["YouTubeUploader", "YouTubeAPI"]
