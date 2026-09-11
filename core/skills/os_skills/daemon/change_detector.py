"""DataSourceChangeDetector — watches DataHub for source changes."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import hashlib
import json


@dataclass(frozen=True)
class SourceChangedEvent:
    """Immutable event: a data source was added, updated, or deleted."""
    source_id: str
    change_type: str  # "added" | "updated" | "deleted"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    old_hash: Optional[str] = None  # For "updated" | "deleted"
    new_hash: Optional[str] = None  # For "added" | "updated"
    metadata: Dict = field(default_factory=dict)

    def validate(self) -> bool:
        """Validate event correctness."""
        if self.change_type not in ["added", "updated", "deleted"]:
            return False
        if self.change_type == "added" and self.new_hash is None:
            return False
        if self.change_type == "updated" and (self.old_hash is None or self.new_hash is None):
            return False
        if self.change_type == "deleted" and self.old_hash is None:
            return False
        return True


class DataSourceChangeDetector:
    """
    Polls DataHub every 60s for source changes.

    Detects:
    - New sources added (emit SourceAddedEvent)
    - Existing sources updated (emit SourceUpdatedEvent)
    - Sources deleted (emit SourceDeletedEvent)
    """

    def __init__(self, poll_interval_seconds: int = 60):
        """
        Initialize detector.

        Args:
            poll_interval_seconds: How often to check for changes (default 60s)
        """
        self.poll_interval = poll_interval_seconds
        self.seen_sources: Dict[str, str] = {}  # {source_id: content_hash}
        self.events: List[SourceChangedEvent] = []

    def detect_changes(self, current_sources: Dict[str, str]) -> List[SourceChangedEvent]:
        """
        Detect changes in data sources.

        Args:
            current_sources: {source_id: content_str}

        Returns:
            List of SourceChangedEvent for any detected changes
        """
        new_events = []

        # Compute hashes for current sources
        current_hashes = {
            sid: self._hash_content(content)
            for sid, content in current_sources.items()
        }

        # Detect added sources
        for sid in current_hashes:
            if sid not in self.seen_sources:
                event = SourceChangedEvent(
                    source_id=sid,
                    change_type="added",
                    new_hash=current_hashes[sid],
                    metadata={"reason": "new_source_detected"}
                )
                if event.validate():
                    new_events.append(event)

        # Detect updated sources
        for sid in self.seen_sources:
            if sid in current_hashes:
                if self.seen_sources[sid] != current_hashes[sid]:
                    event = SourceChangedEvent(
                        source_id=sid,
                        change_type="updated",
                        old_hash=self.seen_sources[sid],
                        new_hash=current_hashes[sid],
                        metadata={"reason": "content_changed"}
                    )
                    if event.validate():
                        new_events.append(event)

        # Detect deleted sources
        for sid in self.seen_sources:
            if sid not in current_hashes:
                event = SourceChangedEvent(
                    source_id=sid,
                    change_type="deleted",
                    old_hash=self.seen_sources[sid],
                    metadata={"reason": "source_removed"}
                )
                if event.validate():
                    new_events.append(event)

        # Update state
        self.seen_sources = current_hashes
        self.events.extend(new_events)

        return new_events

    def get_all_events(self) -> List[SourceChangedEvent]:
        """Get all detected change events."""
        return list(self.events)

    def reset(self) -> None:
        """Clear all state (for testing)."""
        self.seen_sources = {}
        self.events = []

    @staticmethod
    def _hash_content(content: str) -> str:
        """Hash content deterministically."""
        return hashlib.sha256(content.encode()).hexdigest()
