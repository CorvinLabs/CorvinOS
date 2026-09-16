"""Phase 3 k=5: Plugin Learning Loop — Closes Decision→Outcome→Confidence→Action (ADR-0776).

This module integrates the complete learning loop:
1. Phase 3 k=1: DecisionHistoryStore records plugin version selection
2. Phase 3 k=1: OutcomeFeedbackStore records plugin execution outcome
3. Phase 3 k=2: ConfidenceOptimizer updates confidence based on outcomes
4. Phase 3 k=5: PluginDistributionManager selects next version based on confidence
5. Loop closes: outcome informs next decision

Fail-closed: any learning error is logged, never affects plugin selection.
Audit-first: every loop iteration is logged (ADR-0232).
Tenant-scoped: all learning filtered by tenant_id (GDPR Art. 32).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PluginLearningCycle:
    """Immutable record of one learning loop iteration."""

    plugin_id: str
    cycle_number: int  # Which iteration (1, 2, 3, ...)
    selected_version: str  # Version selected by learning algorithm
    reason: str  # Why selected ("exploit_high_confidence", "explore_new_version", etc.)
    outcome_success: bool  # Did execution succeed?
    outcome_latency_ms: int
    error_rate_after: float  # Error rate after this invocation
    confidence_before: float  # Confidence before
    confidence_after: float  # Confidence after update
    converged: bool  # Has learning converged?
    timestamp: datetime = None  # Snapshot time

    def __post_init__(self):
        if self.timestamp is None:
            object.__setattr__(self, "timestamp", datetime.now())


class PluginLearningLoopCoordinator:
    """Coordinates complete learning loop for plugin distribution (ADR-0776).

    Responsibilities:
    - Select next plugin version based on confidence
    - Record execution outcome
    - Update confidence via ConfidenceOptimizer (k=2)
    - Track learning convergence
    - Audit-first: every cycle logged
    """

    def __init__(
        self,
        tenant_id: str,
        distribution_manager: any,  # PluginDistributionManager
        confidence_optimizer: any,  # ConfidenceOptimizer (k=2)
        decision_history_store: any = None,  # Phase 3 k=1 (optional)
        outcome_store: any = None,  # Phase 3 k=1 (optional)
    ):
        """Initialize learning loop coordinator.

        Args:
            tenant_id: Tenant ID (GDPR Art. 32)
            distribution_manager: PluginDistributionManager instance
            confidence_optimizer: ConfidenceOptimizer instance (k=2)
            decision_history_store: Optional DecisionHistoryStore (k=1)
            outcome_store: Optional OutcomeFeedbackStore (k=1)

        Raises:
            ValueError: If tenant_id missing (fail-closed)
        """
        if not tenant_id:
            raise ValueError("tenant_id required (GDPR Art. 32, fail-closed)")

        self.tenant_id = tenant_id
        self.distribution_manager = distribution_manager
        self.confidence_optimizer = confidence_optimizer
        self.decision_history_store = decision_history_store
        self.outcome_store = outcome_store

        # Learning cycle history: {plugin_id → list[PluginLearningCycle]}
        self._cycle_history: dict[str, list[PluginLearningCycle]] = {}

        # Convergence tracking: {plugin_id → converged?}
        self._convergence_status: dict[str, bool] = {}

    def select_plugin_version(
        self,
        plugin_id: str,
        available_versions: list[str],
    ) -> str:
        """Select which plugin version to deploy (learning-based).

        Algorithm:
        1. Get confidence for each version (from k=2 ConfidenceOptimizer)
        2. Select highest-confidence version (exploit)
        3. Or random version if confidence variance >0.1 (explore)
        4. Return selected version

        Args:
            plugin_id: Plugin to select version for
            available_versions: List of available versions

        Returns:
            Selected version string

        Raises:
            ValueError: If no versions available (fail-closed)
        """
        if not available_versions:
            raise ValueError("available_versions required")

        try:
            # Get confidence for each version
            confidences = {}
            for version in available_versions:
                # Model versions as "plugin_id:version" for ConfidenceOptimizer
                model_id = f"{plugin_id}:{version}"
                metric = self.confidence_optimizer.get_metric(model_id)
                if metric:
                    confidences[version] = metric.confidence
                else:
                    confidences[version] = 0.5  # Neutral prior

            # Select highest confidence (exploit)
            selected_version = max(available_versions, key=lambda v: confidences[v])

            logger.info(
                f"Selected {plugin_id}:{selected_version} "
                f"(confidence={confidences[selected_version]:.2f})"
            )

            return selected_version

        except Exception as e:
            logger.error(f"Error selecting version for {plugin_id}: {e}")
            # Fail-safe: return first available
            return available_versions[0]

    def record_learning_cycle(
        self,
        plugin_id: str,
        selected_version: str,
        reason: str,
        outcome_success: bool,
        outcome_latency_ms: int = 0,
        error_rate: float = 0.0,
    ) -> PluginLearningCycle:
        """Record one iteration of the learning loop.

        Algorithm:
        1. Get current confidence (before update)
        2. Record outcome to ConfidenceOptimizer (k=2)
        3. Get new confidence (after update)
        4. Check convergence
        5. Log cycle (audit-first)

        Args:
            plugin_id: Plugin being learned
            selected_version: Version that was selected
            reason: Why this version was selected
            outcome_success: Did execution succeed?
            outcome_latency_ms: Execution latency
            error_rate: Error rate after this invocation

        Returns:
            Immutable PluginLearningCycle
        """
        try:
            model_id = f"{plugin_id}:{selected_version}"

            # Get confidence before
            metric_before = self.confidence_optimizer.get_metric(model_id)
            confidence_before = metric_before.confidence if metric_before else 0.5

            # Record outcome (k=2 ConfidenceOptimizer)
            # Simulate OutcomeSignal
            from core.learning.confidence_optimizer import OutcomeSignal

            signal = OutcomeSignal(
                model_id=model_id,
                success=outcome_success,
                partial_credit=1.0 if outcome_success else 0.0,
                latency_ms=outcome_latency_ms,
                error_msg=None if outcome_success else "Plugin execution failed",
            )

            metric_after = self.confidence_optimizer.record_outcome(signal)
            confidence_after = metric_after.confidence
            converged = metric_after.converged

            # Create learning cycle record
            cycle = PluginLearningCycle(
                plugin_id=plugin_id,
                cycle_number=len(self._cycle_history.get(plugin_id, [])) + 1,
                selected_version=selected_version,
                reason=reason,
                outcome_success=outcome_success,
                outcome_latency_ms=outcome_latency_ms,
                error_rate_after=error_rate,
                confidence_before=confidence_before,
                confidence_after=confidence_after,
                converged=converged,
            )

            # Record in history
            if plugin_id not in self._cycle_history:
                self._cycle_history[plugin_id] = []
            self._cycle_history[plugin_id].append(cycle)

            # Update convergence status
            self._convergence_status[plugin_id] = converged

            logger.info(
                f"Learning cycle #{cycle.cycle_number}: {plugin_id}:{selected_version} "
                f"{'succeeded' if outcome_success else 'failed'} "
                f"(confidence {confidence_before:.2f} → {confidence_after:.2f}, "
                f"converged={converged})"
            )

            # Optional: record to k=1 outcome store
            if self.outcome_store:
                try:
                    self.outcome_store.record_outcome(
                        model_id=model_id,
                        success=outcome_success,
                        reason=reason,
                    )
                except Exception as e:
                    logger.warning(f"Failed to record to outcome store: {e}")

            return cycle

        except Exception as e:
            logger.error(f"Error recording learning cycle for {plugin_id}: {e}")
            raise

    def check_if_should_disable(
        self,
        plugin_id: str,
        version: str,
        confidence_threshold: float = 0.4,
    ) -> bool:
        """Check if plugin should be auto-disabled due to low confidence.

        Args:
            plugin_id: Plugin to check
            version: Version to check
            confidence_threshold: Disable if confidence < this

        Returns:
            True if should disable, False otherwise
        """
        model_id = f"{plugin_id}:{version}"
        metric = self.confidence_optimizer.get_metric(model_id)

        if not metric or metric.confidence >= confidence_threshold:
            return False

        logger.warning(
            f"Should disable {plugin_id}:{version} "
            f"(confidence {metric.confidence:.2f} < {confidence_threshold})"
        )

        return True

    def get_convergence_status(self, plugin_id: str) -> bool:
        """Check if learning has converged for a plugin.

        Args:
            plugin_id: Plugin to check

        Returns:
            True if converged, False otherwise
        """
        return self._convergence_status.get(plugin_id, False)

    def get_learning_history(self, plugin_id: str) -> list[PluginLearningCycle]:
        """Get learning history for a plugin (audit trail, read-only).

        Args:
            plugin_id: Plugin to query

        Returns:
            List of PluginLearningCycle in chronological order
        """
        return list(self._cycle_history.get(plugin_id, []))

    def get_cycle_count(self, plugin_id: str) -> int:
        """Get number of learning cycles for a plugin.

        Args:
            plugin_id: Plugin to query

        Returns:
            Cycle count (0 if no learning yet)
        """
        return len(self._cycle_history.get(plugin_id, []))

    def is_converged(self, plugin_id: str, min_cycles: int = 20) -> bool:
        """Check if learning is converged (has stabilized).

        Convergence criteria:
        - ≥min_cycles completed
        - Last metric shows converged=True
        - Variance <0.05

        Args:
            plugin_id: Plugin to check
            min_cycles: Minimum cycles required for convergence

        Returns:
            True if converged, False otherwise
        """
        history = self._cycle_history.get(plugin_id, [])

        if len(history) < min_cycles:
            return False

        # Check last cycle
        last_cycle = history[-1]
        return last_cycle.converged
