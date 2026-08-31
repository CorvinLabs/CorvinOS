"""
Queue Corruption Detector — ADR-0298

Detect corrupted messages via checksums. Quarantine on detection.
"""

import hashlib
import json
from typing import Any, Optional


class QueueCorruptionDetector:
    """Detect corrupted queue messages."""

    @staticmethod
    def compute_checksum(message: dict[str, Any]) -> str:
        """
        Compute SHA256 checksum of message content (excluding checksum field).

        Deterministic: excludes 'checksum' field itself.
        """
        # Create copy without checksum field
        content = {k: v for k, v in message.items() if k != "checksum"}

        # JSON serialize with sorted keys (deterministic)
        json_str = json.dumps(content, sort_keys=True, separators=(",", ":"))

        # Compute SHA256
        return hashlib.sha256(json_str.encode()).hexdigest()

    @staticmethod
    def add_checksum(message: dict[str, Any]) -> dict[str, Any]:
        """Add checksum to message."""
        message_with_checksum = message.copy()
        message_with_checksum["checksum"] = QueueCorruptionDetector.compute_checksum(message)
        return message_with_checksum

    @staticmethod
    def verify_checksum(message: dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        Verify message checksum.

        Returns: (is_valid, expected_checksum)
        """
        if "checksum" not in message:
            return False, "Missing checksum field"

        stored_checksum = message["checksum"]
        expected_checksum = QueueCorruptionDetector.compute_checksum(message)

        if stored_checksum != expected_checksum:
            return False, expected_checksum

        return True, None

    @staticmethod
    def is_corrupted(message: dict[str, Any]) -> bool:
        """Check if message is corrupted (checksum mismatch)."""
        is_valid, _ = QueueCorruptionDetector.verify_checksum(message)
        return not is_valid
