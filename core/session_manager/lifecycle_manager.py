"""SessionLifecycleManager: autonomous session splitting & resuming (ADR-0471)."""

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class SplitTrigger(Enum):
    NONE = "none"
    CONTEXT_LIMIT = "context_limit"  # 85% → 95%
    PHASE_EXIT = "phase_exit"
    TOKEN_BUDGET = "token_budget"
    STALLED = "stalled"
    ITERATION_CAP = "iteration_cap"
    EXPLICIT_MILESTONE = "explicit_milestone"


@dataclass
class Checkpoint:
    """Immutable session checkpoint (ADR-0471)."""
    session_id: str
    goal: str
    goal_hash: str
    timestamp: float
    context_reduction_pct: float
    context_tokens_used: int
    audit_trail_hash: str  # Hash-chain from previous session
    checkpoint_hash: str = field(default="")
    phase: str = field(default="execution")

    def compute_hash(self) -> str:
        """Compute SHA256 of (goal + context_reduction + timestamp)."""
        hashable = f"{self.goal}|{self.context_reduction_pct}|{self.timestamp}"
        return hashlib.sha256(hashable.encode()).hexdigest()


@dataclass
class SplitDecision:
    """Decision to split or not."""
    should_split: bool
    trigger: SplitTrigger
    reason: str = ""
    new_phase: Optional[str] = None


class SessionLifecycleManager:
    """Autonomous session management without operator intervention (ADR-0471)."""

    def __init__(
        self,
        context_limit_pct: float = 0.85,
        token_budget_daily: int = 500000,
        stall_threshold_sec: int = 1800,  # 30 min
        iteration_cap: int = 50,
    ):
        self.context_limit_pct = context_limit_pct
        self.token_budget_daily = token_budget_daily
        self.stall_threshold_sec = stall_threshold_sec
        self.iteration_cap = iteration_cap

        self.checkpoints: dict[str, Checkpoint] = {}  # session_id → checkpoint
        self.last_progress_ts: dict[str, float] = {}  # session_id → timestamp

    def should_split_session(
        self,
        session_id: str,
        context_usage_pct: float,
        phase: str,
        total_tokens_used: int,
        iterations: int,
        last_progress_ts: float,
    ) -> SplitDecision:
        """Decide autonomously if session should split (ADR-0471)."""
        now = datetime.now().timestamp()

        # Trigger 1: Context Limit (85% → 95%)
        if context_usage_pct >= self.context_limit_pct:
            return SplitDecision(
                should_split=True,
                trigger=SplitTrigger.CONTEXT_LIMIT,
                reason=f"Context at {context_usage_pct}%",
            )

        # Trigger 2: Phase Exit (operator/task signals)
        # (Would be set via explicit call)

        # Trigger 3: Token Budget
        if total_tokens_used >= self.token_budget_daily:
            return SplitDecision(
                should_split=True,
                trigger=SplitTrigger.TOKEN_BUDGET,
                reason=f"Daily budget exhausted ({total_tokens_used} tokens)",
            )

        # Trigger 4: Stalled (no progress for 30 min)
        time_since_progress = now - last_progress_ts
        if time_since_progress >= self.stall_threshold_sec:
            logger.warning(f"[SessionLifecycle] Session {session_id} stalled for {time_since_progress}s")
            return SplitDecision(
                should_split=True,
                trigger=SplitTrigger.STALLED,
                reason=f"No progress for {time_since_progress}s",
            )

        # Trigger 5: Iteration Cap
        if iterations >= self.iteration_cap:
            return SplitDecision(
                should_split=True,
                trigger=SplitTrigger.ITERATION_CAP,
                reason=f"Iteration cap ({self.iteration_cap}) reached",
            )

        # No split needed
        return SplitDecision(
            should_split=False,
            trigger=SplitTrigger.NONE,
        )

    async def create_checkpoint(
        self,
        session_id: str,
        goal: str,
        context: dict,
        audit_trail_hash: str,
        phase: str = "execution",
    ) -> Checkpoint:
        """Create immutable checkpoint for resuming (ADR-0471)."""
        now = datetime.now().timestamp()
        context_tokens = context.get("tokens_used", 0)
        context_total = context.get("tokens_available", 100000)
        context_reduction_pct = (context_tokens / context_total * 100) if context_total > 0 else 0

        goal_hash = hashlib.sha256(goal.encode()).hexdigest()

        checkpoint = Checkpoint(
            session_id=session_id,
            goal=goal,
            goal_hash=goal_hash,
            timestamp=now,
            context_reduction_pct=context_reduction_pct,
            context_tokens_used=context_tokens,
            audit_trail_hash=audit_trail_hash,
            phase=phase,
        )

        checkpoint.checkpoint_hash = checkpoint.compute_hash()

        # Store checkpoint
        self.checkpoints[session_id] = checkpoint

        logger.info(
            f"[SessionLifecycle] Checkpoint created: "
            f"session={session_id} goal_hash={goal_hash} "
            f"context_reduction={context_reduction_pct}%"
        )

        return checkpoint

    async def verify_continuity(
        self,
        old_checkpoint: Checkpoint,
        new_session_goal: str,
    ) -> bool:
        """Verify goal continuity between sessions (fail-closed, ADR-0471)."""
        old_goal_hash = old_checkpoint.goal_hash
        new_goal_hash = hashlib.sha256(new_session_goal.encode()).hexdigest()

        if old_goal_hash != new_goal_hash:
            logger.error(
                f"[SessionLifecycle] GOAL DRIFT DETECTED: "
                f"old={old_goal_hash} new={new_goal_hash} — FAILING CLOSED"
            )
            return False  # Fail-closed: deny resume

        logger.debug(f"[SessionLifecycle] Continuity verified: goal_hash={old_goal_hash}")
        return True

    def signal_phase_exit(self, session_id: str, next_phase: Optional[str] = None) -> SplitDecision:
        """Operator/task signals phase exit → split (ADR-0471)."""
        return SplitDecision(
            should_split=True,
            trigger=SplitTrigger.PHASE_EXIT,
            reason="Explicit phase exit",
            new_phase=next_phase,
        )

    def reset_progress(self, session_id: str):
        """Reset stall timer (call when task makes progress)."""
        self.last_progress_ts[session_id] = datetime.now().timestamp()
