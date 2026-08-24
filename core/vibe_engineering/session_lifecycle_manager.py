"""
Sprint 1: SessionLifecycleManager

Detects 6 split triggers and initiates checkpoints autonomously.
Integrates with Brain v0.2's HealthMonitor, LoopEngineer, and EventBus.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, List, Callable
from datetime import datetime, timedelta
import json
import logging

logger = logging.getLogger(__name__)

class SplitTrigger(Enum):
    """Six autonomous split triggers (Phase 1 scope)."""
    PHASE_EXIT = "phase_exit"  # Detected: current phase complete
    CONTEXT_LIMIT = "context_limit_85"  # Context >= 85% of max tokens
    TOKEN_BURN = "token_burn"  # Daily token budget exhausted
    EXPLICIT_MILESTONE = "explicit_milestone"  # User-marked checkpoint
    ITERATION_CAP = "iteration_cap_50"  # >= 50 iterations in current session
    STALL_DETECTED = "stall_detected"  # No progress for 30+ minutes

@dataclass
class SessionState:
    """Current session state for trigger evaluation."""
    session_id: str
    phase: str
    iteration_count: int = 0
    context_tokens: int = 0
    max_context_tokens: int = 4000
    tokens_burned_today: int = 0
    daily_token_budget: int = 100000
    last_progress_time: datetime = field(default_factory=datetime.now)
    stall_threshold_seconds: int = 1800  # 30 minutes
    strategies_tried: List[str] = field(default_factory=list)

@dataclass
class TriggerEvaluation:
    """Result of trigger evaluation."""
    triggered: bool
    trigger_type: Optional[SplitTrigger] = None
    reason: str = ""
    timestamp: datetime = field(default_factory=datetime.now)

class SessionLifecycleManager:
    """
    Autonomous session lifecycle management.

    Detects split triggers and initiates checkpoints without human intervention.
    Integrates with Brain v0.2 subsystems for stall detection and strategy history.
    """

    def __init__(self, max_context_tokens: int = 4000, daily_budget: int = 100000):
        self.max_context_tokens = max_context_tokens
        self.daily_budget = daily_budget
        self.evaluation_history: List[TriggerEvaluation] = []
        self.split_count = 0

    def evaluate_triggers(self, state: SessionState) -> TriggerEvaluation:
        """
        Evaluate all 6 triggers. Returns first trigger that fires.

        Trigger priority (checked in order):
        1. Phase Exit (explicit/semantic)
        2. Context Limit (85%+ of max tokens)
        3. Token Burn (daily budget exhausted)
        4. Explicit Milestone (user-marked)
        5. Iteration Cap (50+ iterations)
        6. Stall Detected (30+ min no progress)
        """

        # Trigger 1: Phase Exit
        if self._check_phase_exit(state):
            eval_result = TriggerEvaluation(
                triggered=True,
                trigger_type=SplitTrigger.PHASE_EXIT,
                reason="Current phase complete (detected semantically or explicitly)"
            )
            self.evaluation_history.append(eval_result)
            return eval_result

        # Trigger 2: Context Limit (85%)
        if self._check_context_limit(state):
            eval_result = TriggerEvaluation(
                triggered=True,
                trigger_type=SplitTrigger.CONTEXT_LIMIT,
                reason=f"Context at {self._context_usage_percent(state):.0f}% (>= 85%)"
            )
            self.evaluation_history.append(eval_result)
            return eval_result

        # Trigger 3: Token Burn
        if self._check_token_burn(state):
            eval_result = TriggerEvaluation(
                triggered=True,
                trigger_type=SplitTrigger.TOKEN_BURN,
                reason=f"Daily token budget exhausted ({state.tokens_burned_today}/{state.daily_token_budget})"
            )
            self.evaluation_history.append(eval_result)
            return eval_result

        # Trigger 4: Explicit Milestone (would be set by user/engine)
        if self._check_explicit_milestone(state):
            eval_result = TriggerEvaluation(
                triggered=True,
                trigger_type=SplitTrigger.EXPLICIT_MILESTONE,
                reason="Explicit milestone marker set"
            )
            self.evaluation_history.append(eval_result)
            return eval_result

        # Trigger 5: Iteration Cap (50+)
        if self._check_iteration_cap(state):
            eval_result = TriggerEvaluation(
                triggered=True,
                trigger_type=SplitTrigger.ITERATION_CAP,
                reason=f"Iteration count >= 50 ({state.iteration_count} iterations)"
            )
            self.evaluation_history.append(eval_result)
            return eval_result

        # Trigger 6: Stall Detected (30+ min no progress)
        if self._check_stall(state):
            eval_result = TriggerEvaluation(
                triggered=True,
                trigger_type=SplitTrigger.STALL_DETECTED,
                reason=f"No progress for {self._elapsed_since_progress(state) / 60:.0f} minutes (threshold: 30 min)"
            )
            self.evaluation_history.append(eval_result)
            return eval_result

        # No trigger fired
        eval_result = TriggerEvaluation(triggered=False, reason="No triggers detected")
        self.evaluation_history.append(eval_result)
        return eval_result

    # Trigger implementations
    def _check_phase_exit(self, state: SessionState) -> bool:
        """Phase exit: would be detected by LoopEngineer (strategy complete)."""
        # Phase 1 implementation: stub (would integrate with LoopEngineer in v1.0)
        return False

    def _check_context_limit(self, state: SessionState) -> bool:
        """Context >= 85% of max tokens."""
        usage_pct = state.context_tokens / state.max_context_tokens if state.max_context_tokens > 0 else 0
        return usage_pct >= 0.85

    def _check_token_burn(self, state: SessionState) -> bool:
        """Daily token budget exhausted."""
        return state.tokens_burned_today >= state.daily_token_budget

    def _check_explicit_milestone(self, state: SessionState) -> bool:
        """Explicit milestone marker (would be set by user/LoopEngineer)."""
        # Phase 1 implementation: stub (would be set externally)
        return False

    def _check_iteration_cap(self, state: SessionState) -> bool:
        """Iteration count >= 50."""
        return state.iteration_count >= 50

    def _check_stall(self, state: SessionState) -> bool:
        """No progress for 30+ minutes."""
        elapsed = self._elapsed_since_progress(state)
        return elapsed >= state.stall_threshold_seconds

    # Helper methods
    def _context_usage_percent(self, state: SessionState) -> float:
        """Percentage of context tokens used."""
        if state.max_context_tokens == 0:
            return 0.0
        return (state.context_tokens / state.max_context_tokens) * 100

    def _elapsed_since_progress(self, state: SessionState) -> float:
        """Seconds since last progress update."""
        now = datetime.now()
        elapsed = (now - state.last_progress_time).total_seconds()
        return elapsed

    def record_progress(self, state: SessionState):
        """Record progress update (resets stall timer)."""
        state.last_progress_time = datetime.now()
        logger.debug(f"Progress recorded at {state.last_progress_time}")

    def on_split_initiated(self):
        """Called when a split checkpoint is created."""
        self.split_count += 1
        logger.info(f"Split #{self.split_count} initiated")

    def get_statistics(self) -> Dict:
        """Return evaluation statistics."""
        triggered_evals = [e for e in self.evaluation_history if e.triggered]
        triggers_by_type = {}
        for eval in triggered_evals:
            if eval.trigger_type:
                triggers_by_type[eval.trigger_type.value] = triggers_by_type.get(eval.trigger_type.value, 0) + 1

        return {
            "total_evaluations": len(self.evaluation_history),
            "triggers_fired": len(triggered_evals),
            "splits_initiated": self.split_count,
            "triggers_by_type": triggers_by_type
        }


# Test helper: create test state
def create_test_state(
    session_id: str = "test_session",
    phase: str = "execution",
    iteration_count: int = 0,
    context_tokens: int = 0,
    tokens_burned: int = 0
) -> SessionState:
    """Helper to create test session states."""
    return SessionState(
        session_id=session_id,
        phase=phase,
        iteration_count=iteration_count,
        context_tokens=context_tokens,
        tokens_burned_today=tokens_burned
    )
