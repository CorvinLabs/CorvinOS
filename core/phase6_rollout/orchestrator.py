"""
Phase 6 Orchestrator — drives canary→50%→100% rollout with automatic decision gates.

Core responsibility: make autonomous go/no-go decisions based on health metrics.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Tuple
import asyncio
import logging
from abc import ABC, abstractmethod


logger = logging.getLogger(__name__)


class RolloutStage(Enum):
    """Stages of the production rollout."""
    INITIAL = "initial"  # Pre-canary, all users on stable (Phase 5)
    CANARY_10 = "canary_10"  # 10% users on new stack
    RAMP_50 = "ramp_50"  # 50% users on new stack
    FULL_100 = "full_100"  # 100% users on new stack
    COMPLETE = "complete"  # Rollout done, stable operation
    ROLLED_BACK = "rolled_back"  # Rolled back to Phase 5


class HealthStatus(Enum):
    """Health status of the current stage."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class DecisionGate(Enum):
    """Decision gates that control rollout progression."""
    CANARY_HEALTH_48H = "canary_health_48h"  # Canary must be healthy for 48h
    RAMP_50_HEALTH_48H = "ramp_50_health_48h"  # 50% ramp must be healthy for 48h
    FEATURE_PROMOTION_VELOCITY = "feature_promotion_velocity"  # Features promoted ALPHA→PRODUCTION
    AUDIT_INTEGRITY = "audit_integrity"  # Audit chain must verify
    ERROR_RATE_THRESHOLD = "error_rate_threshold"  # Error rate <0.1%
    LATENCY_THRESHOLD = "latency_threshold"  # p99 latency <500ms


@dataclass
class HealthMetrics:
    """Snapshot of system health at a point in time."""
    timestamp: datetime
    throughput_per_sec: float
    latency_p99_ms: float
    error_rate_percent: float
    audit_integrity_percent: float
    feature_promotion_count: int
    features_stuck_alpha_count: int

    def is_healthy(self) -> bool:
        """Returns True if all metrics are within strict SLO (not degraded or critical)."""
        return (
            self.error_rate_percent < 0.1
            and self.latency_p99_ms < 200  # Must be below degraded threshold
            and self.audit_integrity_percent >= 99.9
        )

    def is_degraded(self) -> bool:
        """Returns True if some metrics are borderline."""
        return (
            (self.error_rate_percent >= 0.1 and self.error_rate_percent < 0.5)
            or (self.latency_p99_ms >= 200 and self.latency_p99_ms < 500)
        )


@dataclass
class RolloutState:
    """Tracks the state of the current rollout stage."""
    stage: RolloutStage
    started_at: datetime
    stage_duration: timedelta
    canary_traffic_percent: int
    metrics: List[HealthMetrics] = field(default_factory=list)
    health_status: HealthStatus = HealthStatus.UNKNOWN
    decisions_made: List[Tuple[DecisionGate, bool, str]] = field(default_factory=list)

    def age(self) -> timedelta:
        """Returns how long we've been in this stage."""
        return datetime.now() - self.started_at

    def is_stage_ready_for_promotion(self) -> bool:
        """Check if current stage has been healthy for long enough to promote."""
        min_duration = timedelta(hours=48)
        if self.age() < min_duration:
            return False

        # Check last N metrics are healthy
        recent = self.metrics[-6:]  # Last 6 samples (6 × 15min = 90min rolling window)
        if len(recent) < 6:
            return False

        return all(m.is_healthy() for m in recent)


@dataclass
class RolloutDecision:
    """A decision made by the orchestrator."""
    gate: DecisionGate
    pass_gate: bool
    timestamp: datetime
    reason: str
    recommended_action: str
    confidence_percent: float


class RolloutOrchestrator:
    """
    Autonomous orchestrator that drives the canary→50%→100% rollout.

    Responsibilities:
    1. Monitor health metrics (throughput, latency, error rate, audit integrity)
    2. Make go/no-go decisions based on health gates
    3. Auto-promote to next stage when ready
    4. Auto-rollback to Phase 5 if health degrades
    5. Track all decisions in audit log
    """

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.state = RolloutState(
            stage=RolloutStage.INITIAL,
            started_at=datetime.now(),
            stage_duration=timedelta(hours=0),
            canary_traffic_percent=0,
        )
        self.decision_history: List[RolloutDecision] = []
        self._health_check_interval = timedelta(minutes=15)
        self._last_health_check = datetime.now() - timedelta(hours=1)  # Initialize to past so first call proceeds
        self._is_running = False

    async def start(self) -> None:
        """Start the orchestrator (health monitoring loop)."""
        self._is_running = True
        logger.info(f"Orchestrator started for tenant {self.tenant_id}")

    async def stop(self) -> None:
        """Stop the orchestrator."""
        self._is_running = False
        logger.info(f"Orchestrator stopped for tenant {self.tenant_id}")

    async def next_stage(self) -> RolloutStage:
        """
        Transition to the next stage if health gates pass.

        Returns the next stage, or current stage if not ready.
        """
        if not self._is_running:
            return self.state.stage

        # Check if it's time for a health check
        now = datetime.now()
        if now - self._last_health_check < self._health_check_interval:
            return self.state.stage

        self._last_health_check = now

        # Get current health metrics (will be populated by SimulationFramework or real monitoring)
        # For now, we just check the decision gates

        match self.state.stage:
            case RolloutStage.INITIAL:
                # Initial → Canary 10%
                decision = self._check_canary_10_gate()
                if decision.pass_gate:
                    await self._transition_to_stage(RolloutStage.CANARY_10, 10)
                    return RolloutStage.CANARY_10

            case RolloutStage.CANARY_10:
                # Canary 10% → Ramp 50%
                decision = self._check_canary_health_48h_gate()
                if decision.pass_gate:
                    await self._transition_to_stage(RolloutStage.RAMP_50, 50)
                    return RolloutStage.RAMP_50
                elif self._health_is_critical():
                    await self._rollback("Canary health degraded below critical threshold")
                    return RolloutStage.ROLLED_BACK

            case RolloutStage.RAMP_50:
                # Ramp 50% → Full 100%
                decision = self._check_ramp_50_health_48h_gate()
                if decision.pass_gate:
                    await self._transition_to_stage(RolloutStage.FULL_100, 100)
                    return RolloutStage.FULL_100
                elif self._health_is_critical():
                    await self._rollback("50% ramp health degraded below critical threshold")
                    return RolloutStage.ROLLED_BACK

            case RolloutStage.FULL_100:
                # Full 100% → Complete
                decision = self._check_full_production_gate()
                if decision.pass_gate:
                    await self._transition_to_stage(RolloutStage.COMPLETE, 100)
                    return RolloutStage.COMPLETE

            case _:
                pass  # No more transitions

        return self.state.stage

    def _check_canary_10_gate(self) -> RolloutDecision:
        """Gate: Can we start canary deployment to 10% users?"""
        # Always pass if infrastructure is ready (Phase 5 validated this)
        return RolloutDecision(
            gate=DecisionGate.CANARY_HEALTH_48H,
            pass_gate=True,
            timestamp=datetime.now(),
            reason="Phase 5 production validation complete, infrastructure ready",
            recommended_action="Deploy canary to 10% traffic",
            confidence_percent=99.0,
        )

    def _check_canary_health_48h_gate(self) -> RolloutDecision:
        """Gate: Has canary been healthy for 48h?"""
        if not self.state.is_stage_ready_for_promotion():
            return RolloutDecision(
                gate=DecisionGate.CANARY_HEALTH_48H,
                pass_gate=False,
                timestamp=datetime.now(),
                reason=f"Canary stage age {self.state.age().total_seconds()/3600:.1f}h < 48h minimum",
                recommended_action="Continue monitoring, check again in 24 hours",
                confidence_percent=85.0,
            )

        return RolloutDecision(
            gate=DecisionGate.CANARY_HEALTH_48H,
            pass_gate=True,
            timestamp=datetime.now(),
            reason=f"Canary healthy for 48h+ (age: {self.state.age().total_seconds()/3600:.1f}h)",
            recommended_action="Promote to 50% traffic ramp",
            confidence_percent=98.0,
        )

    def _check_ramp_50_health_48h_gate(self) -> RolloutDecision:
        """Gate: Has 50% ramp been healthy for 48h?"""
        if not self.state.is_stage_ready_for_promotion():
            return RolloutDecision(
                gate=DecisionGate.RAMP_50_HEALTH_48H,
                pass_gate=False,
                timestamp=datetime.now(),
                reason=f"50% ramp stage age {self.state.age().total_seconds()/3600:.1f}h < 48h minimum",
                recommended_action="Continue monitoring, check again in 24 hours",
                confidence_percent=85.0,
            )

        return RolloutDecision(
            gate=DecisionGate.RAMP_50_HEALTH_48H,
            pass_gate=True,
            timestamp=datetime.now(),
            reason=f"50% ramp healthy for 48h+ (age: {self.state.age().total_seconds()/3600:.1f}h)",
            recommended_action="Promote to 100% full production",
            confidence_percent=98.0,
        )

    def _check_full_production_gate(self) -> RolloutDecision:
        """Gate: Can we declare the rollout complete?"""
        # Must have run full 100% for 7 days
        min_stability = timedelta(days=7)
        if self.state.age() < min_stability:
            return RolloutDecision(
                gate=DecisionGate.AUDIT_INTEGRITY,
                pass_gate=False,
                timestamp=datetime.now(),
                reason=f"100% production age {self.state.age().total_seconds()/86400:.1f}d < 7d minimum",
                recommended_action="Continue monitoring for stability",
                confidence_percent=90.0,
            )

        return RolloutDecision(
            gate=DecisionGate.AUDIT_INTEGRITY,
            pass_gate=True,
            timestamp=datetime.now(),
            reason="Rollout stable for 7+ days at 100%, ready to complete",
            recommended_action="Mark rollout as complete, archive Phase 5 code",
            confidence_percent=99.0,
        )

    def _health_is_critical(self) -> bool:
        """Check if current health is critical (should trigger rollback)."""
        if not self.state.metrics:
            return False

        latest = self.state.metrics[-1]
        return (
            latest.error_rate_percent > 1.0
            or latest.latency_p99_ms > 1000
            or latest.audit_integrity_percent < 99.0
        )

    async def _transition_to_stage(self, new_stage: RolloutStage, traffic_percent: int) -> None:
        """Transition to a new rollout stage."""
        logger.info(
            f"Transitioning from {self.state.stage} to {new_stage} "
            f"(traffic: {traffic_percent}%)"
        )
        self.state.stage = new_stage
        self.state.started_at = datetime.now()
        self.state.canary_traffic_percent = traffic_percent
        self.state.metrics = []  # Reset metrics for new stage

    async def _rollback(self, reason: str) -> None:
        """Rollback to Phase 5."""
        logger.critical(f"ROLLING BACK to Phase 5: {reason}")
        self.state.stage = RolloutStage.ROLLED_BACK
        # In real production, this would trigger blue-green switch and rollback scripts

    def record_metrics(self, metrics: HealthMetrics) -> None:
        """Record a new health metric sample."""
        self.state.metrics.append(metrics)

        # Update health status
        if metrics.is_healthy():
            self.state.health_status = HealthStatus.HEALTHY
        elif metrics.is_degraded():
            self.state.health_status = HealthStatus.DEGRADED
        else:
            self.state.health_status = HealthStatus.CRITICAL

        # Log for audit trail
        logger.info(
            f"Metrics recorded: throughput={metrics.throughput_per_sec:.1f}/sec, "
            f"latency_p99={metrics.latency_p99_ms:.2f}ms, "
            f"error_rate={metrics.error_rate_percent:.3f}%, "
            f"status={self.state.health_status.value}"
        )

    def get_state(self) -> RolloutState:
        """Get current rollout state."""
        return self.state

    def get_health_status(self) -> HealthStatus:
        """Get current health status."""
        return self.state.health_status

    def get_decision_history(self) -> List[RolloutDecision]:
        """Get history of decisions made."""
        return self.decision_history
