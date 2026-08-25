"""SelfMonitoringSubsystem: Detect cognitive overload.

Monitors:
- Error rate (fraction of iterations with errors)
- Context size (tokens used)
- Strategy diversity (number of different strategies tried)
- Wallclock time (seconds elapsed in current session)
- Token burn (daily budget consumption)

Formula:
cognitive_load = weighted_sum(
    error_rate × 0.3,
    context_size × 0.2,
    low_strategy_diversity × 0.2,
    wallclock_time × 0.15,
    token_burn × 0.15
)

Alert: If cognitive_load > 0.8 → "cognitive_overload"
Action: Checkpoint + reset context entirely (fresh start session)

Implementation:
- Track all 5 metrics per session
- Normalize each to [0.0-1.0]
- Calculate weighted sum
- Alert on threshold

ADR-0407: Session Manager Phase 2.2
Depends on: base.MonitorBase
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Set

from .base import MonitorBase, MonitorAlert, AlertType, MonitorConfig, MonitorState

logger = logging.getLogger(__name__)


@dataclass
class SelfMonitoringState(MonitorState):
    """Extended state for SelfMonitoringSubsystem."""

    error_count: int = 0
    iteration_count: int = 0
    context_size_tokens: int = 0
    max_context_tokens: int = 200000
    strategies_tried: Set[str] = field(default_factory=set)
    session_start_time: datetime = field(default_factory=datetime.utcnow)
    token_budget_used: float = 0.0  # Fraction [0.0-1.0]


class SelfMonitoringSubsystem(MonitorBase):
    """Detect cognitive overload.

    Monitors 5 dimensions of system state and calculates cognitive load.
    If load > 0.8, recommends checkpoint and context reset.

    Configuration:
    - error_rate_weight: Weight for error rate (default 0.3)
    - context_size_weight: Weight for context size (default 0.2)
    - strategy_diversity_weight: Weight for low strategy diversity (default 0.2)
    - wallclock_time_weight: Weight for wallclock time (default 0.15)
    - token_burn_weight: Weight for token burn (default 0.15)
    - cognitive_load_threshold: Threshold for overload (default 0.8)
    - error_rate_threshold: Error rate at 100% load (default 0.3)
    - max_wallclock_seconds: Wallclock time at 100% load (default 3600 = 1 hour)
    - min_strategy_diversity_healthy: Healthy strategy count (default 3)
    """

    def __init__(self, config: Optional[MonitorConfig] = None):
        """Initialize SelfMonitoringSubsystem.

        Args:
            config: Optional configuration
        """
        super().__init__("self_monitoring", config)

        # Weights (must sum to 1.0)
        self.error_rate_weight = 0.3
        self.context_size_weight = 0.2
        self.strategy_diversity_weight = 0.2
        self.wallclock_time_weight = 0.15
        self.token_burn_weight = 0.15

        # Thresholds
        self.cognitive_load_threshold = 0.8
        self.error_rate_threshold = 0.3  # 30% error rate = 100% load
        self.max_wallclock_seconds = 3600  # 1 hour = 100% load
        self.min_strategy_diversity_healthy = 3

    def record_iteration(
        self,
        session_id: str,
        task_id: str,
        tenant_id: str,
        error_occurred: bool = False,
        strategy_used: Optional[str] = None,
        context_size: int = 0,
    ) -> None:
        """Record an iteration.

        Args:
            session_id: Session ID
            task_id: Task ID
            tenant_id: Tenant ID
            error_occurred: Whether an error occurred in this iteration
            strategy_used: Optional strategy name used in this iteration
            context_size: Context size in tokens
        """
        state = self.create_or_get_self_monitoring_state(session_id, task_id, tenant_id)

        state.iteration_count += 1
        if error_occurred:
            state.error_count += 1

        if strategy_used:
            state.strategies_tried.add(strategy_used)

        if context_size > 0:
            state.context_size_tokens = context_size

    def update_token_budget(
        self, session_id: str, fraction_used: float
    ) -> None:
        """Update token budget usage.

        Args:
            session_id: Session ID
            fraction_used: Fraction of daily budget used [0.0-1.0]
        """
        if session_id in self.session_states:
            state = self.session_states[session_id]
            if isinstance(state, SelfMonitoringState):
                state.token_budget_used = fraction_used

    def check(self, state: MonitorState) -> Optional[MonitorAlert]:
        """Check for cognitive overload.

        Args:
            state: MonitorState for the session

        Returns:
            MonitorAlert if overload detected, None otherwise
        """
        if not isinstance(state, SelfMonitoringState):
            return None

        # Calculate cognitive load
        cognitive_load = self._calculate_cognitive_load(state)

        if cognitive_load > self.cognitive_load_threshold:
            alert = MonitorAlert(
                alert_type=AlertType.COGNITIVE_OVERLOAD,
                session_id=state.session_id,
                task_id=state.task_id,
                tenant_id=state.tenant_id,
                severity="critical",
                reason=f"Cognitive overload detected: load={cognitive_load:.2f} "
                f"(threshold={self.cognitive_load_threshold})",
                metadata={
                    "cognitive_load": cognitive_load,
                    "error_rate": self._calculate_error_rate(state),
                    "context_size_tokens": state.context_size_tokens,
                    "strategy_diversity": len(state.strategies_tried),
                    "wallclock_time_seconds": (
                        datetime.utcnow() - state.session_start_time
                    ).total_seconds(),
                    "token_budget_used": state.token_budget_used,
                    "iteration_count": state.iteration_count,
                },
            )

            logger.critical(
                f"{self.name}: {state.session_id} cognitive overload detected "
                f"(load={cognitive_load:.2f})"
            )

            return alert

        return None

    def _calculate_cognitive_load(self, state: SelfMonitoringState) -> float:
        """Calculate cognitive load from all 5 dimensions.

        Args:
            state: SelfMonitoringState

        Returns:
            Cognitive load [0.0-1.0]
        """
        # Dimension 1: Error rate
        error_rate = self._calculate_error_rate(state)
        error_load = min(error_rate / self.error_rate_threshold, 1.0)

        # Dimension 2: Context size
        context_fraction = min(
            state.context_size_tokens / state.max_context_tokens, 1.0
        )
        context_load = context_fraction

        # Dimension 3: Low strategy diversity
        strategy_count = len(state.strategies_tried)
        if strategy_count < self.min_strategy_diversity_healthy:
            diversity_load = 1.0 - (strategy_count / self.min_strategy_diversity_healthy)
        else:
            diversity_load = 0.0

        # Dimension 4: Wallclock time
        elapsed_seconds = (datetime.utcnow() - state.session_start_time).total_seconds()
        time_load = min(elapsed_seconds / self.max_wallclock_seconds, 1.0)

        # Dimension 5: Token burn
        token_load = state.token_budget_used

        # Weighted sum
        cognitive_load = (
            error_load * self.error_rate_weight
            + context_load * self.context_size_weight
            + diversity_load * self.strategy_diversity_weight
            + time_load * self.wallclock_time_weight
            + token_load * self.token_burn_weight
        )

        return min(cognitive_load, 1.0)

    def _calculate_error_rate(self, state: SelfMonitoringState) -> float:
        """Calculate error rate from error count and iteration count.

        Args:
            state: SelfMonitoringState

        Returns:
            Error rate [0.0-1.0]
        """
        if state.iteration_count == 0:
            return 0.0

        return state.error_count / state.iteration_count

    def create_or_get_self_monitoring_state(
        self, session_id: str, task_id: str, tenant_id: str
    ) -> SelfMonitoringState:
        """Create or get SelfMonitoringState for a session.

        Args:
            session_id: Session ID
            task_id: Task ID
            tenant_id: Tenant ID

        Returns:
            SelfMonitoringState for the session
        """
        if session_id not in self.session_states:
            self.session_states[session_id] = SelfMonitoringState(
                session_id=session_id,
                task_id=task_id,
                tenant_id=tenant_id,
            )
        return self.session_states[session_id]
