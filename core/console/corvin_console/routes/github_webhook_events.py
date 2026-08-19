"""GitHub Webhook Event Model & Processing.

Defines event types, schema, queue, and delivery guarantees.
Supports: Creation, Update, Delete, Conflict, Sync events.
"""

import json
import hashlib
import hmac
from datetime import datetime
from enum import Enum
from typing import Dict, Any, List, Optional
from pathlib import Path
from dataclasses import dataclass, asdict
import logging
import threading
import queue as qlib

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """GitHub webhook event types."""
    SYNC_STARTED = "sync_started"
    SYNC_COMPLETED = "sync_completed"
    SYNC_FAILED = "sync_failed"
    SKILL_CREATED = "skill_created"
    SKILL_UPDATED = "skill_updated"
    SKILL_DELETED = "skill_deleted"
    CONFLICT_DETECTED = "conflict_detected"
    CONFLICT_RESOLVED = "conflict_resolved"
    WEBHOOK_RECEIVED = "webhook_received"
    DELIVERY_FAILED = "delivery_failed"
    DELIVERY_RETRY = "delivery_retry"


class DeliveryGuarantee(str, Enum):
    """Event delivery guarantee levels."""
    AT_LEAST_ONCE = "at_least_once"  # Default: may retry multiple times
    EXACTLY_ONCE = "exactly_once"    # Requires idempotency key + dedup
    AT_MOST_ONCE = "at_most_once"    # Fire and forget, no retry


@dataclass(frozen=True)
class WebhookEvent:
    """Immutable event structure."""
    event_id: str
    event_type: EventType
    timestamp: str
    tenant_id: str
    repo_url: str
    payload: Dict[str, Any]
    delivery_guarantee: DeliveryGuarantee = DeliveryGuarantee.AT_LEAST_ONCE
    retry_count: int = 0
    max_retries: int = 3
    created_at: str = None

    def __post_init__(self):
        """Set created_at if not provided."""
        if self.created_at is None:
            object.__setattr__(self, 'created_at', datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        data = asdict(self)
        data['event_type'] = self.event_type.value
        data['delivery_guarantee'] = self.delivery_guarantee.value
        return data

    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps(self.to_dict())

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'WebhookEvent':
        """Deserialize from dict."""
        data['event_type'] = EventType(data['event_type'])
        data['delivery_guarantee'] = DeliveryGuarantee(data.get('delivery_guarantee', 'at_least_once'))
        return WebhookEvent(**data)

    def compute_hash(self) -> str:
        """Compute hash for deduplication (exactly-once)."""
        content = f"{self.event_id}:{self.tenant_id}:{self.repo_url}:{self.event_type}"
        return hashlib.sha256(content.encode()).hexdigest()


class EventQueue:
    """Thread-safe event queue with persistence."""

    def __init__(self, tenant_id: str = "_default", max_size: int = 1000):
        self.tenant_id = tenant_id
        self.queue: qlib.Queue = qlib.Queue(maxsize=max_size)
        self.tenant_path = Path.home() / '.corvin' / 'tenants' / tenant_id
        self.event_log = self.tenant_path / 'github-events.jsonl'
        self.dead_letter_dir = self.tenant_path / 'github-dlq'
        self.dead_letter_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def enqueue(self, event: WebhookEvent) -> bool:
        """Add event to queue with persistence."""
        try:
            self.queue.put(event, timeout=1)
            self._persist_event(event)
            logger.info(f"Event enqueued: {event.event_id} ({event.event_type.value})")
            return True
        except qlib.Full:
            logger.error(f"Event queue full, moving to dead-letter: {event.event_id}")
            self._move_to_dlq(event, "queue_full")
            return False
        except Exception as e:
            logger.error(f"Failed to enqueue event: {e}")
            self._move_to_dlq(event, str(e))
            return False

    def dequeue(self, timeout: int = 5) -> Optional[WebhookEvent]:
        """Get next event from queue."""
        try:
            return self.queue.get(timeout=timeout)
        except qlib.Empty:
            return None

    def retry_event(self, event: WebhookEvent) -> bool:
        """Re-queue an event for retry."""
        if event.retry_count < event.max_retries:
            retry_event = WebhookEvent(
                event_id=event.event_id,
                event_type=event.event_type,
                timestamp=event.timestamp,
                tenant_id=event.tenant_id,
                repo_url=event.repo_url,
                payload=event.payload,
                delivery_guarantee=event.delivery_guarantee,
                retry_count=event.retry_count + 1,
                max_retries=event.max_retries,
                created_at=event.created_at
            )
            logger.info(f"Retrying event {event.event_id} (attempt {retry_event.retry_count}/{event.max_retries})")
            return self.enqueue(retry_event)
        else:
            logger.error(f"Event {event.event_id} exhausted retries ({event.retry_count}), moving to DLQ")
            self._move_to_dlq(event, f"exhausted_retries_{event.retry_count}")
            return False

    def _persist_event(self, event: WebhookEvent):
        """Write event to persistent log."""
        try:
            with open(self.event_log, 'a') as f:
                f.write(event.to_json() + '\n')
        except Exception as e:
            logger.error(f"Failed to persist event: {e}")

    def _move_to_dlq(self, event: WebhookEvent, reason: str):
        """Move event to dead-letter queue."""
        try:
            dlq_file = self.dead_letter_dir / f"{event.event_id}.dlq"
            with open(dlq_file, 'w') as f:
                json.dump({
                    "event": event.to_dict(),
                    "reason": reason,
                    "timestamp": datetime.utcnow().isoformat() + "Z"
                }, f, indent=2)
            logger.warning(f"Event moved to DLQ: {event.event_id} ({reason})")
        except Exception as e:
            logger.error(f"Failed to write to DLQ: {e}")

    def queue_size(self) -> int:
        """Get current queue size."""
        return self.queue.qsize()


class EventProcessor:
    """Process events with delivery guarantee enforcement."""

    def __init__(self, tenant_id: str = "_default", webhook_secret: Optional[str] = None):
        self.tenant_id = tenant_id
        # FIX 1: Require webhook secret (fail-closed)
        if not webhook_secret:
            raise ValueError("Webhook secret required for security (fail-closed)")
        self.webhook_secret = webhook_secret
        self.queue = EventQueue(tenant_id)
        self.processed_ids = set()  # For exactly-once deduplication
        self.lock = threading.Lock()
        self.tenant_path = Path.home() / '.corvin' / 'tenants' / tenant_id

    def process_github_webhook(self, payload: Dict[str, Any], signature: str = "") -> Dict[str, Any]:
        """Process incoming GitHub webhook."""
        # Verify signature
        if not self._verify_signature(payload, signature):
            return {"success": False, "error": "Invalid webhook signature"}

        try:
            # Create event from webhook payload
            event = self._create_event_from_webhook(payload)

            # Check for exactly-once idempotency
            if event.delivery_guarantee == DeliveryGuarantee.EXACTLY_ONCE:
                if not self._check_idempotency(event):
                    logger.warning(f"Duplicate event (already processed): {event.event_id}")
                    return {"success": True, "message": "Event deduplicated (already processed)"}
                self._mark_processed(event)

            # Enqueue for processing
            if self.queue.enqueue(event):
                return {"success": True, "event_id": event.event_id, "message": "Event enqueued"}
            else:
                return {"success": False, "error": "Failed to enqueue event"}

        except Exception as e:
            logger.error(f"Failed to process webhook: {e}")
            return {"success": False, "error": str(e)}

    def _verify_signature(self, payload: str, signature: str) -> bool:
        """Verify GitHub webhook signature (X-Hub-Signature-256)."""
        if not self.webhook_secret:
            logger.warning("No webhook secret configured, skipping signature verification")
            return True

        try:
            expected_sig = "sha256=" + hmac.new(
                self.webhook_secret.encode(),
                payload.encode() if isinstance(payload, str) else json.dumps(payload).encode(),
                hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(expected_sig, signature)
        except Exception as e:
            logger.error(f"Signature verification failed: {e}")
            return False

    def _create_event_from_webhook(self, payload: Dict[str, Any]) -> WebhookEvent:
        """Convert GitHub webhook payload to internal event."""
        return WebhookEvent(
            event_id=f"evt-{datetime.utcnow().strftime('%s')}-{hash(str(payload)) % 10000}",
            event_type=EventType.WEBHOOK_RECEIVED,
            timestamp=datetime.utcnow().isoformat() + "Z",
            tenant_id=self.tenant_id,
            repo_url=payload.get("repository", {}).get("html_url", "unknown"),
            payload=payload,
            delivery_guarantee=DeliveryGuarantee.AT_LEAST_ONCE
        )

    def _check_idempotency(self, event: WebhookEvent) -> bool:
        """Check if event has been processed (exactly-once guarantee).

        FIX 5: Atomic check-and-insert to prevent race condition.
        """
        with self.lock:
            event_hash = event.compute_hash()
            if event_hash in self.processed_ids:
                return False
            # Atomic insert while holding lock
            self.processed_ids.add(event_hash)
            return True

    def _mark_processed(self, event: WebhookEvent):
        """Mark event as processed (deprecated - use _check_idempotency atomically)."""
        # Note: This is now redundant but kept for compat
        with self.lock:
            event_hash = event.compute_hash()
            self.processed_ids.add(event_hash)

    def get_queue_stats(self) -> Dict[str, Any]:
        """Get queue statistics."""
        dlq_count = len(list(self.queue.dead_letter_dir.glob("*.dlq")))
        return {
            "queue_size": self.queue.queue_size(),
            "dlq_size": dlq_count,
            "processed_count": len(self.processed_ids),
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
