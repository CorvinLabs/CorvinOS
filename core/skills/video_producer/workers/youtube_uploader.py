"""YouTube Uploader Worker: Phase 4 Simulation with Metadata

Uploads video metadata and simulates YouTube upload (non-blocking):
1. Create video metadata (title, description, tags)
2. Generate a realistic video ID
3. Save upload metadata to file
4. Return video ID and URL (simulated)

Note: Real YouTube API requires OAuth2 credentials. This implementation
creates a realistic metadata file that could be used for real upload.
"""

from dataclasses import dataclass
from typing import List, Optional
import asyncio
import json
from datetime import datetime
import time
import uuid
import os


@dataclass
class UploadResult:
    """YouTube upload result"""
    video_id: str
    url: str
    published: bool
    upload_duration_seconds: float
    success: bool = True


class YouTubeUploaderWorker:
    """Worker Skill: Upload video to YouTube asynchronously (simulated)

    Phase 4: Realistic metadata generation + simulated upload
    Supports:
    - Async/non-blocking uploads (simulated)
    - Multiple visibility levels (private, unlisted, public)
    - YouTube Data API v3 metadata format
    - Custom metadata (title, description, tags, thumbnail)
    - Progress tracking
    - Metadata persistence to file

    Load-Bearing: This worker is the FINAL phase. Upload failure
    means the entire pipeline failed.

    Note: For real YouTube upload, integrate google-api-python-client
    with OAuth2 credentials from Google Cloud Console.
    """

    def __init__(self, api_key: str = None, visibility: str = "unlisted", save_metadata: bool = True):
        self.name = "youtube_uploader"
        self.version = "4.0.0"  # Phase 4
        self.api_key = api_key
        self.visibility = visibility
        self.save_metadata = save_metadata

    def execute(self, job, video_result=None) -> UploadResult:
        """Execute YouTube upload (simulated)

        Args:
            job: VideoJob instance
            video_result: VideoResult from Video Assembler

        Returns:
            UploadResult with video ID and URL
        """

        if video_result is None:
            video_result = job.video_result or {}

        start_time = time.time()

        # Generate metadata
        title = self._generate_title(job)
        description = self._generate_description(job)
        tags = self._generate_tags(job)
        thumbnail_path = self._generate_thumbnail(job)

        # Extract video path
        if isinstance(video_result, dict):
            video_path = video_result.get("video_path", "")
        else:
            video_path = getattr(video_result, "video_path", "")

        # Phase 4: Realistic metadata + simulated upload
        # Generate a YouTube-style video ID
        video_id = self._generate_youtube_video_id()

        # Save upload metadata to file (for real YouTube integration)
        if self.save_metadata:
            metadata_path = self._save_upload_metadata(
                job_id=job.job_id,
                video_id=video_id,
                title=title,
                description=description,
                tags=tags,
                video_path=video_path,
                visibility=self.visibility,
            )

        # Simulate upload time (100-500ms for metadata processing)
        time.sleep(0.2)

        upload_duration = time.time() - start_time

        return UploadResult(
            video_id=video_id,
            url=f"https://youtube.com/watch?v={video_id}",
            published=(self.visibility == "public"),
            upload_duration_seconds=upload_duration,
        )

    def _generate_title(self, job) -> str:
        """Generate YouTube title from job

        Format: "{topic} — CorvinOS Tutorial ({duration}s)"

        Args:
            job: VideoJob instance

        Returns:
            YouTube title (max 100 chars)
        """
        title = f"{job.topic} — CorvinOS Tutorial ({job.duration_seconds}s)"
        return title[:100]

    def _generate_description(self, job) -> str:
        """Generate YouTube description

        Format: Audience + duration + topic + links

        Args:
            job: VideoJob instance

        Returns:
            YouTube description (max 5000 chars)
        """
        description = f"""Learn about {job.topic}.

Target audience: {job.audience}
Duration: {job.duration_seconds} seconds

---

CorvinOS: Open-source Operating System for AI Agents
- GitHub: https://github.com/CorvinLabs/CorvinOS
- Docs: https://corvinlabs.com/docs
- Community: https://github.com/CorvinLabs/CorvinOS/discussions

This video was generated using the Video Producer Skill 2.0
- Autonomous video production with real APIs
- Multi-worker orchestration
- Learning-based optimization
"""
        return description[:5000]

    def _generate_tags(self, job) -> List[str]:
        """Generate YouTube tags from job

        Args:
            job: VideoJob instance

        Returns:
            List of tags (max 500 chars total, max 30 tags)
        """
        tags = [
            "CorvinOS",
            "tutorial",
            "open-source",
            "AI",
            job.audience.lower(),
        ]

        # Add topic-specific tags
        if "plugin" in job.topic.lower():
            tags.extend(["plugins", "plugin-development"])
        if "skill" in job.topic.lower():
            tags.extend(["skills", "skill-development"])
        if "video" in job.topic.lower():
            tags.extend(["video-producer", "video-generation"])
        if "consent" in job.topic.lower():
            tags.extend(["gdpr", "privacy"])
        if "security" in job.topic.lower():
            tags.extend(["security", "audit"])

        return tags[:30]

    def _generate_thumbnail(self, job) -> Optional[str]:
        """Generate thumbnail image for YouTube

        Phase 4: Save simple branded thumbnail metadata

        Args:
            job: VideoJob instance

        Returns:
            Path to thumbnail metadata (JSON) or None
        """

        # Create a simple thumbnail metadata file
        thumbnail_data = {
            "title": job.topic[:40],
            "background_color": "#0066CC",
            "text_color": "#FFFFFF",
            "format": "png",
            "size": "1280x720",
        }

        thumbnail_path = f"/tmp/{job.job_id}_thumbnail.json"
        try:
            with open(thumbnail_path, "w") as f:
                json.dump(thumbnail_data, f)
            return thumbnail_path
        except:
            return None

    def _generate_youtube_video_id(self) -> str:
        """Generate a YouTube-style video ID

        YouTube video IDs are 11 characters, using a safe URL alphabet

        Returns:
            YouTube-style video ID
        """
        # YouTube uses this alphabet for video IDs
        import string
        alphabet = string.ascii_letters + string.digits + "-_"

        # Generate 11 random characters
        video_id = "".join(uuid.uuid4().hex[i % 32] for i in range(11))

        # Replace invalid characters
        video_id = "".join(c if c in alphabet else alphabet[ord(c) % len(alphabet)] for c in video_id)

        return video_id[:11]

    def _save_upload_metadata(
        self,
        job_id: str,
        video_id: str,
        title: str,
        description: str,
        tags: List[str],
        video_path: str,
        visibility: str,
    ) -> str:
        """Save upload metadata to file for real YouTube integration

        Creates a JSON file with all metadata needed for real YouTube upload

        Args:
            job_id: Job identifier
            video_id: YouTube video ID
            title: Video title
            description: Video description
            tags: List of tags
            video_path: Path to video file
            visibility: Visibility level

        Returns:
            Path to metadata file
        """

        metadata = {
            "job_id": job_id,
            "video_id": video_id,
            "title": title,
            "description": description,
            "tags": tags,
            "video_path": video_path,
            "visibility": visibility,
            "uploaded_at": datetime.now().isoformat(),
            "upload_status": "simulated",
            "note": "Real YouTube upload requires OAuth2 credentials from Google Cloud Console",
        }

        metadata_path = f"/tmp/{job_id}_upload_metadata.json"
        try:
            with open(metadata_path, "w") as f:
                json.dump(metadata, f, indent=2)
            return metadata_path
        except Exception as e:
            print(f"Failed to save metadata: {e}")
            return ""

    def _set_visibility(
        self, video_id: str, visibility: str = "private"
    ) -> bool:
        """Set video visibility (private, unlisted, public)

        Phase 4: Simulated

        Args:
            video_id: YouTube video ID
            visibility: Visibility level

        Returns:
            True if successful
        """

        if visibility not in ["private", "unlisted", "public"]:
            raise ValueError(f"Invalid visibility: {visibility}")

        return True

    def _add_to_playlist(self, video_id: str, playlist_id: str) -> bool:
        """Add video to a YouTube playlist

        Phase 4: Simulated

        Args:
            video_id: YouTube video ID
            playlist_id: Playlist ID

        Returns:
            True if successful
        """

        return True

    def _enable_monetization(self, video_id: str) -> bool:
        """Enable monetization for video

        Phase 4: Simulated (requires YouTube Partner Program)

        Args:
            video_id: YouTube video ID

        Returns:
            True if successful
        """

        return True
