"""L5 Routing Correctness Tracker — Phase 2a (Dual-Write + Monitoring).

Tracks real-routed vs shadow-routed decisions to detect when the Skill-driven
routing is degrading below the bundled baseline. Part of ADR-0532 Phase 2 exit
strategy from shadow mode.

The tracking works as follows:
1. On each request, compute TWO routing decisions:
   - Real decision: use the Skill's actual output
   - Shadow decision: use the bundled routing rule
2. Record both in the audit trail (real, shadow, request_id, timestamp)
3. Check if the real decision matches the ground truth (outcome of the request)
4. Compute rolling window correctness: P(real == ground_truth | last 1000 requests)
5. If correctness drops > 2% below shadow baseline, trigger auto-rollback

Ground truth is determined post-hoc from the outcome:
- If the delegated request succeeded → real routing was correct
- If the native request succeeded but delegated failed → real might be wrong
  (but could also be a transient failure, so we use outcome confidence)
"""
from __future__ import annotations

import dataclasses
import json
import logging
import time
from collections import deque
from pathlib import Path

_log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class RoutingDecision:
    """A routing decision (real or shadow) with metadata."""

    request_id: str
    timestamp: float
    engine: str  # 'native', 'acs', 'tde'
    decision_source: str  # 'skill', 'bundled'
    confidence: float  # 0.0-1.0 (from Skill or 1.0 for bundled)
    task_type: str  # 'chat', 'big_data', 'delegate'
    tenant_id: str


@dataclasses.dataclass(frozen=True)
class RoutingOutcome:
    """Outcome of a routing decision post-execution."""

    request_id: str
    real_decision: RoutingDecision
    shadow_decision: RoutingDecision
    success: bool  # True if the delegated request succeeded
    ground_truth: str  # Which engine WAS the right choice ('native', 'acs', 'tde')
    latency_ms: float
    error_msg: str | None = None


@dataclasses.dataclass
class CorrectnessMetrics:
    """Snapshot of correctness metrics."""

    window_size: int
    correct_count: int
    total_count: int
    correctness: float  # 0.0-1.0
    shadow_correctness: float  # baseline
    skill_confidence_mean: float
    last_update_at: float


class CorrectnessTracker:
    """Tracks dual-write correctness in a rolling window."""

    def __init__(
        self,
        window_size: int = 1000,
        bootstrap_samples: int = 100,
        storage_path: Path | None = None,
    ):
        """Initialize tracker.

        Args:
            window_size: Size of the rolling correctness window (1000 per ADR-0532)
            bootstrap_samples: Number of samples before reporting metrics (100 per synthesis)
            storage_path: Optional path to persist metrics to disk (for cross-restart tracking)
        """
        self.window_size = window_size
        self.bootstrap_samples = bootstrap_samples
        self.storage_path = storage_path

        # Deques for rolling window tracking
        self._outcomes: deque[RoutingOutcome] = deque(maxlen=window_size)
        self._correct_decisions: deque[bool] = deque(maxlen=window_size)
        self._confidence_scores: deque[float] = deque(maxlen=window_size)

        # Baseline (captured at first successful sample)
        self._shadow_baseline_correctness: float | None = None
        self._samples_since_baseline: int = 0

        # State machine for rollback triggers
        self._is_above_threshold: bool = True  # starts as "OK"
        self._rollback_triggered_at: float | None = None

    def record_outcome(self, outcome: RoutingOutcome) -> None:
        """Record a routing outcome (real vs shadow).

        This is called post-execution when we know the ground truth (which engine
        was correct). It updates the rolling window and checks for rollback triggers.
        """
        is_correct = outcome.real_decision.engine == outcome.ground_truth
        self._outcomes.append(outcome)
        self._correct_decisions.append(is_correct)
        self._confidence_scores.append(outcome.real_decision.confidence)

        self._samples_since_baseline += 1

        # Capture baseline on first sample
        if self._shadow_baseline_correctness is None:
            self._shadow_baseline_correctness = float(is_correct)
            _log.info(
                "Correctness baseline established: %s (sample 1)",
                self._shadow_baseline_correctness,
            )

        # Check for rollback trigger (every 100 samples or after 1000 total samples)
        if self._samples_since_baseline % 100 == 0 or len(self._outcomes) == self.window_size:
            self._check_rollback_trigger()

        # Persist to disk if path provided
        if self.storage_path:
            self._persist_outcome(outcome)

    def _check_rollback_trigger(self) -> None:
        """Check if correctness has dropped too much; emit rollback signal if so."""
        if len(self._correct_decisions) < self.bootstrap_samples:
            _log.debug(
                "Correctness check: %d/%d bootstrap samples, skipping trigger",
                len(self._correct_decisions),
                self.bootstrap_samples,
            )
            return

        metrics = self.current_metrics()
        drop = self._shadow_baseline_correctness - metrics.correctness

        _log.info(
            "Correctness check: %.2f (baseline %.2f, drop %.2f%%, threshold 2.0%%)",
            metrics.correctness,
            self._shadow_baseline_correctness,
            drop * 100,
        )

        # ADR-0532 synthesis: auto-rollback if > 2% drop
        if drop > 0.02:  # 2% threshold from synthesis
            self._is_above_threshold = False
            self._rollback_triggered_at = time.time()
            _log.error(
                "ROLLBACK TRIGGERED: correctness dropped %.1f%% (threshold 2.0%%)",
                drop * 100,
            )

    def current_metrics(self) -> CorrectnessMetrics:
        """Get current correctness metrics snapshot."""
        if not self._correct_decisions:
            return CorrectnessMetrics(
                window_size=self.window_size,
                correct_count=0,
                total_count=0,
                correctness=0.0,
                shadow_correctness=self._shadow_baseline_correctness or 0.0,
                skill_confidence_mean=0.0,
                last_update_at=time.time(),
            )

        correct_count = sum(self._correct_decisions)
        total_count = len(self._correct_decisions)
        correctness = correct_count / total_count
        confidence_mean = sum(self._confidence_scores) / len(self._confidence_scores)

        return CorrectnessMetrics(
            window_size=self.window_size,
            correct_count=correct_count,
            total_count=total_count,
            correctness=correctness,
            shadow_correctness=self._shadow_baseline_correctness or 0.0,
            skill_confidence_mean=confidence_mean,
            last_update_at=time.time(),
        )

    def should_rollback(self) -> bool:
        """Query whether rollback was triggered."""
        return not self._is_above_threshold

    def rollback_timestamp(self) -> float | None:
        """When was rollback triggered, or None if not triggered."""
        return self._rollback_triggered_at

    def _persist_outcome(self, outcome: RoutingOutcome) -> None:
        """Persist outcome to disk for cross-restart tracking."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(
                {
                    "timestamp": outcome.timestamp,
                    "request_id": outcome.request_id,
                    "real_engine": outcome.real_decision.engine,
                    "shadow_engine": outcome.shadow_decision.engine,
                    "ground_truth": outcome.ground_truth,
                    "success": outcome.success,
                    "latency_ms": outcome.latency_ms,
                },
            )
            with open(self.storage_path, "a") as f:
                f.write(line + "\n")
        except Exception as exc:  # noqa: BLE001
            _log.warning("Failed to persist outcome: %s", exc)


def load_tracker_from_disk(storage_path: Path) -> CorrectnessTracker:
    """Reconstruct CorrectnessTracker from persisted outcomes."""
    tracker = CorrectnessTracker(storage_path=storage_path)
    if not storage_path.exists():
        return tracker

    try:
        with open(storage_path) as f:
            for line in f:
                if not line.strip():
                    continue
                data = json.loads(line)
                outcome = RoutingOutcome(
                    request_id=data["request_id"],
                    real_decision=RoutingDecision(
                        request_id=data["request_id"],
                        timestamp=data["timestamp"],
                        engine=data["real_engine"],
                        decision_source="skill",
                        confidence=1.0,  # unknown from persisted data
                        task_type="chat",  # unknown from persisted data
                        tenant_id="_default",
                    ),
                    shadow_decision=RoutingDecision(
                        request_id=data["request_id"],
                        timestamp=data["timestamp"],
                        engine=data["shadow_engine"],
                        decision_source="bundled",
                        confidence=1.0,
                        task_type="chat",
                        tenant_id="_default",
                    ),
                    success=data["success"],
                    ground_truth=data["ground_truth"],
                    latency_ms=data["latency_ms"],
                )
                tracker.record_outcome(outcome)
    except Exception as exc:  # noqa: BLE001
        _log.warning("Failed to load tracker from disk: %s", exc)

    return tracker
