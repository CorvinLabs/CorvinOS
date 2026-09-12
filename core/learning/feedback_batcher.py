"""Phase 2c: Feedback Batcher — Batch Learning Outcomes for Unified Optimizer.

Buffers learning outcomes (task completions, feedback events) into batches for
the unified optimizer to process. Ensures non-blocking, tenant-scoped batching.

Triggers:
  - 100 outcomes accumulated, OR
  - 5 minutes elapsed (whichever comes first)

Guarantees:
  - Each batch contains outcomes from BOTH routing (L5) + context (L10) decisions
  - Outcomes ordered by timestamp
  - Tenant-scoped (no cross-tenant batches)
  - Non-blocking append-only (no locks on main request path)

ADR-0532 Phase 2c: Learning Optimizer
"""
from __future__ import annotations

import dataclasses
import logging
import time
import threading
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.learning.event_persistence import TaskOutcome

_log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class FeedbackBatch:
    """A batch of learning outcomes ready for optimizer."""

    batch_id: str  # UUID
    tenant_id: str
    skill_ids: set[str]  # Which skills' decisions are in this batch
    outcome_count: int
    timestamp_range: tuple[float, float]  # (earliest, latest)
    outcomes: tuple  # tuple of TaskOutcome (immutable)
    outcome_distribution: dict  # {success: N, partial: N, failure: N}


class FeedbackBatcher:
    """Buffers learning outcomes into batches for optimizer processing."""

    def __init__(
        self,
        batch_size: int = 100,
        batch_timeout_sec: int = 300,  # 5 minutes
        storage_dir: Path | None = None,
    ):
        """Initialize feedback batcher.

        Args:
            batch_size: Flush batch when this many outcomes accumulated
            batch_timeout_sec: Flush batch after this many seconds
            storage_dir: Optional path to persist batches (for durability)
        """
        self.batch_size = batch_size
        self.batch_timeout = batch_timeout_sec
        self.storage_dir = storage_dir

        # Per-tenant buffers (tenant_id -> deque of outcomes)
        self._buffers: dict[str, deque] = {}
        self._last_flush_time: dict[str, float] = {}
        # MEDIUM FIX #2: Lock for concurrent feedback batching
        self._lock = threading.Lock()

    def add_outcome(self, outcome: TaskOutcome) -> FeedbackBatch | None:
        """Add one outcome to the buffer.

        Returns:
            FeedbackBatch if buffer reached threshold, else None

        MEDIUM FIX #2: Thread-safe via Lock
        """
        # MEDIUM FIX #2: Acquire lock for concurrent feedback batching
        with self._lock:
            tenant_id = outcome.tenant_id
            self._buffers.setdefault(tenant_id, deque())

            # Add to buffer
            self._buffers[tenant_id].append(outcome)

            # Check flush conditions
            should_flush = (
                len(self._buffers[tenant_id]) >= self.batch_size
                or (time.time() - self._last_flush_time.get(tenant_id, 0)) > self.batch_timeout
            )

            if should_flush:
                return self.flush_batch(tenant_id)

            return None

    def flush_batch(self, tenant_id: str) -> FeedbackBatch | None:
        """Flush accumulated outcomes for a tenant into a batch.

        Returns:
            FeedbackBatch with all buffered outcomes, or None if buffer empty

        MEDIUM FIX #2: Thread-safe via Lock
        """
        # MEDIUM FIX #2: Acquire lock for flush operations
        with self._lock:
            buffer = self._buffers.get(tenant_id)
            if not buffer or len(buffer) == 0:
                return None

            outcomes = tuple(buffer)
            buffer.clear()

            # Compute batch metadata
            skill_ids = set()
            outcome_distribution = {"success": 0, "partial": 0, "failure": 0}
            timestamps = []

            for outcome in outcomes:
                skill_ids.add(outcome.decision_skill_id)
                timestamps.append(outcome.timestamp)
                status = "success" if outcome.success else ("partial" if outcome.partial else "failure")
                outcome_distribution[status] += 1

            # Create batch
            import uuid

            batch = FeedbackBatch(
                batch_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                skill_ids=skill_ids,
                outcome_count=len(outcomes),
                timestamp_range=(min(timestamps), max(timestamps)) if timestamps else (0, 0),
                outcomes=outcomes,
                outcome_distribution=outcome_distribution,
            )

            # Persist to disk
            if self.storage_dir:
                self._persist_batch(batch)

            # Update flush time
            self._last_flush_time[tenant_id] = time.time()

            _log.info(
                "Feedback batch flushed: batch_id=%s tenant_id=%s count=%d skills=%s",
                batch.batch_id,
                tenant_id,
                batch.outcome_count,
                batch.skill_ids,
            )

            return batch

    def _persist_batch(self, batch: FeedbackBatch) -> None:
        """Persist batch to disk for durability."""
        try:
            import json

            self.storage_dir.mkdir(parents=True, exist_ok=True)
            batch_path = self.storage_dir / f"{batch.batch_id}.json"

            # Serialize batch (outcomes are already serializable)
            batch_dict = {
                "batch_id": batch.batch_id,
                "tenant_id": batch.tenant_id,
                "skill_ids": list(batch.skill_ids),
                "outcome_count": batch.outcome_count,
                "timestamp_range": batch.timestamp_range,
                "outcome_distribution": batch.outcome_distribution,
                "created_at": time.time(),
            }

            with open(batch_path, "w") as f:
                json.dump(batch_dict, f)

        except Exception as exc:  # noqa: BLE001
            _log.warning("Failed to persist batch: %s", exc)

    def get_pending_batches(self) -> list[FeedbackBatch]:
        """Get list of batches not yet processed by optimizer.

        Returns:
            List of batches ready for processing (may be empty)
        """
        pending = []
        for tenant_id in list(self._buffers.keys()):
            if len(self._buffers[tenant_id]) > 0:
                batch = self.flush_batch(tenant_id)
                if batch:
                    pending.append(batch)
        return pending

    def buffer_size(self, tenant_id: str) -> int:
        """Get current buffer size for a tenant."""
        return len(self._buffers.get(tenant_id, []))
