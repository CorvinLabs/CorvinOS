"""Bridge abstraction base class"""

from abc import ABC, abstractmethod
from typing import Dict, Any
from core.media.models import MediaFile

class BridgeBase(ABC):
    """Abstract base for all media distribution bridges"""

    name: str  # "discord", "telegram", "slack", "console", etc.

    @abstractmethod
    def send(self, media: MediaFile, config: Dict[str, Any]) -> Dict[str, Any]:
        """Send media to this bridge

        Args:
            media: MediaFile to send
            config: Bridge-specific config (e.g., {"channel_id": "123"})

        Returns:
            {
                "status": "sent" | "failed" | "pending",
                "bridge": self.name,
                "bridge_id": "...",  # unique ID on this bridge
                "url": "...",        # shareable URL if applicable
                "error": "..."       # if failed
            }
        """
        pass

    @abstractmethod
    def can_send(self) -> bool:
        """Check if bridge is configured and can send"""
        pass

    def validate_config(self, config: Dict[str, Any]) -> bool:
        """Validate bridge-specific config (override if needed)"""
        return bool(config)
