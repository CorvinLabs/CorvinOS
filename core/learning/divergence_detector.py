"""Finding #13 Mitigation: Convergence Lockout — Divergence Detector + Automatic Rollback.

Implements automatic recovery from suboptimal convergence by detecting when loss trajectory
diverges from the Pareto frontier and rolling back to the nearest non-dominated weights.

ADR-0647 Phase 2: Divergence Detection (Finding #13 mitigation)

Key mechanisms:
1. Loss Trajectory Monitor: Track loss over 100-sample windows
2. Divergence Detection: 3 consecutive windows with loss > baseline + 2*stddev
3. Pareto Frontier Tracking: Store non-dominated weight configurations
4. Weight Rollback: Restore to nearest frontier point + reduced learning rate
5. Audit-First: Every rollback logged before execution
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import json
import math

from .learning_events import LearningEvent, EventType

logger = logging.getLogger(__name__)

WINDOW_SIZE = 100
CONSECUTIVE_DIVERGENCE_WINDOWS = 3
STDDEV_THRESHOLD = 2.0


@dataclass(frozen=True)
class WeightSnapshot:
    """Immutable snapshot of loop weights at a point in time."""
    timestamp: str
    weights: Dict[str, float]
    loss: float
    is_frontier: bool = False  # True if this point is on Pareto frontier

    def dominates(self, other: WeightSnapshot) -> bool:
        """Check if this snapshot dominates another (lower loss)."""
        return self.loss < other.loss

    def dominated_by(self, frontier_point: WeightSnapshot) -> bool:
        """Check if this snapshot is dominated by a frontier point."""
        return frontier_point.loss < self.loss


@dataclass
class DivergenceEvent:
    """Detected divergence in loss trajectory."""
    timestamp: str
    loop_id: str
    window_index: int
    loss_tail_mean: float
    loss_baseline_mean: float
    stddev: float
    threshold: float
    consecutive_divergence_count: int


@dataclass
class RollbackEvent:
    """Recorded weight rollback to Pareto frontier."""
    timestamp: str
    loop_id: str
    reason: str  # "divergence_detected", etc.
    weights_before: Dict[str, float]
    weights_after: Dict[str, float]
    loss_before: float
    loss_after: float
    frontier_point: WeightSnapshot
    learning_rate_reduction: float  # 0.5 for 50% reduction


class DivergenceDetector:
    """Find #13 Mitigation: Detects convergence lockout and triggers rollback.

    Monitors loss trajectory for divergence and automatically rolls back weights
    to nearest Pareto frontier point + reduced learning rate.

    Guarantees:
    - Loss divergence detected within 300 samples (3 windows of 100)
    - Rollback audit-first (logged before weights changed)
    - Pareto frontier maintained (only non-dominated points stored)
    - Learning resumes with 0.5x learning rate for 50 samples
    - Tenant-scoped (GDPR Art. 32)
    """

    def __init__(
        self,
        loop_id: str,
        tenant_id: str = "_default",
        audit_backend=None,
        corvin_home: str = None,
    ):
        """Initialize divergence detector.

        Args:
            loop_id: Unique identifier (e.g., "memory", "skills", "routing")
            tenant_id: Tenant for isolation
            audit_backend: Audit backend (required for audit-first logging)
            corvin_home: Path to ~/.corvin
        """
        self.loop_id = loop_id
        self.tenant_id = tenant_id
        self.audit_backend = audit_backend

        # Persistence
        if corvin_home is None:
            from core.paths.tenant import corvin_home as _corvin_home  # noqa: PLC0415
            corvin_home = _corvin_home()
        self.corvin_home = Path(corvin_home)
        self.history_file = (
            self.corvin_home
            / "tenants"
            / tenant_id
            / "learning"
            / f"divergence_detector_{loop_id}.jsonl"
        )

        # Thread safety
        self._lock = threading.RLock()

        # Loss trajectory tracking (sliding window)
        self.loss_history: List[float] = []
        self.loss_windows: List[List[float]] = []  # Batches of WINDOW_SIZE

        # Pareto frontier: weight configurations that are not dominated
        self.frontier_points: List[WeightSnapshot] = []

        # Divergence tracking
        self.consecutive_divergence_count = 0
        self.last_divergence_event: Optional[DivergenceEvent] = None

        # Rollback tracking
        self.rollback_events: List[RollbackEvent] = []
        self.post_rollback_samples = 0

        # Load persisted history
        self._load_history()

    def _load_history(self) -> None:
        """Load persisted divergence history (recovery after restart)."""
        if not self.history_file.exists():
            return

        try:
            with open(self.history_file, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        record_type = data.get("type")

                        if record_type == "frontier_point":
                            # Reconstruct frontier point
                            point = WeightSnapshot(
                                timestamp=data.get("timestamp", ""),
                                weights=data.get("weights", {}),
                                loss=data.get("loss", 0.0),
                                is_frontier=True,
                            )
                            self.frontier_points.append(point)

                        elif record_type == "rollback_event":
                            # Reconstruct rollback event
                            event = RollbackEvent(
                                timestamp=data.get("timestamp", ""),
                                loop_id=data.get("loop_id", ""),
                                reason=data.get("reason", ""),
                                weights_before=data.get("weights_before", {}),
                                weights_after=data.get("weights_after", {}),
                                loss_before=data.get("loss_before", 0.0),
                                loss_after=data.get("loss_after", 0.0),
                                frontier_point=WeightSnapshot(
                                    timestamp=data.get("frontier_timestamp", ""),
                                    weights=data.get("frontier_weights", {}),
                                    loss=data.get("frontier_loss", 0.0),
                                    is_frontier=True,
                                ),
                                learning_rate_reduction=data.get("learning_rate_reduction", 0.5),
                            )
                            self.rollback_events.append(event)

                    except (json.JSONDecodeError, TypeError, KeyError) as e:
                        logger.warning(f"[Divergence Detector {self.loop_id}] Failed to load history: {e}")
        except Exception as e:
            logger.error(f"[Divergence Detector {self.loop_id}] Failed to load persisted history: {e}")

    def _persist_frontier_point(self, point: WeightSnapshot) -> None:
        """Append frontier point to disk."""
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)

            with open(self.history_file, "a") as f:
                record = {
                    "type": "frontier_point",
                    "timestamp": point.timestamp,
                    "loop_id": self.loop_id,
                    "weights": point.weights,
                    "loss": point.loss,
                }
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.error(f"[Divergence Detector {self.loop_id}] Failed to persist frontier point: {e}")

    def _persist_rollback_event(self, event: RollbackEvent) -> None:
        """Append rollback event to disk."""
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)

            with open(self.history_file, "a") as f:
                record = {
                    "type": "rollback_event",
                    "timestamp": event.timestamp,
                    "loop_id": event.loop_id,
                    "reason": event.reason,
                    "weights_before": event.weights_before,
                    "weights_after": event.weights_after,
                    "loss_before": event.loss_before,
                    "loss_after": event.loss_after,
                    "frontier_timestamp": event.frontier_point.timestamp,
                    "frontier_weights": event.frontier_point.weights,
                    "frontier_loss": event.frontier_point.loss,
                    "learning_rate_reduction": event.learning_rate_reduction,
                }
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.error(f"[Divergence Detector {self.loop_id}] Failed to persist rollback event: {e}")

    def record_loss(self, loss: float) -> None:
        """Record a loss measurement (called after each training step)."""
        with self._lock:
            if not isinstance(loss, (int, float)) or not math.isfinite(loss):
                raise ValueError(f"Invalid loss value: {loss}")

            self.loss_history.append(loss)

            # Build sliding windows of WINDOW_SIZE
            if len(self.loss_history) % WINDOW_SIZE == 0:
                start_idx = len(self.loss_history) - WINDOW_SIZE
                window = self.loss_history[start_idx:]
                self.loss_windows.append(window)

    def record_weights(self, weights: Dict[str, float]) -> None:
        """Record current weights (called after weight update)."""
        with self._lock:
            if not self.loss_history:
                return

            current_loss = self.loss_history[-1]
            snapshot = WeightSnapshot(
                timestamp=datetime.utcnow().isoformat() + "Z",
                weights=dict(weights),  # Immutable copy
                loss=current_loss,
            )

            # Update Pareto frontier
            self._update_frontier(snapshot)

    def _update_frontier(self, new_point: WeightSnapshot) -> None:
        """Update Pareto frontier with new point (remove dominated, add if non-dominated)."""
        # Remove points dominated by the new point
        self.frontier_points = [
            p for p in self.frontier_points
            if not new_point.dominates(p)
        ]

        # Check if new point is dominated by any frontier point
        is_dominated = any(p.dominates(new_point) for p in self.frontier_points)

        # Add new point if non-dominated
        if not is_dominated:
            new_point_non_frozen = WeightSnapshot(
                timestamp=new_point.timestamp,
                weights=new_point.weights,
                loss=new_point.loss,
                is_frontier=True,
            )
            self.frontier_points.append(new_point_non_frozen)
            self._persist_frontier_point(new_point_non_frozen)

    def detect_divergence(self) -> Optional[DivergenceEvent]:
        """Check if loss trajectory has diverged from baseline.

        Returns:
            DivergenceEvent if divergence detected, None otherwise
        """
        with self._lock:
            if len(self.loss_windows) < 2:
                # Not enough windows to detect divergence
                return None

            # Baseline: mean loss of all completed windows
            all_window_losses = [sum(w) / len(w) for w in self.loss_windows[:-1]]
            if not all_window_losses:
                return None

            baseline_mean = sum(all_window_losses) / len(all_window_losses)
            baseline_variance = sum(
                (x - baseline_mean) ** 2 for x in all_window_losses
            ) / max(1, len(all_window_losses))
            baseline_stddev = math.sqrt(baseline_variance)

            # Current window (may be partial)
            current_window = self.loss_windows[-1]
            current_mean = sum(current_window) / len(current_window)

            # Divergence threshold: baseline_mean + STDDEV_THRESHOLD * stddev
            threshold = baseline_mean + (STDDEV_THRESHOLD * baseline_stddev)

            # Check if current window exceeds threshold
            is_diverged = current_mean > threshold

            if is_diverged:
                self.consecutive_divergence_count += 1
            else:
                self.consecutive_divergence_count = 0

            # Emit event if divergence detected for CONSECUTIVE_DIVERGENCE_WINDOWS
            if self.consecutive_divergence_count >= CONSECUTIVE_DIVERGENCE_WINDOWS:
                event = DivergenceEvent(
                    timestamp=datetime.utcnow().isoformat() + "Z",
                    loop_id=self.loop_id,
                    window_index=len(self.loss_windows),
                    loss_tail_mean=current_mean,
                    loss_baseline_mean=baseline_mean,
                    stddev=baseline_stddev,
                    threshold=threshold,
                    consecutive_divergence_count=self.consecutive_divergence_count,
                )
                self.last_divergence_event = event
                return event

            return None

    def find_nearest_frontier_point(
        self, current_weights: Dict[str, float]
    ) -> Optional[WeightSnapshot]:
        """Find nearest non-dominated frontier point (by Euclidean distance in weight space).

        Returns:
            Nearest frontier point or None if frontier is empty
        """
        with self._lock:
            if not self.frontier_points:
                return None

            # Compute Euclidean distance to each frontier point
            min_distance = float('inf')
            nearest_point = None

            for frontier_point in self.frontier_points:
                distance = 0.0
                for key in current_weights:
                    if key in frontier_point.weights:
                        distance += (current_weights[key] - frontier_point.weights[key]) ** 2
                distance = math.sqrt(distance)

                if distance < min_distance:
                    min_distance = distance
                    nearest_point = frontier_point

            return nearest_point

    def is_dominated(self, weights: Dict[str, float], current_loss: float) -> bool:
        """Check if current weights are dominated by frontier point(s)."""
        with self._lock:
            # A configuration is dominated if there exists a frontier point with lower loss
            return any(p.loss < current_loss for p in self.frontier_points)

    def apply_rollback(
        self,
        current_weights: Dict[str, float],
        current_loss: float,
    ) -> RollbackEvent:
        """Execute rollback to nearest Pareto frontier point.

        Args:
            current_weights: Current weight configuration
            current_loss: Current loss value

        Returns:
            RollbackEvent describing the rollback

        Raises:
            RuntimeError if audit fails (fail-closed)
        """
        with self._lock:
            # Find nearest frontier point
            frontier_point = self.find_nearest_frontier_point(current_weights)
            if not frontier_point:
                raise RuntimeError(
                    f"[Divergence Detector {self.loop_id}] "
                    f"Cannot rollback: Pareto frontier is empty"
                )

            # Audit-first: log rollback BEFORE executing
            if self.audit_backend:
                audit_event = {
                    "tenant_id": self.tenant_id,
                    "event_type": "weight_rollback_executed",
                    "loop_id": self.loop_id,
                    "reason": "divergence_detected",
                    "weights_before": current_weights,
                    "weights_after": frontier_point.weights,
                    "loss_before": current_loss,
                    "loss_after": frontier_point.loss,
                    "frontier_loss": frontier_point.loss,
                }

                try:
                    self.audit_backend.write_event(audit_event)
                except Exception as e:
                    logger.error(f"[Divergence Detector {self.loop_id}] Audit failed: {e}")
                    raise RuntimeError(
                        f"[Divergence Detector {self.loop_id}] "
                        f"FATAL: audit failed; rollback NOT executed (fail-closed)."
                    )

            # Create rollback event
            event = RollbackEvent(
                timestamp=datetime.utcnow().isoformat() + "Z",
                loop_id=self.loop_id,
                reason="divergence_detected",
                weights_before=dict(current_weights),
                weights_after=dict(frontier_point.weights),
                loss_before=current_loss,
                loss_after=frontier_point.loss,
                frontier_point=frontier_point,
                learning_rate_reduction=0.5,  # Reduce learning rate by 50%
            )

            self.rollback_events.append(event)
            self._persist_rollback_event(event)

            # Reset divergence counter
            self.consecutive_divergence_count = 0
            self.post_rollback_samples = 0

            logger.warning(
                f"[Divergence Detector {self.loop_id}] "
                f"Rollback executed: loss {event.loss_before:.4f} → {event.loss_after:.4f}"
            )

            return event

    def record_post_rollback_sample(self) -> None:
        """Record that one sample has been processed after rollback (for learning rate reduction window)."""
        with self._lock:
            if self.rollback_events:  # Only count if a rollback happened
                self.post_rollback_samples += 1

    def should_restore_learning_rate(self) -> bool:
        """Check if we've completed the 50-sample reduced learning rate period."""
        with self._lock:
            return self.post_rollback_samples >= 50

    def get_statistics(self) -> Dict[str, any]:
        """Get current statistics (for diagnostics + testing)."""
        with self._lock:
            return {
                "loop_id": self.loop_id,
                "loss_history_length": len(self.loss_history),
                "windows_completed": len(self.loss_windows),
                "frontier_size": len(self.frontier_points),
                "rollback_count": len(self.rollback_events),
                "consecutive_divergence_count": self.consecutive_divergence_count,
                "post_rollback_samples": self.post_rollback_samples,
                "has_last_divergence_event": self.last_divergence_event is not None,
            }
