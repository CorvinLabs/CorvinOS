"""Discord bridge for media distribution"""

import os
import json
from typing import Dict, Any
from pathlib import Path

from .base import BridgeBase
from core.media.models import MediaFile

class DiscordBridge(BridgeBase):
    """Send media to Discord channels"""

    name = "discord"

    def __init__(self):
        """Initialize Discord bridge"""
        self.token = os.getenv("DISCORD_BOT_TOKEN", "")
        self.config_file = Path.home() / ".corvin" / "bridges" / "discord.json"
        self.config_file.parent.mkdir(parents=True, exist_ok=True)

    def can_send(self) -> bool:
        """Check if Discord bridge is configured"""
        return bool(self.token) or self.config_file.exists()

    def send(self, media: MediaFile, config: Dict[str, Any]) -> Dict[str, Any]:
        """Send media to Discord channel

        Args:
            media: MediaFile to send
            config: {"channel_id": "123456789", "message": "optional text"}

        Returns:
            Send result
        """

        if not self.can_send():
            return {
                "status": "failed",
                "bridge": self.name,
                "error": "Discord bot not configured (missing DISCORD_BOT_TOKEN)"
            }

        channel_id = config.get("channel_id")
        message_text = config.get("message", "")

        if not channel_id:
            return {
                "status": "failed",
                "bridge": self.name,
                "error": "Missing channel_id in config"
            }

        # For PoC: Just validate and return success
        # In production: Would use discord.py or discord HTTP API

        # Validate file exists
        if not Path(media.file_path).exists():
            return {
                "status": "failed",
                "bridge": self.name,
                "error": f"Media file not found: {media.file_path}"
            }

        # Check file size (Discord max 25MB for most bots)
        if media.file_size_bytes > 25 * 1024 * 1024:
            return {
                "status": "failed",
                "bridge": self.name,
                "error": f"File too large for Discord ({media.size_mb():.1f}MB, max 25MB)"
            }

        # In real implementation, this would use:
        # import discord
        # channel = client.get_channel(int(channel_id))
        # await channel.send(
        #     content=message_text,
        #     file=discord.File(media.file_path, filename=media.original_name)
        # )

        # For now, log the action
        self._log_send(media, channel_id, message_text)

        return {
            "status": "sent",
            "bridge": self.name,
            "channel_id": channel_id,
            "media_name": media.original_name,
            "media_size_mb": round(media.size_mb(), 1),
            "url": f"https://discord.com/channels/guild/{channel_id}"
        }

    def _log_send(self, media: MediaFile, channel_id: str, message: str):
        """Log media send for tracking"""

        log_dir = Path.home() / ".corvin" / "media" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        log_entry = {
            "timestamp": media.created_at,
            "media_id": media.media_id,
            "channel_id": channel_id,
            "message": message,
            "file": media.original_name,
            "size_bytes": media.file_size_bytes
        }

        log_file = log_dir / f"discord_{media.media_id}.json"
        with open(log_file, "w") as f:
            json.dump(log_entry, f, indent=2)
