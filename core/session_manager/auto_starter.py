"""SessionAutoStarter: Auto-initialize sessions on split triggers (ADR-0472).

Monitors running tasks, detects split triggers, and autonomously starts new
sessions with checkpoint injection — zero operator intervention.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, Any
from dataclasses import dataclass

from .lifecycle_manager import SessionLifecycleManager, SplitTrigger
from .checkpoint_manager import CheckpointManager
from .retry_engine import RetryEngine, RetryPolicy

logger = logging.getLogger(__name__)


class TaskStateLock:
    """Per-task synchronization to prevent race conditions (CRITICAL-003 fix)."""
    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    async def acquire(self, task_id: str) -> asyncio.Lock:
        """Get or create lock for task_id."""
        async with self._global_lock:
            if task_id not in self._locks:
                self._locks[task_id] = asyncio.Lock()
            return self._locks[task_id]


@dataclass
class SessionInitRequest:
    """Request to initialize a new session from checkpoint."""
    checkpoint_hash: str
    goal: str
    goal_hash: str
    phase: str
    audit_trail_hash: str


class SessionAutoStarter:
    """Auto-start sessions on split triggers (ADR-0472).

    Monitors task progress, detects when sessions need splitting,
    creates checkpoints, and autonomously initializes new sessions.
    """

    def __init__(
        self,
        lifecycle_mgr: SessionLifecycleManager,
        checkpoint_mgr: CheckpointManager,
        max_retries: int = 3,
        retry_backoff_base: float = 1.0,
    ):
        """Initialize SessionAutoStarter.

        Args:
            lifecycle_mgr: SessionLifecycleManager instance
            checkpoint_mgr: CheckpointManager instance
            max_retries: Max retry attempts for session init
            retry_backoff_base: Base for exponential backoff (seconds)
        """
        self.lifecycle_mgr = lifecycle_mgr
        self.checkpoint_mgr = checkpoint_mgr
        self.max_retries = max_retries
        self.retry_backoff_base = retry_backoff_base

        # Task monitoring state
        self.task_states: dict[str, dict[str, Any]] = {}
        # CRITICAL-003 fix: Per-task synchronization
        self._state_lock = TaskStateLock()
        # CRITICAL-005 fix: Retry engine for transient failure handling
        retry_policy = RetryPolicy(
            max_attempts=max_retries,
            backoff_base_sec=retry_backoff_base,
            backoff_max_sec=60.0,
            jitter=True,
        )
        self.retry_engine = RetryEngine(policy=retry_policy)

    async def on_task_start(
        self,
        task_id: str,
        goal: str,
        tenant_id: str = "_default",
    ) -> str:
        """Called when a long-running task starts.

        Initializes baseline checkpoint and returns first session_id.

        Args:
            task_id: Unique task identifier
            goal: Task goal/instruction
            tenant_id: Tenant ID for isolation

        Returns:
            Initial session_id (operator should have created this)
        """
        # CRITICAL-003 fix: Use lock for initial state setup
        lock = await self._state_lock.acquire(task_id)
        async with lock:
            session_id = f"session_{task_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

            # CRITICAL-008 fix: Validate tenant_id
            if not self._is_valid_tenant_id(tenant_id):
                raise ValueError(f"Invalid tenant_id: {tenant_id}")

            # Initialize task state tracking
            self.task_states[task_id] = {
                "session_id": session_id,
                "goal": goal,
                "tenant_id": tenant_id,
                "start_time": datetime.now().timestamp(),
                "last_progress_time": datetime.now().timestamp(),
                "split_count": 0,
                "iterations": 0,
            }

            logger.info(
                f"[SessionAutoStarter] Task started: task={task_id} goal={goal[:50]} "
                f"session={session_id} tenant={tenant_id}"
            )

            return session_id

    async def on_task_progress(
        self,
        task_id: str,
        context_usage_pct: float,
        iterations: int,
        context: dict,
        audit_trail_hash: str,
    ) -> Optional[str]:
        """Called on each task iteration.

        Monitors context usage, token budget, stall status.
        If split trigger detected, auto-starts new session.

        Args:
            task_id: Task ID
            context_usage_pct: Context usage percentage (0-100)
            iterations: Total iterations so far
            context: Current context snapshot
            audit_trail_hash: Hash-chain from previous session

        Returns:
            New session_id if split occurred, None otherwise
        """
        # CRITICAL-003 fix: Acquire lock for this task to prevent race conditions
        lock = await self._state_lock.acquire(task_id)
        async with lock:
            if task_id not in self.task_states:
                logger.warning(f"[SessionAutoStarter] Unknown task: {task_id}")
                return None

            state = self.task_states[task_id]
            state["iterations"] = iterations

            # CRITICAL FIX: Get last_progress_time BEFORE updating it for stall check
            now = datetime.now().timestamp()
            last_progress_time = state.get("last_progress_time", now)

            # Query split decision (use OLD last_progress_time BEFORE resetting)
            split_decision = self.lifecycle_mgr.should_split_session(
                session_id=state["session_id"],
                context_usage_pct=context_usage_pct,
                phase=state.get("phase", "execution"),
                total_tokens_used=context.get("tokens_used", 0),
                iterations=iterations,
                last_progress_ts=last_progress_time,
            )

            # Now update progress timestamp AFTER split check
            state["last_progress_time"] = now

            if not split_decision.should_split:
                return None

            logger.info(
                f"[SessionAutoStarter] Split trigger detected: task={task_id} "
                f"trigger={split_decision.trigger.value} reason={split_decision.reason}"
            )

            # Create checkpoint
            try:
                checkpoint = await self.checkpoint_mgr.create_checkpoint(
                    session_id=state["session_id"],
                    goal=state["goal"],
                    context=context,
                    audit_trail_hash=audit_trail_hash,
                    phase=split_decision.new_phase or state.get("phase", "execution"),
                )
            except Exception as e:
                logger.exception(f"[SessionAutoStarter] Checkpoint creation failed: {e}")
                return None

            # Verify continuity (fail-closed)
            is_continuous = await self.lifecycle_mgr.verify_continuity(
                checkpoint,
                new_session_goal=state["goal"],
            )
            if not is_continuous:
                # CRITICAL-009 fix: Raise exception instead of silent None
                logger.error(
                    f"[SessionAutoStarter] Goal drift detected! "
                    f"Refusing to proceed. task={task_id}"
                )
                raise RuntimeError(f"Goal drift detected for task {task_id} — split rejected")

            # Auto-start new session
            new_session_id = await self._auto_start_session(task_id, checkpoint)
            if not new_session_id:
                return None

            # Update task state (still inside lock)
            state["session_id"] = new_session_id
            state["split_count"] += 1
            state["phase"] = split_decision.new_phase or "execution"

            logger.info(
                f"[SessionAutoStarter] Session split completed: "
                f"task={task_id} old={checkpoint.session_id} new={new_session_id} "
                f"split_count={state['split_count']}"
            )

            return new_session_id

    async def _auto_start_session(
        self,
        task_id: str,
        checkpoint,
    ) -> Optional[str]:
        """Auto-start a new session with checkpoint injection.

        Uses retry engine with exponential backoff for transient failures (CRITICAL-005 fix).

        Args:
            task_id: Task ID (for audit trail)
            checkpoint: Checkpoint to inject

        Returns:
            New session_id if successful, None otherwise
        """
        split_id = f"split_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        for attempt in range(self.retry_engine.policy.max_attempts):
            try:
                # Generate new session ID
                new_session_id = f"session_{task_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_split{attempt}"

                # TODO: Call actual session init API (to be wired in Phase 1.2)
                # new_session = await session_api.init_from_checkpoint(checkpoint)

                logger.info(
                    f"[SessionAutoStarter] New session initialized: "
                    f"session={new_session_id} checkpoint_hash={checkpoint.checkpoint_hash}"
                )

                # Record successful attempt
                await self.retry_engine.record_attempt(
                    task_id=task_id,
                    split_id=split_id,
                    attempt=attempt,
                    error=None,
                    success=True,
                )

                return new_session_id

            except Exception as e:
                # Record failed attempt
                await self.retry_engine.record_attempt(
                    task_id=task_id,
                    split_id=split_id,
                    attempt=attempt,
                    error=e,
                    success=False,
                )

                # CRITICAL-005 fix: Use retry engine to decide if we should retry
                should_retry = await self.retry_engine.should_retry(
                    task_id=task_id,
                    split_id=split_id,
                    attempt=attempt,
                    error=e,
                )

                if should_retry:
                    # Calculate backoff with jitter
                    backoff_sec = await self.retry_engine.get_backoff_delay(attempt)
                    logger.warning(
                        f"[SessionAutoStarter] Session init failed (attempt {attempt + 1}), "
                        f"retrying in {backoff_sec:.1f}s: {e}"
                    )
                    await asyncio.sleep(backoff_sec)
                else:
                    logger.error(
                        f"[SessionAutoStarter] Session init failed after {attempt + 1} attempts "
                        f"(error is non-retryable or max attempts reached)"
                    )
                    return None

        return None

    async def on_task_complete(
        self,
        task_id: str,
    ) -> dict:
        """Called when task completes.

        Returns final session metadata for audit trail.

        Args:
            task_id: Task ID

        Returns:
            Completion metadata (session_id, split_count, total_duration)
        """
        # CRITICAL-003 fix: Use lock for final state read
        lock = await self._state_lock.acquire(task_id)
        async with lock:
            if task_id not in self.task_states:
                logger.warning(f"[SessionAutoStarter] Unknown task on completion: {task_id}")
                return {}

            state = self.task_states[task_id]
            total_duration = datetime.now().timestamp() - state["start_time"]

            logger.info(
                f"[SessionAutoStarter] Task completed: task={task_id} "
                f"splits={state['split_count']} duration={total_duration:.1f}s"
            )

            result = {
                "task_id": task_id,
                "final_session_id": state["session_id"],
                "splits": state["split_count"],
                "duration_seconds": total_duration,
                "iterations": state["iterations"],
            }

            # Clean up state after completion
            del self.task_states[task_id]

            return result

    def get_task_state(self, task_id: str) -> Optional[dict]:
        """Get current task/session state (for monitoring).

        Args:
            task_id: Task ID

        Returns:
            Task state dict or None
        """
        return self.task_states.get(task_id)

    @staticmethod
    def _is_valid_tenant_id(tenant_id: str) -> bool:
        """Validate tenant_id format (CRITICAL-008 fix: tenant isolation).

        Args:
            tenant_id: Tenant ID to validate

        Returns:
            True if valid, False otherwise
        """
        # Allowlist: alphanumeric, underscore, hyphen only
        if not tenant_id:
            return False
        return all(c.isalnum() or c in ('_', '-') for c in tenant_id)
