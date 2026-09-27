"""Phase 2b Convergence Detector — Phase tracking + false convergence detection (ADR-0314, ADR-0722).

This module implements advanced convergence detection for Phase 2b:
1. Track convergence phases: exploration → plateau → exploitation → (optional) divergence
2. Detect false convergence: distinguish real convergence from early plateaus
3. Alert on divergence: when confidence declines after apparent convergence
4. Schedule optimizations: set next_optimization_date based on phase and velocity
5. Monitor convergence quality: track convergence stability over time

Errors: any detection error is logged, never propagates to caller.
Tenant-scoped: one detector per tenant (GDPR Art. 32).
Audit: NONE. This module keeps its state in memory only and writes nothing —
neither an audit record nor the ``convergence/`` directory it creates. (Its
docstring claimed "all events logged to EventStore"; no such call existed.)
Audited logging of these observations is ``phase2b_integration``'s job.
History lists are capped per skill (MAX_HISTORY_PER_SKILL).

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).

Phase Machine:
    exploration (n < 10) → plateau (stable 3-5d) → exploitation (n ≥ 30) → divergence (if ↓)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Literal
from pathlib import Path
from enum import Enum
import json
import threading
import statistics

logger = logging.getLogger(__name__)


class ConvergencePhase(str, Enum):
    """Convergence phase machine states."""

    EXPLORATION = "exploration"  # Collecting initial data (n < 10)
    PLATEAU = "plateau"  # Stable, no improvement (3-7 days)
    EXPLOITATION = "exploitation"  # Confident, can be aggressive (n ≥ 30, stable)
    DIVERGING = "diverging"  # Declining after plateau (recovery needed)
    CONVERGED = "converged"  # Final stable state (confidence ≥ 0.90, n ≥ 30)


@dataclass
class ConvergencePhaseTransition:
    """Record of a phase transition."""

    skill_id: str
    from_phase: ConvergencePhase
    to_phase: ConvergencePhase
    timestamp: datetime
    reason: str  # Why did the transition occur?
    confidence_at_transition: float
    n_samples_at_transition: int


@dataclass
class FalseConvergenceBoundary:
    """Boundary conditions for false convergence detection."""

    skill_id: str
    plateau_start_timestamp: datetime
    plateau_confidence: float
    min_plateau_days_for_false_positive: int = 3  # If plateau < 3d, it's just noise
    max_plateau_confidence_for_recovery: float = 0.85  # Convergence requires > 0.85
    observed_as_true: bool = False  # Did this plateau eventually resume climbing?
    resume_timestamp: Optional[datetime] = None
    false_positive: bool = False  # Marked as false convergence if resumed


@dataclass
class DivergenceAlert:
    """Alert when confidence diverges from plateau."""

    skill_id: str
    plateau_confidence: float
    current_confidence: float
    decline_percent: float  # How much did it decline? (e.g., -8.5%)
    timestamp: datetime
    severity: Literal["warning", "critical"]  # warning: -5%, critical: -10%+
    recommended_action: str  # "increase_learning_rate" | "review_parameters" | "rollback"


@dataclass
class OptimizationSchedule:
    """Scheduled optimization based on phase and velocity."""

    skill_id: str
    current_phase: ConvergencePhase
    next_optimization_date: datetime
    reason: str  # "plateau_ending" | "exploitation_ready" | "divergence_detected" | "velocity_threshold"
    estimated_cycle_time_ms: Optional[int] = None
    priority: int = 5  # 1=critical, 10=low


class ConvergenceDetector:
    """Advanced convergence detection with phase tracking and false-positive detection.

    Phase Machine:
        exploration (samples < 10)
            ↓ (after 10+ samples)
        plateau (improvement < 1% for 3+ days)
            ├→ false_positive? (if later resumes climbing)
            └→ exploitation (after 3-7 days stable)
                ├→ converged (confidence ≥ 0.90, n ≥ 30, stable 7+ days)
                └→ diverging (confidence starts declining)
    """

    # Phase thresholds
    EXPLORATION_SAMPLE_MIN = 10  # Move to plateau after N samples
    PLATEAU_STABILITY_DAYS_MIN = 3  # Must be stable for N days
    PLATEAU_STABILITY_DAYS_MAX = 7  # Can't stay in plateau > N days
    EXPLOITATION_SAMPLE_MIN = 30  # Need N samples for exploitation
    CONVERGENCE_CONFIDENCE_MIN = 0.90  # Converged if confidence ≥ this
    CONVERGENCE_PLATEAU_DAYS_MIN = 7  # Must hold for N consecutive days

    # Divergence detection
    DIVERGENCE_WARN_THRESHOLD = -0.05  # Warn if decline > 5%
    DIVERGENCE_CRITICAL_THRESHOLD = -0.10  # Critical if decline > 10%

    # False convergence detection
    FALSE_POSITIVE_RESUME_THRESHOLD = 0.02  # If resumes climbing > 2%, it was false positive
    MAX_HISTORY_PER_SKILL = 1000  # cap on transitions / alerts kept per skill

    def __init__(
        self,
        tenant_id: str,
        tenant_home: Path,
    ):
        """Initialize convergence detector.

        Args:
            tenant_id: Tenant ID (GDPR Art. 32)
            tenant_home: Root directory for tenant data

        Raises:
            ValueError: If tenant_id missing
        """
        if not tenant_id:
            raise ValueError("tenant_id required (GDPR Art. 32, fail-closed)")
        from core.tenants import validate_tenant_id  # noqa: PLC0415

        validate_tenant_id(tenant_id)

        self.tenant_id = tenant_id
        self.tenant_home = Path(tenant_home)

        # State: {skill_id → current_phase}
        self._phases: Dict[str, ConvergencePhase] = {}
        # State: {skill_id → [ConvergencePhaseTransition]}
        self._transitions: Dict[str, List[ConvergencePhaseTransition]] = {}
        # State: {skill_id → FalseConvergenceBoundary}
        self._false_positive_boundaries: Dict[str, FalseConvergenceBoundary] = {}
        # State: {skill_id → [DivergenceAlert]}
        self._divergence_alerts: Dict[str, List[DivergenceAlert]] = {}
        # State: {skill_id → OptimizationSchedule}
        self._optimization_schedules: Dict[str, OptimizationSchedule] = {}

        self._lock = threading.RLock()

        # Persistent storage
        self._convergence_dir = self.tenant_home / "global" / "learning" / "convergence"
        self._convergence_dir.mkdir(parents=True, exist_ok=True)

    def update_phase(
        self,
        skill_id: str,
        current_confidence: float,
        n_samples: int,
        trend_direction: str,
        plateau_days: int,
        timestamp: Optional[datetime] = None,
    ) -> ConvergencePhase:
        """Update phase based on current confidence and trend.

        Args:
            skill_id: Skill identifier
            current_confidence: Current confidence [0.0, 1.0]
            n_samples: Total samples collected
            trend_direction: "climbing" | "plateau" | "diverging"
            plateau_days: Days at plateau
            timestamp: Update timestamp (default: now)

        Returns:
            Current ConvergencePhase
        """
        timestamp = timestamp or datetime.now(timezone.utc)

        with self._lock:
            try:
                current_phase = self._phases.get(skill_id, ConvergencePhase.EXPLORATION)
                new_phase = self._compute_phase(
                    current_phase,
                    current_confidence,
                    n_samples,
                    trend_direction,
                    plateau_days,
                )

                # Record transition if phase changed
                if new_phase != current_phase:
                    self._record_transition(
                        skill_id,
                        current_phase,
                        new_phase,
                        current_confidence,
                        n_samples,
                        timestamp,
                    )

                    # Update schedule
                    self._schedule_optimization(skill_id, new_phase, timestamp)

                self._phases[skill_id] = new_phase

                logger.debug(
                    f"phase_updated: {skill_id}: {current_phase} → {new_phase} "
                    f"(conf={current_confidence:.3f}, n={n_samples}, plateau={plateau_days}d)"
                )

                return new_phase

            except Exception as e:
                logger.error(f"update_phase_error: {skill_id}: {e}")
                return current_phase

    def detect_false_convergence(self, skill_id: str) -> bool:
        """Check if this skill's convergence is false (early plateau that resumes climbing).

        Returns:
            True if false convergence detected
        """
        with self._lock:
            boundary = self._false_positive_boundaries.get(skill_id)
            if not boundary or not boundary.observed_as_true:
                return False

            # False convergence: was stable, then resumed climbing
            return boundary.false_positive

    def detect_divergence(
        self,
        skill_id: str,
        current_confidence: float,
        timestamp: Optional[datetime] = None,
    ) -> Optional[DivergenceAlert]:
        """Detect if confidence is diverging (declining from plateau).

        Args:
            skill_id: Skill to analyze
            current_confidence: Current confidence
            timestamp: Observation timestamp (default: now)

        Returns:
            DivergenceAlert if divergence detected, None otherwise
        """
        timestamp = timestamp or datetime.now(timezone.utc)

        with self._lock:
            try:
                # Need a recorded plateau to detect divergence
                boundary = self._false_positive_boundaries.get(skill_id)
                if not boundary or not boundary.observed_as_true:
                    return None

                plateau_conf = boundary.plateau_confidence
                decline_percent = (current_confidence - plateau_conf) / (plateau_conf + 0.001)

                # Only trigger if actually diverging
                if decline_percent >= -self.DIVERGENCE_WARN_THRESHOLD:
                    return None

                # Determine severity
                if decline_percent <= self.DIVERGENCE_CRITICAL_THRESHOLD:
                    severity = "critical"
                    recommended_action = "rollback"
                else:
                    severity = "warning"
                    recommended_action = "review_parameters"

                alert = DivergenceAlert(
                    skill_id=skill_id,
                    plateau_confidence=plateau_conf,
                    current_confidence=current_confidence,
                    decline_percent=decline_percent * 100,
                    timestamp=timestamp,
                    severity=severity,
                    recommended_action=recommended_action,
                )

                # Record alert
                if skill_id not in self._divergence_alerts:
                    self._divergence_alerts[skill_id] = []

                self._divergence_alerts[skill_id].append(alert)
                del self._divergence_alerts[skill_id][:-self.MAX_HISTORY_PER_SKILL]

                logger.warning(
                    f"divergence_detected: {skill_id}: {severity}: "
                    f"plateau={plateau_conf:.3f}, current={current_confidence:.3f}, "
                    f"decline={decline_percent*100:.1f}%, action={recommended_action}"
                )

                return alert

            except Exception as e:
                logger.error(f"detect_divergence_error: {skill_id}: {e}")
                return None

    def get_current_phase(self, skill_id: str) -> ConvergencePhase:
        """Get current phase for a skill.

        Args:
            skill_id: Skill to query

        Returns:
            Current ConvergencePhase or EXPLORATION if not tracked
        """
        with self._lock:
            return self._phases.get(skill_id, ConvergencePhase.EXPLORATION)

    def get_phase_history(self, skill_id: str) -> List[ConvergencePhaseTransition]:
        """Get phase transition history for a skill.

        Args:
            skill_id: Skill to query

        Returns:
            List of phase transitions
        """
        with self._lock:
            return self._transitions.get(skill_id, [])

    def get_next_optimization_date(self, skill_id: str) -> Optional[datetime]:
        """Get scheduled optimization date for a skill.

        Args:
            skill_id: Skill to query

        Returns:
            Next optimization datetime or None
        """
        with self._lock:
            schedule = self._optimization_schedules.get(skill_id)
            return schedule.next_optimization_date if schedule else None

    def get_divergence_alerts(
        self,
        skill_id: str,
        hours: int = 24,
    ) -> List[DivergenceAlert]:
        """Get recent divergence alerts.

        Args:
            skill_id: Skill to query
            hours: How many hours back to look

        Returns:
            List of DivergenceAlerts
        """
        with self._lock:
            alerts = self._divergence_alerts.get(skill_id, [])
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            return [a for a in alerts if a.timestamp >= cutoff]

    def record_plateau_boundary(
        self,
        skill_id: str,
        confidence: float,
        timestamp: Optional[datetime] = None,
    ) -> FalseConvergenceBoundary:
        """Record when skill enters a plateau (for false convergence detection).

        Args:
            skill_id: Skill entering plateau
            confidence: Confidence at plateau start
            timestamp: Plateau start time (default: now)

        Returns:
            FalseConvergenceBoundary record
        """
        timestamp = timestamp or datetime.now(timezone.utc)

        with self._lock:
            boundary = FalseConvergenceBoundary(
                skill_id=skill_id,
                plateau_start_timestamp=timestamp,
                plateau_confidence=confidence,
            )

            self._false_positive_boundaries[skill_id] = boundary

            logger.debug(
                f"plateau_boundary_recorded: {skill_id}: conf={confidence:.3f}, "
                f"timestamp={timestamp.isoformat()}"
            )

            return boundary

    def check_plateau_resumed(
        self,
        skill_id: str,
        new_confidence: float,
    ) -> bool:
        """Check if a plateau has resumed climbing (indicator of false convergence).

        Args:
            skill_id: Skill to check
            new_confidence: New confidence measurement

        Returns:
            True if plateau resumed climbing significantly
        """
        with self._lock:
            boundary = self._false_positive_boundaries.get(skill_id)
            if not boundary:
                return False

            plateau_conf = boundary.plateau_confidence
            improvement = (new_confidence - plateau_conf) / (plateau_conf + 0.001)

            if improvement >= self.FALSE_POSITIVE_RESUME_THRESHOLD:
                # Mark as false positive
                boundary.false_positive = True
                boundary.resume_timestamp = datetime.now(timezone.utc)
                boundary.observed_as_true = True

                logger.info(
                    f"false_convergence_detected: {skill_id}: "
                    f"resumed from {plateau_conf:.3f} to {new_confidence:.3f} "
                    f"(+{improvement*100:.1f}%)"
                )

                return True

            return False

    # Private helpers

    def _compute_phase(
        self,
        current_phase: ConvergencePhase,
        confidence: float,
        n_samples: int,
        trend_direction: str,
        plateau_days: int,
    ) -> ConvergencePhase:
        """State machine to compute next phase.

        Args:
            current_phase: Current phase
            confidence: Current confidence
            n_samples: Total samples
            trend_direction: "climbing" | "plateau" | "diverging"
            plateau_days: Days at plateau

        Returns:
            Next ConvergencePhase
        """
        # exploration → plateau
        if current_phase == ConvergencePhase.EXPLORATION:
            if n_samples >= self.EXPLORATION_SAMPLE_MIN and plateau_days >= self.PLATEAU_STABILITY_DAYS_MIN:
                return ConvergencePhase.PLATEAU
            return ConvergencePhase.EXPLORATION

        # plateau → exploitation or back to exploration
        if current_phase == ConvergencePhase.PLATEAU:
            if trend_direction == "climbing":
                return ConvergencePhase.EXPLORATION  # Reset if starting to climb again
            if (
                plateau_days >= self.PLATEAU_STABILITY_DAYS_MIN
                and n_samples >= self.EXPLOITATION_SAMPLE_MIN
            ):
                return ConvergencePhase.EXPLOITATION
            if plateau_days > self.PLATEAU_STABILITY_DAYS_MAX:
                return ConvergencePhase.EXPLOITATION  # Force move after too long
            return ConvergencePhase.PLATEAU

        # exploitation → converged or diverging
        if current_phase == ConvergencePhase.EXPLOITATION:
            if trend_direction == "diverging" and confidence < 0.85:
                return ConvergencePhase.DIVERGING
            if confidence >= self.CONVERGENCE_CONFIDENCE_MIN and plateau_days >= self.CONVERGENCE_PLATEAU_DAYS_MIN:
                return ConvergencePhase.CONVERGED
            return ConvergencePhase.EXPLOITATION

        # diverging → exploitation or exploration
        if current_phase == ConvergencePhase.DIVERGING:
            if trend_direction == "climbing":
                return ConvergencePhase.EXPLOITATION  # Recovery starting
            if confidence < 0.70:
                return ConvergencePhase.EXPLORATION  # Critical drop, restart
            return ConvergencePhase.DIVERGING

        # converged stays converged unless diverging
        if current_phase == ConvergencePhase.CONVERGED:
            if trend_direction == "diverging":
                return ConvergencePhase.DIVERGING
            return ConvergencePhase.CONVERGED

        return current_phase

    def _record_transition(
        self,
        skill_id: str,
        from_phase: ConvergencePhase,
        to_phase: ConvergencePhase,
        confidence: float,
        n_samples: int,
        timestamp: datetime,
    ) -> None:
        """Record a phase transition.

        Args:
            skill_id: Skill transitioning
            from_phase: Previous phase
            to_phase: New phase
            confidence: Confidence at transition
            n_samples: Samples at transition
            timestamp: Transition time
        """
        reason = self._transition_reason(from_phase, to_phase, n_samples)

        transition = ConvergencePhaseTransition(
            skill_id=skill_id,
            from_phase=from_phase,
            to_phase=to_phase,
            timestamp=timestamp,
            reason=reason,
            confidence_at_transition=confidence,
            n_samples_at_transition=n_samples,
        )

        if skill_id not in self._transitions:
            self._transitions[skill_id] = []

        self._transitions[skill_id].append(transition)
        del self._transitions[skill_id][:-self.MAX_HISTORY_PER_SKILL]

    def _transition_reason(
        self,
        from_phase: ConvergencePhase,
        to_phase: ConvergencePhase,
        n_samples: int,
    ) -> str:
        """Explain why a transition occurred.

        Args:
            from_phase: Previous phase
            to_phase: New phase
            n_samples: Sample count

        Returns:
            String describing reason
        """
        if from_phase == ConvergencePhase.EXPLORATION and to_phase == ConvergencePhase.PLATEAU:
            return f"reached {self.EXPLORATION_SAMPLE_MIN}+ samples"
        if from_phase == ConvergencePhase.PLATEAU and to_phase == ConvergencePhase.EXPLOITATION:
            return "stable plateau, ready for exploitation"
        if from_phase == ConvergencePhase.EXPLOITATION and to_phase == ConvergencePhase.CONVERGED:
            return f"convergence achieved (conf ≥ {self.CONVERGENCE_CONFIDENCE_MIN})"
        if from_phase == ConvergencePhase.EXPLOITATION and to_phase == ConvergencePhase.DIVERGING:
            return "confidence declining, divergence detected"
        if from_phase == ConvergencePhase.DIVERGING and to_phase == ConvergencePhase.EXPLOITATION:
            return "recovery started"
        return f"{from_phase} → {to_phase}"

    def _schedule_optimization(
        self,
        skill_id: str,
        phase: ConvergencePhase,
        timestamp: datetime,
    ) -> None:
        """Schedule next optimization based on phase.

        Args:
            skill_id: Skill to schedule
            phase: Current phase
            timestamp: Current time
        """
        if phase == ConvergencePhase.PLATEAU:
            next_date = timestamp + timedelta(days=2)  # Check for false positive soon
            reason = "plateau_ending"
        elif phase == ConvergencePhase.EXPLOITATION:
            next_date = timestamp + timedelta(days=3)  # Regular check
            reason = "exploitation_ready"
        elif phase == ConvergencePhase.CONVERGED:
            next_date = timestamp + timedelta(days=7)  # Long interval, stable
            reason = "convergence_stable"
        elif phase == ConvergencePhase.DIVERGING:
            next_date = timestamp + timedelta(hours=12)  # Urgent, check often
            reason = "divergence_detected"
        else:
            next_date = timestamp + timedelta(days=1)
            reason = "regular_check"

        schedule = OptimizationSchedule(
            skill_id=skill_id,
            current_phase=phase,
            next_optimization_date=next_date,
            reason=reason,
        )

        self._optimization_schedules[skill_id] = schedule


__all__ = [
    "ConvergenceDetector",
    "ConvergencePhase",
    "ConvergencePhaseTransition",
    "FalseConvergenceBoundary",
    "DivergenceAlert",
    "OptimizationSchedule",
]
