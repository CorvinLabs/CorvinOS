"""Phase 2b Learning Loop Optimizer — Skill-primary switch when confidence > 0.75 (ADR-0314, ADR-0532).

This module implements automated learning optimization for Phase 2b:
1. Monitor confidence trends via 7-day rolling average
2. Detect convergence plateaus (confidence > 0.90 for 7+ consecutive days)
3. Trigger parameter optimization when feedback signals indicate improvement potential
4. Auto-adjust confidence thresholds based on learning velocity
5. Track learning velocity: time from feedback signal to parameter adjustment

Fail-closed: any optimization error is logged, never propagates to caller.
Tenant-scoped: all operations filtered by tenant_id (GDPR Art. 32).
Audit-first: all events logged via EventStore (ADR-0314).

Phase 2b Threshold: skill_primary switch when confidence > 0.75 (replaces fallback routing).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List
from pathlib import Path
from uuid import uuid4
import json
import threading
import statistics

logger = logging.getLogger(__name__)


@dataclass
class ConfidenceTrend:
    """7-day rolling confidence trend snapshot."""

    skill_id: str
    current_confidence: float  # Most recent confidence score [0.0, 1.0]
    rolling_avg_7day: float  # 7-day rolling average
    rolling_min_7day: float  # Minimum over last 7 days
    rolling_max_7day: float  # Maximum over last 7 days
    variance_7day: float  # Variance over 7-day window
    n_samples: int  # Number of samples in window
    plateau_days: int  # Days at plateau (no improvement > 1%)
    trend_direction: str  # "climbing" | "plateau" | "diverging"
    last_update: datetime
    phase_2b_eligible: bool = False  # True if confident enough for Phase 2b


@dataclass
class ParameterDelta:
    """Tracked parameter optimization."""

    skill_id: str
    param_name: str  # e.g., "confidence_threshold", "learning_rate"
    old_value: float
    new_value: float
    reason: str  # "convergence_detected" | "feedback_signal" | "velocity_improvement"
    confidence_boost: Optional[float] = None  # Expected confidence gain
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class LearningVelocity:
    """Learning velocity metric: feedback → parameter adjustment cycle time."""

    skill_id: str
    feedback_timestamp: datetime
    adjustment_timestamp: Optional[datetime]
    cycle_time_ms: Optional[int] = None  # Time from feedback to adjustment
    feedback_signal_strength: float = 0.5  # [0.0, 1.0] how strong was the feedback?
    adjustment_triggered: bool = False


class Phase2bOptimizer:
    """Automated optimizer for Phase 2b skill-primary routing (confidence > 0.75).

    Monitors confidence trends, detects convergence, triggers optimizations, and
    auto-adjusts thresholds based on learning velocity.

    Phase 2b Switch Logic:
        if skill_confidence > 0.75:
            route_to_skill_primary()  # Not a fallback, primary path
        else:
            route_to_fallback()       # Original behavior
    """

    # Phase 2b configuration
    PHASE_2B_CONFIDENCE_THRESHOLD = 0.75  # Switch point for skill-primary routing
    CONVERGENCE_PLATEAU_THRESHOLD = 0.90  # Converged if confidence ≥ 0.90
    CONVERGENCE_PLATEAU_DAYS = 7  # Must hold for 7+ consecutive days
    ROLLING_WINDOW_DAYS = 7
    IMPROVEMENT_THRESHOLD = 0.01  # 1% improvement = non-plateau

    # Optimization triggers
    FEEDBACK_VELOCITY_THRESHOLD_MS = 3600000  # 1 hour: feedback → adjustment cycle
    FEEDBACK_SIGNAL_MIN_STRENGTH = 0.6  # Only act on strong signals (≥ 0.6)

    # Auto-threshold adjustment
    THRESHOLD_ADJUST_DELTA = 0.02  # Max adjustment per optimization (±2%)
    THRESHOLD_MIN = 0.50  # Never lower threshold below 50%
    THRESHOLD_MAX = 0.95  # Never raise threshold above 95%

    def __init__(
        self,
        tenant_id: str,
        tenant_home: Path,
        observation_window_days: int = 7,
        convergence_threshold: float = CONVERGENCE_PLATEAU_THRESHOLD,
    ):
        """Initialize optimizer.

        Args:
            tenant_id: Tenant ID (GDPR Art. 32)
            tenant_home: Root directory for tenant data
            observation_window_days: Rolling window for trend analysis
            convergence_threshold: Confidence level considered converged

        Raises:
            ValueError: If tenant_id missing or invalid params
        """
        if not tenant_id:
            raise ValueError("tenant_id required (GDPR Art. 32, fail-closed)")
        if observation_window_days < 3:
            raise ValueError("observation_window_days must be ≥3")
        if not (0.5 <= convergence_threshold <= 1.0):
            raise ValueError("convergence_threshold must be ∈ [0.5, 1.0]")

        self.tenant_id = tenant_id
        self.tenant_home = Path(tenant_home)
        self.observation_window_days = observation_window_days
        self.convergence_threshold = convergence_threshold

        # State: {skill_id → ConfidenceTrend}
        self._trends: Dict[str, ConfidenceTrend] = {}
        # State: {skill_id → [ParameterDelta]}
        self._param_deltas: Dict[str, List[ParameterDelta]] = {}
        # State: {skill_id → [LearningVelocity]}
        self._velocities: Dict[str, List[LearningVelocity]] = {}
        # Thresholds per skill: {skill_id → current_threshold}
        self._confidence_thresholds: Dict[str, float] = {}

        self._lock = threading.RLock()
        self._boot_time = datetime.now(timezone.utc)

        # Persistent storage for trends/velocities
        self._trends_dir = self.tenant_home / "global" / "learning" / "phase2b_trends"
        self._trends_dir.mkdir(parents=True, exist_ok=True)

    def record_confidence_score(
        self,
        skill_id: str,
        confidence: float,
        timestamp: Optional[datetime] = None,
    ) -> ConfidenceTrend:
        """Record a confidence score for a skill.

        Args:
            skill_id: Skill identifier
            confidence: Confidence score [0.0, 1.0]
            timestamp: Observation timestamp (default: now)

        Returns:
            Updated ConfidenceTrend

        Raises:
            ValueError: On validation failure (fail-closed)
        """
        if not skill_id or not isinstance(skill_id, str):
            raise ValueError("skill_id required")
        if not (0.0 <= confidence <= 1.0):
            raise ValueError(f"confidence must be ∈ [0.0, 1.0], got {confidence}")

        timestamp = timestamp or datetime.now(timezone.utc)

        with self._lock:
            try:
                # Initialize trend if first observation
                if skill_id not in self._trends:
                    self._trends[skill_id] = ConfidenceTrend(
                        skill_id=skill_id,
                        current_confidence=confidence,
                        rolling_avg_7day=confidence,
                        rolling_min_7day=confidence,
                        rolling_max_7day=confidence,
                        variance_7day=0.0,
                        n_samples=1,
                        plateau_days=0,
                        trend_direction="climbing",
                        last_update=timestamp,
                        phase_2b_eligible=confidence > self.PHASE_2B_CONFIDENCE_THRESHOLD,
                    )
                    self._confidence_thresholds[skill_id] = self.PHASE_2B_CONFIDENCE_THRESHOLD
                    return self._trends[skill_id]

                # Load historical data (for rolling avg/min/max/variance)
                historical = self._load_historical_confidence(skill_id, days=self.observation_window_days)
                historical.append(confidence)

                # Keep only last N days
                cutoff = timestamp - timedelta(days=self.observation_window_days)
                recent = [c for c in historical if c is not None]
                if len(recent) < 2:
                    recent = [confidence]

                # Compute 7-day statistics
                rolling_avg = statistics.mean(recent) if recent else confidence
                rolling_min = min(recent) if recent else confidence
                rolling_max = max(recent) if recent else confidence
                variance = (
                    statistics.variance(recent)
                    if len(recent) >= 2
                    else 0.0
                )

                # Detect trend direction
                trend_direction = self._detect_trend_direction(recent)

                # Detect plateau
                plateau_days = self._detect_plateau(recent, confidence)

                # Check Phase 2b eligibility
                phase_2b_eligible = rolling_avg > self.PHASE_2B_CONFIDENCE_THRESHOLD

                # Update trend
                updated_trend = ConfidenceTrend(
                    skill_id=skill_id,
                    current_confidence=confidence,
                    rolling_avg_7day=rolling_avg,
                    rolling_min_7day=rolling_min,
                    rolling_max_7day=rolling_max,
                    variance_7day=variance,
                    n_samples=len(recent),
                    plateau_days=plateau_days,
                    trend_direction=trend_direction,
                    last_update=timestamp,
                    phase_2b_eligible=phase_2b_eligible,
                )

                self._trends[skill_id] = updated_trend

                # Persist to disk
                self._save_trend(updated_trend)

                logger.debug(
                    f"confidence_recorded: {skill_id}: conf={confidence:.3f}, "
                    f"trend={trend_direction}, plateau={plateau_days}d, "
                    f"phase2b_eligible={phase_2b_eligible}"
                )

                return updated_trend

            except Exception as e:
                logger.error(f"record_confidence_score_error: {skill_id}: {e}")
                raise

    def detect_convergence(self, skill_id: str) -> bool:
        """Check if skill has converged (confidence plateau at high level).

        Returns:
            True if convergence detected (confidence ≥ 0.90 for 7+ days)
        """
        with self._lock:
            trend = self._trends.get(skill_id)
            if not trend:
                return False

            converged = (
                trend.rolling_avg_7day >= self.convergence_threshold
                and trend.plateau_days >= self.CONVERGENCE_PLATEAU_DAYS
            )

            if converged:
                logger.info(
                    f"convergence_detected: {skill_id}: "
                    f"confidence={trend.rolling_avg_7day:.3f}, "
                    f"plateau={trend.plateau_days}d"
                )

            return converged

    def detect_divergence(self, skill_id: str) -> bool:
        """Check if skill is diverging (confidence should rise, but declining).

        Returns:
            True if divergence detected (downward trend after plateau)
        """
        with self._lock:
            trend = self._trends.get(skill_id)
            if not trend:
                return False

            # Divergence: was plateaued, now declining
            diverging = (
                trend.plateau_days >= self.CONVERGENCE_PLATEAU_DAYS
                and trend.trend_direction == "diverging"
                and trend.current_confidence < trend.rolling_avg_7day * 0.95
            )

            if diverging:
                logger.warning(
                    f"divergence_detected: {skill_id}: "
                    f"was_plateau={trend.plateau_days}d, "
                    f"current={trend.current_confidence:.3f}, "
                    f"avg={trend.rolling_avg_7day:.3f}"
                )

            return diverging

    def trigger_optimization(
        self,
        skill_id: str,
        reason: str,
        feedback_signal_strength: float = 0.75,
    ) -> Optional[ParameterDelta]:
        """Trigger parameter optimization for a skill.

        Args:
            skill_id: Skill to optimize
            reason: Why optimization was triggered
            feedback_signal_strength: How strong is the feedback? [0.0, 1.0]

        Returns:
            ParameterDelta if optimization was applied, None otherwise
        """
        with self._lock:
            try:
                if skill_id not in self._trends:
                    logger.warning(f"trigger_optimization: no trend for {skill_id}")
                    return None

                if feedback_signal_strength < self.FEEDBACK_SIGNAL_MIN_STRENGTH:
                    logger.debug(
                        f"trigger_optimization: feedback too weak: {skill_id}: "
                        f"strength={feedback_signal_strength:.2f} < {self.FEEDBACK_SIGNAL_MIN_STRENGTH}"
                    )
                    return None

                trend = self._trends[skill_id]

                # Decide parameter to adjust
                if reason == "convergence_detected":
                    # Convergence: can be more aggressive (raise threshold slightly)
                    old_threshold = self._confidence_thresholds.get(skill_id, self.PHASE_2B_CONFIDENCE_THRESHOLD)
                    new_threshold = min(
                        old_threshold + self.THRESHOLD_ADJUST_DELTA,
                        self.THRESHOLD_MAX,
                    )

                    delta = ParameterDelta(
                        skill_id=skill_id,
                        param_name="confidence_threshold",
                        old_value=old_threshold,
                        new_value=new_threshold,
                        reason=reason,
                        confidence_boost=0.02,
                    )

                    self._confidence_thresholds[skill_id] = new_threshold

                elif reason == "velocity_improvement":
                    # Learning velocity improved: lower threshold to encourage use
                    old_threshold = self._confidence_thresholds.get(skill_id, self.PHASE_2B_CONFIDENCE_THRESHOLD)
                    new_threshold = max(
                        old_threshold - self.THRESHOLD_ADJUST_DELTA,
                        self.THRESHOLD_MIN,
                    )

                    delta = ParameterDelta(
                        skill_id=skill_id,
                        param_name="confidence_threshold",
                        old_value=old_threshold,
                        new_value=new_threshold,
                        reason=reason,
                        confidence_boost=-0.02,
                    )

                    self._confidence_thresholds[skill_id] = new_threshold

                elif reason == "feedback_signal":
                    # Generic feedback-driven optimization
                    old_threshold = self._confidence_thresholds.get(skill_id, self.PHASE_2B_CONFIDENCE_THRESHOLD)
                    delta_adjustment = self.THRESHOLD_ADJUST_DELTA if trend.trend_direction == "climbing" else -self.THRESHOLD_ADJUST_DELTA

                    new_threshold = max(
                        self.THRESHOLD_MIN,
                        min(self.THRESHOLD_MAX, old_threshold + delta_adjustment),
                    )

                    delta = ParameterDelta(
                        skill_id=skill_id,
                        param_name="confidence_threshold",
                        old_value=old_threshold,
                        new_value=new_threshold,
                        reason=reason,
                        confidence_boost=delta_adjustment * 0.1,
                    )

                    self._confidence_thresholds[skill_id] = new_threshold

                else:
                    logger.warning(f"trigger_optimization: unknown reason: {reason}")
                    return None

                # Record optimization
                if skill_id not in self._param_deltas:
                    self._param_deltas[skill_id] = []

                self._param_deltas[skill_id].append(delta)

                # Track velocity if we have pending feedback
                self._record_adjustment(skill_id, delta)

                logger.info(
                    f"optimization_triggered: {skill_id}: {reason}: "
                    f"{delta.param_name}={delta.old_value:.3f} → {delta.new_value:.3f}"
                )

                return delta

            except Exception as e:
                logger.error(f"trigger_optimization_error: {skill_id}: {e}")
                return None

    def record_feedback(
        self,
        skill_id: str,
        signal_strength: float,
        timestamp: Optional[datetime] = None,
    ) -> LearningVelocity:
        """Record feedback signal for velocity tracking.

        Args:
            skill_id: Skill receiving feedback
            signal_strength: Feedback strength [0.0, 1.0]
            timestamp: Feedback timestamp (default: now)

        Returns:
            LearningVelocity record
        """
        timestamp = timestamp or datetime.now(timezone.utc)

        with self._lock:
            if skill_id not in self._velocities:
                self._velocities[skill_id] = []

            velocity = LearningVelocity(
                skill_id=skill_id,
                feedback_timestamp=timestamp,
                adjustment_timestamp=None,
                feedback_signal_strength=signal_strength,
            )

            self._velocities[skill_id].append(velocity)

            logger.debug(
                f"feedback_recorded: {skill_id}: strength={signal_strength:.2f}"
            )

            return velocity

    def get_learning_velocity(self, skill_id: str, window_hours: int = 24) -> Optional[float]:
        """Compute average feedback → adjustment cycle time.

        Args:
            skill_id: Skill to analyze
            window_hours: Time window to consider

        Returns:
            Average cycle time in milliseconds, or None if no data
        """
        with self._lock:
            velocities = self._velocities.get(skill_id, [])
            if not velocities:
                return None

            cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
            recent = [v for v in velocities if v.feedback_timestamp >= cutoff and v.cycle_time_ms is not None]

            if not recent:
                return None

            return statistics.mean(v.cycle_time_ms for v in recent)

    def get_confidence_trend(self, skill_id: str) -> Optional[ConfidenceTrend]:
        """Get current confidence trend for a skill.

        Args:
            skill_id: Skill to query

        Returns:
            ConfidenceTrend or None if no data
        """
        with self._lock:
            return self._trends.get(skill_id)

    def get_phase_2b_eligible_skills(self) -> List[str]:
        """Get all skills eligible for Phase 2b routing (confidence > 0.75).

        Returns:
            List of skill IDs with confidence > 0.75
        """
        with self._lock:
            return [
                skill_id
                for skill_id, trend in self._trends.items()
                if trend.phase_2b_eligible
            ]

    # Private helpers

    def _load_historical_confidence(self, skill_id: str, days: int = 7) -> List[float]:
        """Load historical confidence scores from disk.

        Args:
            skill_id: Skill to load
            days: How many days back to load

        Returns:
            List of confidence values (may be sparse)
        """
        try:
            trend_file = self._trends_dir / f"{skill_id}_trend.json"
            if not trend_file.exists():
                return []

            with open(trend_file, "r") as f:
                data = json.load(f)

            # Extract historical samples
            samples = data.get("samples", [])
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)

            historical = []
            for sample in samples:
                ts = datetime.fromisoformat(sample["timestamp"].replace("Z", "+00:00"))
                if ts >= cutoff:
                    historical.append(sample["confidence"])

            return historical

        except Exception as e:
            logger.error(f"load_historical_confidence_error: {skill_id}: {e}")
            return []

    def _save_trend(self, trend: ConfidenceTrend) -> None:
        """Persist trend to disk.

        Args:
            trend: ConfidenceTrend to save
        """
        try:
            trend_file = self._trends_dir / f"{trend.skill_id}_trend.json"

            # Load existing data to append sample
            data = {"skill_id": trend.skill_id, "samples": []}
            if trend_file.exists():
                with open(trend_file, "r") as f:
                    data = json.load(f)

            # Append new sample
            data["samples"].append({
                "confidence": trend.current_confidence,
                "timestamp": trend.last_update.isoformat().replace("+00:00", "Z"),
            })

            # Keep only last 30 days
            cutoff = datetime.now(timezone.utc) - timedelta(days=30)
            data["samples"] = [
                s for s in data["samples"]
                if datetime.fromisoformat(s["timestamp"].replace("Z", "+00:00")) >= cutoff
            ]

            with open(trend_file, "w") as f:
                json.dump(data, f, indent=2)

        except Exception as e:
            logger.error(f"save_trend_error: {trend.skill_id}: {e}")

    def _detect_trend_direction(self, recent_samples: List[float]) -> str:
        """Detect whether confidence is climbing, plateau, or diverging.

        Args:
            recent_samples: Recent confidence values

        Returns:
            "climbing" | "plateau" | "diverging"
        """
        if len(recent_samples) < 2:
            return "climbing"

        # Compare first half vs second half
        mid = len(recent_samples) // 2
        if mid < 1:
            mid = 1

        first_half_avg = statistics.mean(recent_samples[:mid])
        second_half_avg = statistics.mean(recent_samples[mid:])

        improvement = (second_half_avg - first_half_avg) / (first_half_avg + 0.001)

        if improvement > self.IMPROVEMENT_THRESHOLD:
            return "climbing"
        elif improvement < -self.IMPROVEMENT_THRESHOLD:
            return "diverging"
        else:
            return "plateau"

    def _detect_plateau(self, recent_samples: List[float], current: float) -> int:
        """Count how many days/samples confidence has been at plateau.

        Args:
            recent_samples: Recent confidence values
            current: Current confidence

        Returns:
            Number of consecutive days at plateau
        """
        if len(recent_samples) < 2:
            return 0

        plateau_count = 0
        for sample in reversed(recent_samples[-7:]):  # Look back 7 days max
            improvement = (current - sample) / (sample + 0.001)
            if abs(improvement) < self.IMPROVEMENT_THRESHOLD:
                plateau_count += 1
            else:
                break

        return plateau_count

    def _record_adjustment(self, skill_id: str, delta: ParameterDelta) -> None:
        """Record adjustment timestamp for velocity tracking.

        Args:
            skill_id: Skill being adjusted
            delta: Parameter delta applied
        """
        try:
            velocities = self._velocities.get(skill_id, [])
            if not velocities:
                return

            # Find most recent feedback
            pending = [v for v in reversed(velocities) if not v.adjustment_triggered]
            if pending:
                latest_feedback = pending[0]
                cycle_time_ms = int(
                    (delta.timestamp - latest_feedback.feedback_timestamp).total_seconds() * 1000
                )
                latest_feedback.adjustment_timestamp = delta.timestamp
                latest_feedback.cycle_time_ms = cycle_time_ms
                latest_feedback.adjustment_triggered = True

                logger.debug(
                    f"velocity_recorded: {skill_id}: "
                    f"cycle_time={cycle_time_ms}ms"
                )

        except Exception as e:
            logger.error(f"record_adjustment_error: {skill_id}: {e}")


__all__ = [
    "Phase2bOptimizer",
    "ConfidenceTrend",
    "ParameterDelta",
    "LearningVelocity",
]
