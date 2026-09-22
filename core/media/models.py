"""Media file data models"""

from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from pathlib import Path
import json

@dataclass
class MediaFile:
    """Represents a managed media file with metadata"""

    media_id: str                    # Unique identifier (uuid4 hex)
    original_name: str               # Original filename
    file_path: str                   # Absolute path to file
    file_size_bytes: int             # File size in bytes
    mime_type: str                   # MIME type (video/mp4, etc.)
    created_at: str = ""             # ISO8601 timestamp
    expires_at: Optional[str] = None # TTL expiration (ISO8601)
    hash_sha256: str = ""            # SHA256 hash for deduplication

    # Optional metadata for video/audio
    duration_seconds: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None

    # Tags and categorization
    tags: List[str] = field(default_factory=list)
    description: str = ""

    # Upload metadata
    uploaded_by: str = "system"      # user_id or service name

    # Bridge tracking
    bridge_ids: Dict[str, str] = field(default_factory=dict)  # {"discord": "msg_123", ...}

    # Storage metadata
    is_public: bool = False          # Publicly accessible?
    is_archived: bool = False        # Archived (no auto-delete)?

    def __post_init__(self):
        """Initialize computed fields"""
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.expires_at:
            # Default: 60 days TTL
            self.expires_at = (datetime.now() + timedelta(days=60)).isoformat()

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MediaFile":
        """Create instance from dictionary"""
        return cls(**data)

    def is_expired(self) -> bool:
        """Check if file has expired"""
        if self.expires_at and not self.is_archived:
            return datetime.now() > datetime.fromisoformat(self.expires_at)
        return False

    def is_video(self) -> bool:
        return self.mime_type.startswith("video/")

    def is_audio(self) -> bool:
        return self.mime_type.startswith("audio/")

    def is_image(self) -> bool:
        return self.mime_type.startswith("image/")

    def size_mb(self) -> float:
        return self.file_size_bytes / (1024 * 1024)

    def __str__(self) -> str:
        return f"{self.original_name} ({self.size_mb():.1f}MB)"

@dataclass
class MediaManifest:
    """Manifest of all managed media files"""

    files: Dict[str, dict] = field(default_factory=dict)  # media_id -> MediaFile dict
    version: str = "1.0"
    last_updated: str = ""

    def __post_init__(self):
        if not self.last_updated:
            self.last_updated = datetime.now().isoformat()

    def add(self, media: MediaFile):
        """Add media to manifest"""
        self.files[media.media_id] = media.to_dict()
        self.last_updated = datetime.now().isoformat()

    def get(self, media_id: str) -> Optional[MediaFile]:
        """Get media by ID"""
        if media_id in self.files:
            return MediaFile.from_dict(self.files[media_id])
        return None

    def remove(self, media_id: str):
        """Remove media from manifest"""
        if media_id in self.files:
            del self.files[media_id]
            self.last_updated = datetime.now().isoformat()

    def list_all(self) -> List[MediaFile]:
        """Get all media files"""
        return [MediaFile.from_dict(data) for data in self.files.values()]

    def to_dict(self) -> dict:
        return {
            "files": self.files,
            "version": self.version,
            "last_updated": self.last_updated
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MediaManifest":
        manifest = cls()
        manifest.files = data.get("files", {})
        manifest.version = data.get("version", "1.0")
        manifest.last_updated = data.get("last_updated", "")
        return manifest
