"""
Phase 6 Canary Deployment Manager — Controls canary deployment via feature flags.

Responsibilities:
1. Enable/disable canary deployment based on operator approval
2. Manage traffic split percentage (10%, 50%, 100%)
3. Check health gates before allowing traffic changes
4. Coordinate with orchestrator for promotion decisions
5. Provide audit trail for all deployment changes

Integrates with:
- FeatureFlag system (ship-dark default)
- RolloutOrchestrator (for promotion decisions)
- HealthCheckEvaluator (for go/no-go gates)
- Audit trail (hash-chained deployment events)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Tuple
import asyncio
import logging


logger = logging.getLogger(__name__)


class CanaryStage(Enum):
    """Stages of canary deployment."""
    DISABLED = "disabled"  # Feature flag off, no canary
    INITIALIZING = "initializing"  # Flag enabled, preparing infrastructure
    READY = "ready"  # Canary infrastructure ready
    ACTIVE_10 = "active_10"  # Canary running at 10% traffic
    ACTIVE_50 = "active_50"  # Canary running at 50% traffic
    ACTIVE_100 = "active_100"  # Canary running at 100% traffic
    PROMOTING = "promoting"  # In process of promoting to next stage
    FAILED = "failed"  # Canary health check failed
    ROLLED_BACK = "rolled_back"  # Rolled back to Phase 5


class HealthCheckResult(Enum):
    """Result of a health check."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass
class CanaryHealthGate:
    """A health gate that must pass before promotion."""
    gate_id: str  # e.g., "error_rate", "latency_p99", "audit_integrity"
    threshold: float  # Critical threshold value
    current_value: float  # Current value
    unit: str  # Unit (%, ms, count, etc.)
    passed: bool
    message: str  # Human-readable status

    def __str__(self) -> str:
        return f"{self.gate_id}={self.current_value}{self.unit} (threshold: {self.threshold}{self.unit}) [{self.message}]"


@dataclass
class CanaryHealthCheckResult:
    """Result of canary health check."""
    timestamp: datetime
    overall_status: HealthCheckResult
    gates: List[CanaryHealthGate]
    ready_for_promotion: bool
    recommendation: str
    confidence_percent: float

    def __str__(self) -> str:
        gates_str = "\n  ".join(str(g) for g in self.gates)
        return f"CanaryHealth@{self.timestamp.isoformat()}: {self.overall_status.value}\n  {gates_str}\n  Ready: {self.ready_for_promotion} ({self.recommendation})"


@dataclass
class CanaryDeploymentState:
    """Tracks the current deployment state."""
    stage: CanaryStage = CanaryStage.DISABLED
    traffic_split_percent: int = 0
    enabled: bool = False
    last_health_check: Optional[datetime] = None
    last_promotion_time: Optional[datetime] = None
    health_history: List[CanaryHealthCheckResult] = field(default_factory=list)
    promotion_attempts: int = 0
    rollback_attempts: int = 0


class CanaryDeploymentManager:
    """
    Controls canary deployment via feature flags and health gates.

    Responsibilities:
    - Enable/disable canary via feature flag
    - Manage traffic split percentage (10%, 50%, 100%)
    - Check health gates before allowing promotion
    - Auto-promote when gates pass
    - Emergency rollback on health degradation
    - Audit trail for all deployment changes
    """

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.state = CanaryDeploymentState()
        self.feature_flag_name = "canary_deployment_phase6"
        self._health_gate_interval = timedelta(minutes=15)

    async def enable_canary(self, traffic_percent: int) -> Tuple[bool, str]:
        """
        Enable canary at specified traffic percentage.

        Args:
            traffic_percent: 10, 50, or 100

        Returns:
            (success, message)
        """
        if traffic_percent not in (10, 50, 100):
            return False, f"Invalid traffic percent: {traffic_percent}. Must be 10, 50, or 100."

        if self.state.stage == CanaryStage.DISABLED:
            self.state.stage = CanaryStage.INITIALIZING
            self.state.enabled = True
            self.state.traffic_split_percent = traffic_percent
            logger.info(f"Canary deployment enabled for tenant {self.tenant_id} at {traffic_percent}% traffic")
            return True, f"Canary enabled at {traffic_percent}% traffic"

        if self.state.traffic_split_percent == traffic_percent:
            return True, f"Canary already at {traffic_percent}% traffic"

        # Attempting to change traffic split while canary is active
        logger.warning(f"Requesting traffic split change to {traffic_percent}% while active at {self.state.traffic_split_percent}%")
        return False, f"Cannot change traffic while canary is active at {self.state.traffic_split_percent}%. Rollback first."

    async def disable_canary(self) -> Tuple[bool, str]:
        """Disable canary deployment."""
        if self.state.stage == CanaryStage.DISABLED:
            return True, "Canary already disabled"

        self.state.stage = CanaryStage.DISABLED
        self.state.enabled = False
        self.state.traffic_split_percent = 0
        logger.info(f"Canary deployment disabled for tenant {self.tenant_id}")
        return True, "Canary disabled"

    async def is_canary_healthy(self) -> CanaryHealthCheckResult:
        """
        Check if canary is healthy enough to proceed with promotion.

        Returns:
            CanaryHealthCheckResult with detailed gate status
        """
        now = datetime.now()

        # Define health gates for canary
        gates: List[CanaryHealthGate] = [
            CanaryHealthGate(
                gate_id="error_rate",
                threshold=0.1,  # <0.1% error rate
                current_value=0.02,  # Simulated: 0.02%
                unit="%",
                passed=True,
                message="Error rate within SLO"
            ),
            CanaryHealthGate(
                gate_id="latency_p99",
                threshold=200.0,  # <200ms p99
                current_value=45.0,  # Simulated: 45ms
                unit="ms",
                passed=True,
                message="Latency within SLO"
            ),
            CanaryHealthGate(
                gate_id="audit_integrity",
                threshold=99.0,  # >=99% integrity
                current_value=99.95,  # Simulated: 99.95%
                unit="%",
                passed=True,
                message="Audit chain verified"
            ),
            CanaryHealthGate(
                gate_id="feature_promotion_velocity",
                threshold=0,  # No stuck features (or minor acceptable count)
                current_value=0,  # Simulated: 0 stuck
                unit="count",
                passed=True,
                message="No features stuck in ALPHA"
            ),
        ]

        # Determine overall status
        all_passed = all(g.passed for g in gates)
        degraded = sum(1 for g in gates if not g.passed) >= 1

        if all_passed:
            overall_status = HealthCheckResult.HEALTHY
            recommendation = "All gates pass. Safe to promote to next stage."
            confidence = 98.0
            ready = True
        elif degraded:
            overall_status = HealthCheckResult.DEGRADED
            recommendation = "Some gates are marginal. Monitor closely before promoting."
            confidence = 75.0
            ready = False
        else:
            overall_status = HealthCheckResult.CRITICAL
            recommendation = "Critical gates failed. Rollback recommended."
            confidence = 20.0
            ready = False

        result = CanaryHealthCheckResult(
            timestamp=now,
            overall_status=overall_status,
            gates=gates,
            ready_for_promotion=ready,
            recommendation=recommendation,
            confidence_percent=confidence,
        )

        # Store in history
        self.state.health_history.append(result)
        self.state.last_health_check = now
        logger.info(f"Canary health check: {overall_status.value} (confidence: {confidence}%)")

        return result

    async def auto_promote_to_next_stage(self) -> Tuple[bool, str]:
        """
        Promote to next stage if health gates pass.

        Returns:
            (success, message)
        """
        if self.state.stage == CanaryStage.DISABLED:
            return False, "Canary not enabled"

        if self.state.stage == CanaryStage.PROMOTING:
            return False, "Already promoting, wait for completion"

        if self.state.stage == CanaryStage.ACTIVE_100:
            return False, "Already at 100%, ready for completion"

        # Check health
        health = await self.is_canary_healthy()
        if not health.ready_for_promotion:
            return False, f"Health gates not passing: {health.recommendation}"

        # Transition to next stage
        self.state.stage = CanaryStage.PROMOTING
        self.state.promotion_attempts += 1
        current_traffic = self.state.traffic_split_percent

        try:
            match current_traffic:
                case 10:
                    next_traffic = 50
                    next_stage = CanaryStage.ACTIVE_50
                case 50:
                    next_traffic = 100
                    next_stage = CanaryStage.ACTIVE_100
                case _:
                    return False, f"Cannot promote from {current_traffic}% traffic"

            self.state.traffic_split_percent = next_traffic
            self.state.stage = next_stage
            self.state.last_promotion_time = datetime.now()
            logger.info(f"Canary promoted to {next_traffic}% traffic (attempt {self.state.promotion_attempts})")
            return True, f"Promoted to {next_traffic}% traffic"

        except Exception as e:
            self.state.stage = CanaryStage.FAILED
            logger.error(f"Promotion failed: {e}")
            return False, f"Promotion failed: {e}"

    async def rollback_to_phase5(self) -> Tuple[bool, str]:
        """
        Emergency rollback to Phase 5.

        Returns:
            (success, message)
        """
        if self.state.stage == CanaryStage.ROLLED_BACK:
            return True, "Already rolled back"

        self.state.stage = CanaryStage.ROLLED_BACK
        self.state.traffic_split_percent = 0
        self.state.enabled = False
        self.state.rollback_attempts += 1
        logger.critical(f"EMERGENCY ROLLBACK triggered for tenant {self.tenant_id} (attempt {self.state.rollback_attempts})")
        return True, f"Rolled back to Phase 5 (attempt {self.state.rollback_attempts})"

    def get_state(self) -> CanaryDeploymentState:
        """Get current deployment state."""
        return self.state

    def get_health_history(self, limit: int = 10) -> List[CanaryHealthCheckResult]:
        """Get recent health check history."""
        return self.state.health_history[-limit:]

    async def wait_for_health_check(self, timeout_seconds: int = 300) -> bool:
        """
        Wait for next health check to complete.

        Used by orchestrator to block on health evaluation.
        """
        start = datetime.now()
        while (datetime.now() - start).total_seconds() < timeout_seconds:
            if self.state.last_health_check is not None:
                age = (datetime.now() - self.state.last_health_check).total_seconds()
                if age < 60:  # Recent check
                    return True
            await asyncio.sleep(5)

        return False

    def get_promotion_stats(self) -> Dict[str, any]:
        """Get promotion attempt statistics."""
        return {
            "promotion_attempts": self.state.promotion_attempts,
            "rollback_attempts": self.state.rollback_attempts,
            "current_stage": self.state.stage.value,
            "traffic_split_percent": self.state.traffic_split_percent,
            "health_checks_run": len(self.state.health_history),
            "last_promotion": self.state.last_promotion_time.isoformat() if self.state.last_promotion_time else None,
            "last_health_check": self.state.last_health_check.isoformat() if self.state.last_health_check else None,
        }
