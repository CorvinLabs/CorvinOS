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

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).
``dual_write.record_routing_outcome`` — this tracker's only feed — has no
caller, and Phase 2 routing is refused in ``delegation_policy`` until one
exists (``_PHASE2_ROLLBACK_GUARD_WIRED``).

Baseline (2026-09-27 review): the rollback baseline used to be captured from
the FIRST sample alone (0.0 or 1.0), so one early failure made the gate
un-triggerable and one early success made any single miss look like a
collapse. It is now the correctness of the first ``bootstrap_samples``
outcomes, and the drop is measured over the outcomes recorded AFTER it —
never before ``bootstrap_samples`` of them exist.
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

        # Baseline: correctness of the first ``bootstrap_samples`` outcomes.
        # (The attribute keeps its historical name; it is the dashboard's
        # ``shadow_correctness`` field.)
        self._shadow_baseline_correctness: float | None = None
        self._baseline_samples: list[bool] = []
        self._post_baseline: deque[bool] = deque(maxlen=window_size)
        self._samples_since_baseline: int = 0

        # State machine for rollback triggers
        self._is_above_threshold: bool = True  # starts as "OK"
        self._rollback_triggered_at: float | None = None

    def record_outcome(self, outcome: RoutingOutcome) -> None:
        """Record a routing outcome (real vs shadow).

        This is called post-execution when we know the ground truth (which engine
        was correct). A decision counts as correct only when it picked the
        ground-truth engine AND the request succeeded.
        """
        is_correct = bool(outcome.success) and outcome.real_decision.engine == outcome.ground_truth
        self._outcomes.append(outcome)
        self._correct_decisions.append(is_correct)
        self._confidence_scores.append(outcome.real_decision.confidence)

        if self._shadow_baseline_correctness is None:
            self._baseline_samples.append(is_correct)
            if len(self._baseline_samples) >= max(1, self.bootstrap_samples):
                self._shadow_baseline_correctness = (
                    sum(self._baseline_samples) / len(self._baseline_samples)
                )
                _log.info(
                    "Correctness baseline established: %.3f over %d samples",
                    self._shadow_baseline_correctness,
                    len(self._baseline_samples),
                )
        else:
            self._post_baseline.append(is_correct)
            self._samples_since_baseline += 1
            self._check_rollback_trigger()

        # Persist to disk if path provided
        if self.storage_path:
            self._persist_outcome(outcome)

    def _check_rollback_trigger(self) -> None:
        """Compare post-baseline correctness with the baseline; flag a >2% drop.

        Evaluated only once at least ``bootstrap_samples`` outcomes exist on
        BOTH sides of the baseline, so a single sample can never trip it.
        """
        if self._shadow_baseline_correctness is None:
            return
        if len(self._post_baseline) < max(1, self.bootstrap_samples):
            return

        recent = self.recent_correctness()
        drop = self._shadow_baseline_correctness - recent

        _log.debug(
            "Correctness check: %.3f (baseline %.3f, drop %.2f%%, threshold 2.0%%)",
            recent,
            self._shadow_baseline_correctness,
            drop * 100,
        )

        # ADR-0532 synthesis: auto-rollback if > 2% drop
        if drop > 0.02 + 1e-9 and self._is_above_threshold:  # epsilon: 1.0-0.98 > 0.02 in floats
            self._is_above_threshold = False
            self._rollback_triggered_at = time.time()
            _log.error(
                "ROLLBACK TRIGGERED: correctness dropped %.1f%% (threshold 2.0%%)",
                drop * 100,
            )

    def recent_correctness(self) -> float | None:
        """Correctness over the outcomes recorded after the baseline (or None)."""
        if not self._post_baseline:
            return None
        return sum(self._post_baseline) / len(self._post_baseline)

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
                    # RoutingOutcome carries no timestamp of its own; reading
                    # ``outcome.timestamp`` raised AttributeError on every
                    # write, so nothing was ever persisted.
                    "timestamp": outcome.real_decision.timestamp,
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
    """Reconstruct CorrectnessTracker from persisted outcomes.

    Replays WITHOUT a storage path and attaches it afterwards: replaying through
    ``record_outcome`` with the path set appended every loaded line to the same
    file again, doubling it on each load.
    """
    tracker = CorrectnessTracker(storage_path=None)
    if not storage_path.exists():
        tracker.storage_path = storage_path
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

    tracker.storage_path = storage_path
    return tracker
