"""In-memory hash-chained audit backend for the unified-loss / backprop tests.

Moved out of ``core/learning/unified_loss.py`` (adversarial review F-L10): a
mock that ships inside the production module is one import away from being
wired into a real optimizer, and the type annotation of
``UnifiedLossOptimizer.__init__`` even named it as the expected backend. The
production contract is ``core.learning.unified_loss.AuditBackend``; this class
satisfies it for tests only.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional


class MockAuditBackend:
    """Deterministic in-memory chain: sha256(prev_hash + event)."""

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []
        self.last_hash_value = "genesis"

    def write_event(self, event: Dict[str, Any]) -> Optional[str]:
        if event is None:
            return None
        event_hash = self._content_hash(event, self.last_hash_value)
        event["hash"] = event_hash
        event["prev_hash"] = self.last_hash_value
        self.events.append(event)
        self.last_hash_value = event_hash
        return event_hash

    def last_hash(self) -> str:
        return self.last_hash_value

    @staticmethod
    def _content_hash(event: Dict[str, Any], prev_hash: str) -> str:
        body = {k: v for k, v in event.items() if k not in ("hash", "prev_hash")}
        return hashlib.sha256(f"{prev_hash}{json.dumps(body, sort_keys=True, default=str)}".encode()).hexdigest()

    def verify_chain(self) -> bool:
        """Linkage AND content: a tampered field breaks the chain, like the real writer."""
        current = "genesis"
        for event in self.events:
            if event["prev_hash"] != current or event["hash"] != self._content_hash(event, current):
                return False
            current = event["hash"]
        return True

    def read_events(self, tenant_id: str) -> List[Dict[str, Any]]:
        return [e for e in self.events if e.get("tenant_id") == tenant_id]
