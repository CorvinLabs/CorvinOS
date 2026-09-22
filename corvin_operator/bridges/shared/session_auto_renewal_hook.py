"""session_auto_renewal_hook.py — Auto-session split on token budget exhaustion (ADR-0407, ADR-0668).

Monitors per-session token usage and autonomously spawns new sessions with checkpoint
injection when the token budget approaches/exceeds its limit.

Used by adapter.py in the Discord Voice Bridge (and other bridges).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

logger = logging.getLogger(__name__)


class SessionAutoRenewalHook:
    """Autonomously split sessions when token budget is exceeded."""

    WARN_THRESHOLD = 0.85  # 85% → warn user
    CRITICAL_THRESHOLD = 0.95  # 95% → prepare split
    SPLIT_THRESHOLD = 0.98  # 98% or >quota → trigger split

    def __init__(
        self,
        context_budget_module=None,
        auto_starter_module=None,
        lifecycle_manager=None,
        checkpoint_manager=None,
    ):
        """Initialize the renewal hook.

        Args:
            context_budget_module: Optional context_budget module (import locally if None)
            auto_starter_module: Optional SessionAutoStarter module
            lifecycle_manager: SessionLifecycleManager instance
            checkpoint_manager: CheckpointManager instance
        """
        self.context_budget = context_budget_module
        self.auto_starter = auto_starter_module
        self.lifecycle_manager = lifecycle_manager
        self.checkpoint_manager = checkpoint_manager

    async def check_and_maybe_split_session(
        self,
        chat_key: str,
        tokens_used: int,
        session_id: str,
        goal: str = "",
        turn_id: Optional[str] = None,
    ) -> Tuple[str, Optional[str]]:
        """Check token budget; split session if needed.

        Args:
            chat_key: Discord channel/user ID (becomes task_id)
            tokens_used: Tokens consumed in this turn
            session_id: Current session ID
            goal: User's goal/instruction (for checkpoint)
            turn_id: Unique turn identifier

        Returns:
            (action: str, new_session_id: Optional[str])
            - action: "ok" | "warn" | "critical" | "split" | "split_failed"
            - new_session_id: If split occurred, the new session_id
        """
        if not self.context_budget:
            # Budget tracking not available; continue silently
            return ("ok", None)

        if not turn_id:
            turn_id = f"turn_{datetime.now().isoformat()}"

        try:
            # 1. Account this turn's tokens
            rec = self.context_budget.account_turn(
                session_id,
                turn_id=turn_id,
                tokens=tokens_used
            )
        except KeyError:
            # Session not registered yet; register with 15M quota
            logger.info(f"[SessionRenewal] Registering new session {session_id} (15M quota)")
            self.context_budget.register_session_budget(
                session_id,
                quota=15_000_000,  # 15M token limit from requirement
                oom_policy="reject"  # Fail-closed
            )
            rec = self.context_budget.account_turn(
                session_id,
                turn_id=turn_id,
                tokens=tokens_used
            )

        # 2. Check budget status
        status = self.context_budget.check_budget(session_id)
        headroom_pct = status.get("headroom_pct", 1.0)
        used = status.get("used", 0)
        quota = status.get("quota", 15_000_000)

        logger.debug(
            f"[SessionRenewal] Budget check: {used}/{quota} tokens "
            f"({100*(1-headroom_pct):.1f}% used, {100*headroom_pct:.1f}% headroom)"
        )

        # 3. Decide action based on headroom
        if headroom_pct <= 0.0 or status.get("action") in ("reject", "evict"):
            # **CRITICAL**: Over budget — split immediately
            logger.critical(
                f"[SessionRenewal] SPLIT TRIGGER: budget over limit "
                f"({used}/{quota}, policy={status.get('action')})"
            )
            new_session_id = await self.trigger_auto_session_split(
                chat_key=chat_key,
                old_session_id=session_id,
                goal=goal,
            )
            if new_session_id:
                return ("split", new_session_id)
            else:
                logger.error("[SessionRenewal] Split failed; continuing in same session")
                return ("split_failed", None)

        elif headroom_pct <= (1.0 - self.SPLIT_THRESHOLD):
            # At/near 98% — prepare for split
            logger.warning(
                f"[SessionRenewal] At split threshold: {100*(1-headroom_pct):.1f}% "
                f"({self.SPLIT_THRESHOLD*100:.0f}%) — will split on next turn"
            )
            return ("critical", None)

        elif headroom_pct <= (1.0 - self.CRITICAL_THRESHOLD):
            # At ~95% — send warning
            logger.warning(
                f"[SessionRenewal] Token warning: {100*(1-headroom_pct):.1f}% used "
                f"({used}/{quota})"
            )
            return ("warn", None)

        # Below 85% — normal operation
        return ("ok", None)

    async def trigger_auto_session_split(
        self,
        chat_key: str,
        old_session_id: str,
        goal: str = "",
    ) -> Optional[str]:
        """Create checkpoint and spawn new session.

        Args:
            chat_key: Discord channel/user ID
            old_session_id: Current session that's over-budget
            goal: User's original goal

        Returns:
            New session_id if successful, None otherwise
        """
        if not self.auto_starter or not self.lifecycle_manager or not self.checkpoint_manager:
            logger.error("[SessionRenewal] Auto-starter not initialized; cannot split")
            return None

        try:
            # 1. Build checkpoint from current state
            status = self.context_budget.check_budget(old_session_id)
            used = status.get("used", 0)
            quota = status.get("quota", 15_000_000)

            context = {
                "tokens_used": used,
                "tokens_available": quota,
                "headroom_pct": status.get("headroom_pct", 0),
                "chat_key": chat_key,
            }

            # Create checkpoint via lifecycle manager
            checkpoint = await self.lifecycle_manager.create_checkpoint(
                session_id=old_session_id,
                goal=goal,
                context=context,
                audit_trail_hash=hashlib.sha256(goal.encode()).hexdigest(),
                phase="execution"
            )

            if not checkpoint:
                logger.error(f"[SessionRenewal] Checkpoint creation failed for {old_session_id}")
                return None

            # 2. Persist checkpoint
            saved = await self.checkpoint_manager.save_checkpoint(checkpoint)
            if not saved:
                logger.error(f"[SessionRenewal] Checkpoint save failed for {old_session_id}")
                return None

            logger.info(f"[SessionRenewal] Checkpoint saved: {checkpoint.checkpoint_hash}")

            # 3. Auto-start new session via SessionAutoStarter
            task_id = f"discord_{chat_key}"  # Map chat_key → task_id

            # Initialize task if not yet tracked
            if task_id not in self.auto_starter.task_states:
                await self.auto_starter.on_task_start(
                    task_id=task_id,
                    goal=goal,
                    tenant_id="_default"
                )

            # Trigger split via on_task_progress (will call lifecycle_manager.should_split_session)
            new_session_id = await self.auto_starter.on_task_progress(
                task_id=task_id,
                context_usage_pct=100 - (status.get("headroom_pct", 0) * 100),  # Inverted
                iterations=status.get("turn_count", 0),
                context=context,
                audit_trail_hash=checkpoint.audit_trail_hash,
                goal=goal
            )

            if not new_session_id:
                logger.error(f"[SessionRenewal] Auto-start failed for task {task_id}")
                return None

            # 4. Register budget for new session
            self.context_budget.register_session_budget(
                new_session_id,
                quota=15_000_000,
                oom_policy="reject"
            )

            logger.info(
                f"[SessionRenewal] Session split complete: {old_session_id} → {new_session_id}"
            )
            return new_session_id

        except Exception as e:
            logger.exception(f"[SessionRenewal] trigger_auto_session_split failed: {e}")
            return None


# Singleton instance (lazy-loaded)
_hook_instance: Optional[SessionAutoRenewalHook] = None


def get_renewal_hook() -> SessionAutoRenewalHook:
    """Get or create the singleton renewal hook."""
    global _hook_instance
    if _hook_instance is None:
        # Lazy-load modules to avoid circular imports
        try:
            import context_budget as _cb
        except ImportError:
            _cb = None

        try:
            from core.session_manager.auto_starter import SessionAutoStarter
            from core.session_manager.lifecycle_manager import SessionLifecycleManager
            from core.session_manager.checkpoint_manager import CheckpointManager

            lifecycle_mgr = SessionLifecycleManager()
            checkpoint_mgr = CheckpointManager()
        except ImportError:
            SessionAutoStarter = None
            lifecycle_mgr = None
            checkpoint_mgr = None

        _hook_instance = SessionAutoRenewalHook(
            context_budget_module=_cb,
            auto_starter_module=SessionAutoStarter,
            lifecycle_manager=lifecycle_mgr,
            checkpoint_manager=checkpoint_mgr,
        )

    return _hook_instance
