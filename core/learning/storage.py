"""TreeOfThoughts storage (Phase 1): append-only event log."""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from .models import TreeNode, LearningEvent, ConfidenceEvent


class LearningEventStore:
    """Append-only event log, date-partitioned JSON files."""
    
    def __init__(self, base_dir: Path = None):
        if base_dir is None:
            from forge import paths as fp
            base_dir = fp.tenant_home("_default") / "learning" / "events"
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.nodes_cache: dict[str, TreeNode] = {}
    
    def get_event_path(self, date_str: str = None) -> Path:
        """Get path for a given date's events. Default: today."""
        if date_str is None:
            date_str = datetime.now().strftime("%Y-%m-%d")
        return self.base_dir / f"{date_str}.jsonl"
    
    def append_event(self, subject_id: str, event: LearningEvent) -> None:
        """Append immutable event to log."""
        path = self.get_event_path()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "subject_id": subject_id,
                "event_type": event.event_type,
                "confidence_delta": event.confidence_delta,
                "reason": event.reason,
                "timestamp": event.timestamp,
                "context": event.context,
            }) + "\n")
    
    def get_events(self, subject_id: str, after: str = None) -> list[LearningEvent]:
        """Get all events for a subject, optionally after a timestamp."""
        events = []
        for event_file in sorted(self.base_dir.glob("*.jsonl")):
            with open(event_file, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if data.get("subject_id") == subject_id:
                        if after is None or data.get("timestamp", "") >= after:
                            events.append(LearningEvent(
                                subject_id=subject_id,
                                event_type=data["event_type"],
                                confidence_delta=data["confidence_delta"],
                                reason=data["reason"],
                                timestamp=data.get("timestamp", datetime.now().isoformat()),
                                context=data.get("context", {}),
                            ))
        return sorted(events, key=lambda e: e.timestamp)
    
    def register_node(self, node: TreeNode) -> None:
        """Register a node in the cache."""
        self.nodes_cache[node.id] = node
    
    def get_node(self, node_id: str) -> Optional[TreeNode]:
        """Get node from cache."""
        return self.nodes_cache.get(node_id)
    
    def all_nodes(self) -> list[TreeNode]:
        """All registered nodes."""
        return list(self.nodes_cache.values())
