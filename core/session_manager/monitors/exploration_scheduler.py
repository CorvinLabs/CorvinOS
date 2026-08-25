"""ExplorationScheduler: Detect and escape local optima.

Monitors:
- Success rate per iteration
- Iteration count

Detection:
- Success rate [0.6-0.8] for 15+ consecutive iterations → suspect local optimum

Alert: "local_optimum_suspected"

Action: Try alternative strategy for 5 iterations (explore)

Implementation:
- Track success rate over sliding window (15 iterations)
- Detect plateau (rate in sweet spot of progress but stalled improvement)
- Recommend exploration phase

ADR-0407: Session Manager Phase 2.2
Depends on: base.MonitorBase
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from .base import MonitorBase, MonitorAlert, AlertType, MonitorConfig, MonitorState

logger = logging.getLogger(__name__)


@dataclass
class ExplorationSchedulerState(MonitorState):
    """Extended state for ExplorationScheduler."""

    success_rates: List[float] = field(default_factory=list)
    iterations_at_plateau: int = 0
    in_exploration_mode: bool = False
    exploration_mode_start_iteration: Optional[int] = None


class ExplorationScheduler(MonitorBase):
    """Detect and escape local optima.

    Monitors success rate over time. When success rate plateaus in the [0.6-0.8]
    range (making progress but not improving), recommends exploration.

    Alert: "local_optimum_suspected" if plateau detected for 15+ iterations

    Configuration:
    - plateau_min_success_rate: Min success rate to consider plateau, default 0.6
    - plateau_max_success_rate: Max success rate to consider plateau, default 0.8
    - plateau_detection_window: Iterations to check, default 15
    """

    def __init__(self, config: Optional[MonitorConfig] = None):
        """Initialize ExplorationScheduler.

        Args:
            config: Optional configuration
        """
        super().__init__("exploration_scheduler", config)
        self.plateau_min_success_rate = 0.6
        self.plateau_max_success_rate = 0.8
        self.plateau_detection_window = 15
        self.exploration_mode_duration = 5  # Try alternative for 5 iterations

    def update_success_rate(
        self, session_id: str, task_id: str, tenant_id: str, success_rate: float
    ) -> None:
        """Update success rate for a session.

        Args:
            session_id: Session ID
            task_id: Task ID
            tenant_id: Tenant ID
            success_rate: Success rate [0.0-1.0] for current iteration
        """
        state = self.create_or_get_exploration_state(session_id, task_id, tenant_id)
        state.success_rates.append(success_rate)

        # Keep only last N rates (sliding window)
        if len(state.success_rates) > self.plateau_detection_window * 2:
            state.success_rates.pop(0)

    def check(self, state: MonitorState) -> Optional[MonitorAlert]:
        """Check for local optimum (plateau in success rate).

        Args:
            state: MonitorState for the session

        Returns:
            MonitorAlert if plateau detected, None otherwise
        """
        if not isinstance(state, ExplorationSchedulerState):
            return None

        if len(state.success_rates) < self.plateau_detection_window:
            return None

        # Get recent success rates
        recent_rates = state.success_rates[-self.plateau_detection_window :]

        # Check if all rates are in plateau zone [0.6-0.8]
        in_plateau = all(
            self.plateau_min_success_rate <= rate <= self.plateau_max_success_rate
            for rate in recent_rates
        )

        if not in_plateau:
            # Reset plateau counter if we break out
            state.iterations_at_plateau = 0
            return None

        # All rates in plateau zone
        state.iterations_at_plateau += 1

        # Alert after sustained plateau
        if state.iterations_at_plateau >= self.plateau_detection_window:
            avg_rate = sum(recent_rates) / len(recent_rates)

            alert = MonitorAlert(
                alert_type=AlertType.LOCAL_OPTIMUM_SUSPECTED,
                session_id=state.session_id,
                task_id=state.task_id,
                tenant_id=state.tenant_id,
                severity="info",
                reason=f"Local optimum suspected: success rate plateaued at {avg_rate:.2f} "
                f"for {state.iterations_at_plateau} iterations",
                metadata={
                    "avg_success_rate": avg_rate,
                    "plateau_duration_iterations": state.iterations_at_plateau,
                    "min_threshold": self.plateau_min_success_rate,
                    "max_threshold": self.plateau_max_success_rate,
                    "recent_rates": recent_rates[-5:],  # Last 5 rates
                },
            )

            logger.info(
                f"{self.name}: {state.session_id} detected local optimum "
                f"(rate={avg_rate:.2f}, duration={state.iterations_at_plateau})"
            )

            # Reset plateau counter after alert
            state.iterations_at_plateau = 0
            state.in_exploration_mode = True
            state.exploration_mode_start_iteration = len(state.success_rates)

            return alert

        return None

    def get_exploration_recommendation(
        self, state: MonitorState
    ) -> Optional[str]:
        """Get recommendation on whether to explore.

        Args:
            state: MonitorState for the session

        Returns:
            Recommendation string, or None
        """
        if not isinstance(state, ExplorationSchedulerState):
            return None

        if not state.in_exploration_mode:
            return None

        current_iteration = len(state.success_rates)
        exploration_duration = (
            current_iteration - (state.exploration_mode_start_iteration or 0)
        )

        if exploration_duration < self.exploration_mode_duration:
            return "EXPLORING: Try alternative strategy"

        # Exploration duration over
        state.in_exploration_mode = False
        state.exploration_mode_start_iteration = None
        return "EXPLORATION_COMPLETE: Evaluate results and decide strategy"

    def create_or_get_exploration_state(
        self, session_id: str, task_id: str, tenant_id: str
    ) -> ExplorationSchedulerState:
        """Create or get ExplorationSchedulerState for a session.

        Args:
            session_id: Session ID
            task_id: Task ID
            tenant_id: Tenant ID

        Returns:
            ExplorationSchedulerState for the session
        """
        if session_id not in self.session_states:
            self.session_states[session_id] = ExplorationSchedulerState(
                session_id=session_id,
                task_id=task_id,
                tenant_id=tenant_id,
            )
        return self.session_states[session_id]
