"""
Queue Corruption Detection — ADR-0298

Detect corrupted messages in queue via checksums.
Fail-closed: corrupted messages quarantined + audited.
"""

from core.queue.detector import QueueCorruptionDetector

__all__ = ["QueueCorruptionDetector"]
