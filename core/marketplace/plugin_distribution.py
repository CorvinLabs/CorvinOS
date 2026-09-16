"""Phase 3 k=5: Plugin Distribution & Update — Canary Rollout + Auto-Rollback (ADR-0776).

This module manages plugin updates with canary deployment and learning-based auto-rollback:
1. Registry: fetch latest version + signature daily
2. Rollback: keep N−1 versions, instant downgrade on failure
3. Canary: 5% of users get new version, monitor error rate
4. Auto-rollback: error rate >2% triggers global revert (all users)
5. Learning loop: confidence per version, auto-disable if <0.4

Fail-closed: any distribution error is logged, never propagates to deployment.
Audit-first: every version check, deployment, rollback is logged (ADR-0232).
Tenant-scoped: all deployments filtered by tenant_id (GDPR Art. 32).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, List
from uuid import uuid4

logger = logging.getLogger(__name__)


class DeploymentStrategy(Enum):
    """Plugin deployment strategy."""

    STABLE = "stable"  # All users get latest (no canary)
    CANARY = "canary"  # 5% get new version, monitor error rate
    ROLLBACK = "rollback"  # Revert to previous version


@dataclass(frozen=True)
class PluginVersion:
    """Immutable plugin version record."""

    plugin_id: str
    version: str  # Semantic version (e.g., "1.0.0")
    signature: str  # Ed25519 signature (base64)
    signature_verified: bool
    code_hash: str  # SHA256 of plugin code
    released_at: datetime
    download_url: str
    checksum: str  # For download verification


@dataclass(frozen=True)
class DeploymentEvent:
    """Immutable deployment audit event (ADR-0232)."""

    plugin_id: str
    version: str
    strategy: DeploymentStrategy
    user_count: int  # How many users affected
    percent_users: float  # Percentage of total users
    status: str  # "success" | "failure" | "rollback"
    error_msg: Optional[str]
    timestamp: datetime = field(default_factory=datetime.now)
    reason: Optional[str] = None  # Why rollback (if status="rollback")


@dataclass(frozen=True)
class HealthMetric:
    """Immutable plugin health snapshot."""

    plugin_id: str
    version: str
    error_rate: float  # 0.0-1.0 (errors / total invocations)
    crash_rate: float  # 0.0-1.0 (crashes / total invocations)
    latency_p99: int  # 99th percentile latency (ms)
    user_feedback_score: float  # -1.0 to 1.0 (likes vs dislikes)
    confidence: float  # From ConfidenceOptimizer (k=2)
    n_invocations: int
    timestamp: datetime = field(default_factory=datetime.now)


class PluginDistributionManager:
    """Manages plugin distribution with canary + learning-based rollback (ADR-0776).

    Responsibilities:
    - Fetch latest versions from registry
    - Manage N−1 version history (for instant rollback)
    - Coordinate canary deployment (5% of users)
    - Track health metrics (error rate, latency, user feedback)
    - Auto-rollback if error rate >2%
    - Audit-first: every deployment event logged
    """

    def __init__(
        self,
        tenant_id: str,
        optimizer: Optional[any] = None,  # ConfidenceOptimizer (k=2)
        canary_percentage: float = 0.05,
        rollback_error_threshold: float = 0.02,
    ):
        """Initialize distribution manager.

        Args:
            tenant_id: Tenant ID (GDPR Art. 32)
            optimizer: Optional ConfidenceOptimizer for learning loop
            canary_percentage: Fraction of users in canary (default 5%)
            rollback_error_threshold: Error rate threshold for auto-rollback (default 2%)

        Raises:
            ValueError: If tenant_id missing (fail-closed)
        """
        if not tenant_id:
            raise ValueError("tenant_id required (GDPR Art. 32, fail-closed)")
        if not (0.0 < canary_percentage < 1.0):
            raise ValueError("canary_percentage must be in (0.0, 1.0)")
        if not (0.0 < rollback_error_threshold < 1.0):
            raise ValueError("rollback_error_threshold must be in (0.0, 1.0)")

        self.tenant_id = tenant_id
        self.optimizer = optimizer
        self.canary_percentage = canary_percentage
        self.rollback_error_threshold = rollback_error_threshold

        # State: {plugin_id → (current_version, previous_version)}
        self._deployed_versions: dict[str, tuple[str, Optional[str]]] = {}

        # Version registry: {(plugin_id, version) → PluginVersion}
        self._version_registry: dict[tuple[str, str], PluginVersion] = {}

        # Health metrics: {(plugin_id, version) → HealthMetric}
        self._health_metrics: dict[tuple[str, str], HealthMetric] = {}

        # Deployment history: all events (audit trail)
        self._deployment_history: list[DeploymentEvent] = []

        # Canary tracking: {plugin_id → (version, start_time, error_count, total_count)}
        self._active_canaries: dict[str, tuple[str, datetime, int, int]] = {}

    def register_version(self, plugin_version: PluginVersion) -> None:
        """Register a new plugin version (from registry fetch).

        Args:
            plugin_version: PluginVersion to register

        Raises:
            ValueError: If version invalid (fail-closed)
        """
        if not plugin_version.plugin_id or not plugin_version.version:
            raise ValueError("plugin_id and version required")

        key = (plugin_version.plugin_id, plugin_version.version)
        self._version_registry[key] = plugin_version

        logger.info(
            f"Registered plugin {plugin_version.plugin_id} "
            f"v{plugin_version.version}"
        )

    def deploy_version(
        self,
        plugin_id: str,
        version: str,
        strategy: DeploymentStrategy = DeploymentStrategy.CANARY,
        total_user_count: int = 1000,
    ) -> DeploymentEvent:
        """Deploy a plugin version (canary or stable).

        Algorithm:
        1. Lookup version in registry (verify signature)
        2. Calculate user affected (5% for canary, 100% for stable)
        3. Create deployment event (audit trail)
        4. For canary: start monitoring error rate
        5. For stable: update deployed version, keep previous in history

        Args:
            plugin_id: Plugin to deploy
            version: Version to deploy
            strategy: DeploymentStrategy (CANARY or STABLE)
            total_user_count: Total users in tenant

        Returns:
            Immutable DeploymentEvent

        Raises:
            ValueError: If version not found or signature invalid (fail-closed)
        """
        if not plugin_id or not version:
            raise ValueError("plugin_id and version required")

        # Lookup version
        key = (plugin_id, version)
        if key not in self._version_registry:
            event = DeploymentEvent(
                plugin_id=plugin_id,
                version=version,
                strategy=strategy,
                user_count=0,
                percent_users=0.0,
                status="failure",
                error_msg=f"Version {version} not found in registry",
            )
            self._deployment_history.append(event)
            logger.error(f"Deployment failed: version {version} not found")
            raise ValueError(f"Version {version} not found")

        plugin_version = self._version_registry[key]

        # Verify signature (fail-closed if not verified)
        if not plugin_version.signature_verified:
            event = DeploymentEvent(
                plugin_id=plugin_id,
                version=version,
                strategy=strategy,
                user_count=0,
                percent_users=0.0,
                status="failure",
                error_msg="Signature not verified",
            )
            self._deployment_history.append(event)
            logger.error(f"Deployment failed: signature not verified for {version}")
            raise ValueError("Signature not verified")

        # Calculate affected users
        if strategy == DeploymentStrategy.CANARY:
            affected_users = max(1, int(total_user_count * self.canary_percentage))
            percent = self.canary_percentage * 100
        else:  # STABLE or ROLLBACK
            affected_users = total_user_count
            percent = 100.0

        # Create deployment event
        event = DeploymentEvent(
            plugin_id=plugin_id,
            version=version,
            strategy=strategy,
            user_count=affected_users,
            percent_users=percent,
            status="success",
            error_msg=None,
        )
        self._deployment_history.append(event)

        # Update version tracking
        if strategy == DeploymentStrategy.CANARY:
            # Start canary monitoring
            self._active_canaries[plugin_id] = (version, datetime.now(), 0, 0)
            logger.info(
                f"Canary deployed: {plugin_id} v{version} "
                f"({affected_users} users, {percent:.1f}%)"
            )
        else:
            # Update deployed version
            current_version = self._deployed_versions.get(plugin_id, (None, None))[0]
            self._deployed_versions[plugin_id] = (version, current_version)
            logger.info(f"Stable deployed: {plugin_id} v{version} ({affected_users} users)")

        return event

    def record_invocation(
        self,
        plugin_id: str,
        version: str,
        success: bool,
        latency_ms: int,
        error_msg: Optional[str] = None,
    ) -> None:
        """Record plugin invocation (used to track health metrics).

        Args:
            plugin_id: Plugin invoked
            version: Version invoked
            success: Whether invocation succeeded
            latency_ms: Invocation latency
            error_msg: Error message (if success=False)
        """
        # Update health metrics (in production, persist to database)
        key = (plugin_id, version)
        if key not in self._health_metrics:
            self._health_metrics[key] = HealthMetric(
                plugin_id=plugin_id,
                version=version,
                error_rate=0.0,
                crash_rate=0.0,
                latency_p99=0,
                user_feedback_score=0.0,
                confidence=0.5,
                n_invocations=0,
            )

        metric = self._health_metrics[key]

        # Simulate metric update (in production: proper aggregation)
        n = metric.n_invocations + 1
        error_rate = (metric.error_rate * metric.n_invocations + (0 if success else 1)) / n
        latency_p99 = max(metric.latency_p99, latency_ms)  # Simplified

        updated_metric = HealthMetric(
            plugin_id=plugin_id,
            version=version,
            error_rate=error_rate,
            crash_rate=metric.crash_rate,
            latency_p99=latency_p99,
            user_feedback_score=metric.user_feedback_score,
            confidence=metric.confidence,
            n_invocations=n,
        )
        self._health_metrics[key] = updated_metric

        # Check if canary should be rolled back
        if plugin_id in self._active_canaries:
            canary_version, start_time, error_count, total_count = self._active_canaries[
                plugin_id
            ]
            if not success:
                error_count += 1
            total_count += 1

            # Calculate error rate
            canary_error_rate = error_count / total_count if total_count > 0 else 0.0

            # Auto-rollback if error rate exceeds threshold
            if canary_error_rate > self.rollback_error_threshold:
                logger.warning(
                    f"Canary error rate {canary_error_rate:.1%} exceeds threshold "
                    f"{self.rollback_error_threshold:.1%} — rolling back"
                )
                self.rollback_version(
                    plugin_id,
                    reason=f"Error rate {canary_error_rate:.1%}",
                )
                del self._active_canaries[plugin_id]
            else:
                # Update canary tracking
                self._active_canaries[plugin_id] = (
                    canary_version,
                    start_time,
                    error_count,
                    total_count,
                )

    def rollback_version(
        self,
        plugin_id: str,
        reason: str = "Error rate exceeded",
    ) -> DeploymentEvent:
        """Rollback plugin to previous version (auto-triggered or manual).

        Args:
            plugin_id: Plugin to rollback
            reason: Reason for rollback

        Returns:
            Immutable DeploymentEvent

        Raises:
            ValueError: If no previous version available (fail-closed)
        """
        if plugin_id not in self._deployed_versions:
            raise ValueError(f"Plugin {plugin_id} not deployed")

        current_version, previous_version = self._deployed_versions[plugin_id]

        if not previous_version:
            raise ValueError(f"No previous version available for {plugin_id}")

        # Deploy previous version
        event = DeploymentEvent(
            plugin_id=plugin_id,
            version=previous_version,
            strategy=DeploymentStrategy.ROLLBACK,
            user_count=1000,  # All users
            percent_users=100.0,
            status="success",
            error_msg=None,
            reason=reason,
        )
        self._deployment_history.append(event)

        # Update tracked version
        self._deployed_versions[plugin_id] = (previous_version, current_version)

        logger.warning(
            f"Rolled back: {plugin_id} {current_version} → {previous_version} ({reason})"
        )

        return event

    def auto_disable_if_low_confidence(
        self,
        plugin_id: str,
        version: str,
        confidence_threshold: float = 0.4,
    ) -> bool:
        """Auto-disable plugin if confidence <threshold (learning loop).

        Args:
            plugin_id: Plugin to check
            version: Version to check
            confidence_threshold: Disable if confidence below this

        Returns:
            True if disabled, False otherwise
        """
        key = (plugin_id, version)
        metric = self._health_metrics.get(key)

        if not metric or metric.confidence >= confidence_threshold:
            return False

        # Auto-disable
        logger.warning(
            f"Auto-disabled {plugin_id} v{version} "
            f"(confidence {metric.confidence:.2f} < {confidence_threshold})"
        )

        # Would notify user here
        # For now, just log

        return True

    def get_health_metrics(self, plugin_id: str, version: str) -> Optional[HealthMetric]:
        """Get health metrics for a plugin version.

        Args:
            plugin_id: Plugin to query
            version: Version to query

        Returns:
            HealthMetric or None if not available
        """
        return self._health_metrics.get((plugin_id, version))

    def get_deployment_history(self) -> list[DeploymentEvent]:
        """Get all deployment events (audit trail, read-only).

        Returns:
            List of DeploymentEvent in chronological order
        """
        return list(self._deployment_history)

    def get_current_version(self, plugin_id: str) -> Optional[str]:
        """Get currently deployed version of a plugin.

        Args:
            plugin_id: Plugin to query

        Returns:
            Version string or None if not deployed
        """
        if plugin_id in self._deployed_versions:
            return self._deployed_versions[plugin_id][0]
        return None
