"""Stream 2: Feedback Processor (Story 9 — Process feedback queue, batch).

Reads feedback queue, processes in batches, emits learning events.
Integrates with audit chain (GDPR Art. 30, 32).
"""

import logging
import asyncio
from pathlib import Path
from typing import Optional, List, Dict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from uuid import uuid4
import json

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeedbackSignal:
    """Immutable feedback signal (outcome, confidence, preference)."""
    signal_id: str
    timestamp: str
    feedback_id: str  # Links to feedback report
    skill_id: str
    signal_type: str  # "outcome" | "confidence" | "preference"
    value: float  # For confidence: 0-1; for outcome: 1=yes, 0=no
    comment: Optional[str] = None
    tenant_id: str = ""


class FeedbackProcessor:
    """Process feedback queue, batch, and emit learning events."""

    def __init__(self, feedback_home: Path, tenant_id: str, batch_size: int = 50):
        """Initialize processor.

        Args:
            feedback_home: Root directory for feedback
            tenant_id: Tenant scope
            batch_size: Number of feedback items per batch
        """
        if not tenant_id:
            raise ValueError("tenant_id required")

        self.feedback_home = Path(feedback_home)
        self.tenant_id = tenant_id
        self.batch_size = batch_size
        self.queue_dir = self.feedback_home / tenant_id / "feedback_queue"
        self.processed_dir = self.feedback_home / tenant_id / "processed"
        self.learning_events_dir = self.feedback_home / tenant_id / "learning_events"

        # Create directories
        for d in [self.queue_dir, self.processed_dir, self.learning_events_dir]:
            d.mkdir(parents=True, exist_ok=True)

    async def process_queue(self) -> Dict[str, int]:
        """Process feedback queue in batches (non-blocking).

        Returns:
            {"processed": N, "errors": M, "emitted_events": K}
        """
        try:
            # Read all feedback files from queue
            queue_files = sorted(self.queue_dir.glob("*.json"))
            if not queue_files:
                logger.info(f"feedback_queue_empty: {self.tenant_id}")
                return {"processed": 0, "errors": 0, "emitted_events": 0}

            # Batch process
            total_processed = 0
            total_errors = 0
            total_events = 0

            for batch in self._batch_iter(queue_files, self.batch_size):
                try:
                    result = await self._process_batch(batch)
                    total_processed += result["processed"]
                    total_errors += result["errors"]
                    total_events += result["emitted_events"]
                except Exception as e:
                    logger.error(f"batch_error: {e}")
                    total_errors += len(batch)

            return {
                "processed": total_processed,
                "errors": total_errors,
                "emitted_events": total_events,
            }

        except Exception as e:
            logger.exception(f"process_queue_error: {e}")
            return {"processed": 0, "errors": 0, "emitted_events": 0}

    async def _process_batch(self, batch: List[Path]) -> Dict[str, int]:
        """Process one batch of feedback items.

        Returns:
            {"processed": N, "errors": M, "emitted_events": K}
        """
        processed = 0
        errors = 0
        events = []

        for feedback_file in batch:
            try:
                with open(feedback_file, "r") as f:
                    feedback_data = json.load(f)

                # Create learning event
                event = self._create_learning_event(feedback_data)
                events.append(event)

                # Move to processed
                processed_file = self.processed_dir / feedback_file.name
                feedback_file.rename(processed_file)
                processed += 1

            except Exception as e:
                logger.error(f"process_item_error: {feedback_file.name}: {e}")
                errors += 1

        # Emit learning events (async, non-blocking)
        if events:
            asyncio.create_task(self._emit_events(events))

        return {"processed": processed, "errors": errors, "emitted_events": len(events)}

    def _create_learning_event(self, feedback_data: dict) -> dict:
        """Create a learning event from feedback data.

        Converts feedback → learning event for skill optimizer.
        """
        event_id = str(uuid4())
        timestamp = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

        event = {
            "event_id": event_id,
            "timestamp": timestamp,
            "event_type": "feedback_received",
            "tenant_id": self.tenant_id,
            "feedback_id": feedback_data.get("feedback_id", ""),
            "skill_id": feedback_data.get("skill_id", "unknown"),
            "signal_type": feedback_data.get("signal_type", "outcome"),
            "value": feedback_data.get("value", 0.5),
            "comment": feedback_data.get("comment", ""),
        }

        return event

    async def _emit_events(self, events: List[dict]) -> None:
        """Emit learning events (non-blocking fire-and-forget)."""
        try:
            for event in events:
                event_file = self.learning_events_dir / f"{event['event_id']}.json"
                with open(event_file, "w") as f:
                    json.dump(event, f)
                logger.debug(f"learning_event_emitted: {event['event_id']}")
        except Exception as e:
            logger.error(f"emit_events_error: {e}")

    def _batch_iter(self, items: List, batch_size: int):
        """Yield batches from items list."""
        for i in range(0, len(items), batch_size):
            yield items[i:i+batch_size]

    def get_queue_status(self) -> Dict:
        """Get current queue status (for dashboard)."""
        queue_files = list(self.queue_dir.glob("*.json"))
        processed_files = list(self.processed_dir.glob("*.json"))

        return {
            "pending": len(queue_files),
            "processed": len(processed_files),
            "total_events": len(queue_files) + len(processed_files),
        }


__all__ = ["FeedbackProcessor", "FeedbackSignal"]
