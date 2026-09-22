"""Media file manager and storage"""

import os
import json
import uuid
import hashlib
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List
from shutil import copy2, rmtree

from .models import MediaFile, MediaManifest

class MediaManager:
    """Manages media files and distribution across bridges"""

    def __init__(self, root: Path = None):
        """Initialize media manager

        Args:
            root: Root directory for media storage (default: ~/.corvin/media)
        """
        if root is None:
            root = Path.home() / ".corvin" / "media"

        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

        # Create subdirectories
        (self.root / "generated").mkdir(exist_ok=True)
        (self.root / "uploads").mkdir(exist_ok=True)
        (self.root / "cache").mkdir(exist_ok=True)

        self.manifest_path = self.root / "manifest.json"
        self.manifest = self._load_manifest()

    def _load_manifest(self) -> MediaManifest:
        """Load manifest from disk"""
        if self.manifest_path.exists():
            with open(self.manifest_path, "r") as f:
                data = json.load(f)
                return MediaManifest.from_dict(data)
        return MediaManifest()

    def _save_manifest(self):
        """Persist manifest to disk"""
        with open(self.manifest_path, "w") as f:
            json.dump(self.manifest.to_dict(), f, indent=2)

    def upload(
        self,
        file_path: str,
        tags: List[str] = None,
        description: str = "",
        is_public: bool = False,
        uploaded_by: str = "system"
    ) -> MediaFile:
        """Upload and register a media file

        Args:
            file_path: Path to file to upload
            tags: Optional tags for categorization
            description: Optional description
            is_public: Make publicly accessible
            uploaded_by: User or service uploading

        Returns:
            MediaFile with metadata

        Raises:
            FileNotFoundError: If file doesn't exist
        """

        source_path = Path(file_path)
        if not source_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Generate unique media ID
        media_id = f"media_{uuid.uuid4().hex[:12]}"

        # Determine destination directory
        dest_dir = self.root / "generated"
        dest_path = dest_dir / f"{media_id}_{source_path.name}"

        # Copy file to media storage
        copy2(source_path, dest_path)

        # Calculate file hash
        file_hash = self._calculate_hash(dest_path)

        # Detect MIME type
        mime_type = self._detect_mime_type(dest_path)

        # Extract metadata (duration, dimensions, etc.)
        metadata = self._extract_metadata(dest_path, mime_type)

        # Create media file object
        media = MediaFile(
            media_id=media_id,
            original_name=source_path.name,
            file_path=str(dest_path),
            file_size_bytes=dest_path.stat().st_size,
            mime_type=mime_type,
            hash_sha256=file_hash,
            tags=tags or [],
            description=description,
            is_public=is_public,
            uploaded_by=uploaded_by,
            duration_seconds=metadata.get("duration"),
            width=metadata.get("width"),
            height=metadata.get("height"),
        )

        # Add to manifest
        self.manifest.add(media)
        self._save_manifest()

        return media

    def get(self, media_id: str) -> Optional[MediaFile]:
        """Get media by ID"""
        return self.manifest.get(media_id)

    def list(self, tags: List[str] = None) -> List[MediaFile]:
        """List media files, optionally filtered by tags"""
        all_media = self.manifest.list_all()

        if tags:
            return [m for m in all_media if any(tag in m.tags for tag in tags)]

        return all_media

    def delete(self, media_id: str):
        """Delete media file"""
        media = self.get(media_id)
        if not media:
            raise ValueError(f"Media not found: {media_id}")

        # Delete file from storage
        Path(media.file_path).unlink(missing_ok=True)

        # Remove from manifest
        self.manifest.remove(media_id)
        self._save_manifest()

    def cleanup_expired(self) -> int:
        """Remove expired media files

        Returns:
            Number of files deleted
        """

        expired_ids = []
        for media in self.manifest.list_all():
            if media.is_expired():
                expired_ids.append(media.media_id)

        for media_id in expired_ids:
            self.delete(media_id)

        return len(expired_ids)

    def update_bridge_id(self, media_id: str, bridge: str, bridge_id: str):
        """Track media across bridges

        Args:
            media_id: Media identifier
            bridge: Bridge name (discord, telegram, etc.)
            bridge_id: ID on that bridge (message_id, file_id, etc.)
        """

        media = self.get(media_id)
        if not media:
            raise ValueError(f"Media not found: {media_id}")

        # Update manifest
        if media_id in self.manifest.files:
            self.manifest.files[media_id]["bridge_ids"][bridge] = bridge_id
            self._save_manifest()

    @staticmethod
    def _calculate_hash(file_path: Path) -> str:
        """Calculate SHA256 hash of file"""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    @staticmethod
    def _detect_mime_type(file_path: Path) -> str:
        """Detect MIME type from extension"""

        extension_map = {
            ".mp4": "video/mp4",
            ".mkv": "video/x-matroska",
            ".webm": "video/webm",
            ".mov": "video/quicktime",
            ".avi": "video/x-msvideo",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".mp3": "audio/mpeg",
            ".aac": "audio/aac",
            ".wav": "audio/wav",
            ".pdf": "application/pdf",
        }

        ext = file_path.suffix.lower()
        return extension_map.get(ext, "application/octet-stream")

    @staticmethod
    def _extract_metadata(file_path: Path, mime_type: str) -> dict:
        """Extract metadata using ffprobe"""

        if "video" not in mime_type and "audio" not in mime_type:
            return {}

        try:
            cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-show_entries", "stream=width,height",
                "-of", "json",
                str(file_path)
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                data = json.loads(result.stdout)

                duration = None
                if "format" in data and "duration" in data["format"]:
                    duration = int(float(data["format"]["duration"]))

                width, height = None, None
                if "streams" in data and len(data["streams"]) > 0:
                    stream = data["streams"][0]
                    width = stream.get("width")
                    height = stream.get("height")

                return {
                    "duration": duration,
                    "width": width,
                    "height": height
                }
        except:
            pass

        return {}
