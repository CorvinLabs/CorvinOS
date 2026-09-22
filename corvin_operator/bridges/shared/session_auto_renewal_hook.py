"""session_auto_renewal_hook.py — Auto-session split on token budget exhaustion (ADR-0407, ADR-0668).

Monitors per-session token usage and autonomously spawns new sessions with checkpoint
injection when the token budget approaches/exceeds its limit.

Integrates ADR-0407 Load-Bearing Anchor: captures decision points and constraints
when a session splits, persisting them into the anchor store so they survive context
truncation in the new session.

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
from dataclasses import dataclass

from .session_plan_export import get_plan_exporter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TokenSavingsMetrics:
    """Immutable token-savings metrics (ADR-0668).

    Tracks baseline (legacy cost) vs actual (optimized cost) tokens.
    Per ADR-0668 Amendment, baseline should derive from real engine.span.* splits.
    This class uses a heuristic for checkpoint creation.
    """
    baseline_tokens: int    # Estimated tokens without optimization
    actual_tokens: int      # Real tokens consumed
    savings_tokens: int     # baseline - actual
    savings_pct: float      # (savings / baseline) * 100


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

    @staticmethod
    def _compute_token_savings(used_tokens: int, quota: int) -> TokenSavingsMetrics:
        """Compute token-savings metrics (ADR-0668).

        Heuristic: assumes legacy router would use 15% more tokens.
        Per ADR-0668 Amendment, this should derive from real engine.span.* splits
        in production.

        Args:
            used_tokens: Actual tokens consumed
            quota: Total quota available (for context)

        Returns:
            TokenSavingsMetrics with baseline, actual, savings_tokens, savings_pct
        """
        # Heuristic: legacy router overhead = 15%
        baseline_tokens = int(used_tokens * 1.15) if used_tokens > 0 else 0
        actual_tokens = used_tokens
        savings_tokens = baseline_tokens - actual_tokens
        savings_pct = (
            (savings_tokens / baseline_tokens * 100)
            if baseline_tokens > 0
            else 0.0
        )

        return TokenSavingsMetrics(
            baseline_tokens=baseline_tokens,
            actual_tokens=actual_tokens,
            savings_tokens=savings_tokens,
            savings_pct=savings_pct,
        )

    async def _capture_load_bearing_anchor(
        self,
        old_session_id: str,
        new_session_id: str,
        checkpoint_hash: str,
        goal: str = "",
        context: Optional[Dict[str, Any]] = None,
        tenant_id: str = "_default",
    ) -> bool:
        """Capture load-bearing anchor facts when session splits (ADR-0407 amendment).

        Persists decision point and constraint facts into the anchor store so they
        survive context truncation in the new session. Non-blocking: a failure here
        must never break the session split or a turn. Tenant-scoped to prevent cross-tenant
        pollution in multi-tenant setups (ADR-0007).

        Args:
            old_session_id: Session being split
            new_session_id: New session ID
            checkpoint_hash: Checkpoint identifier
            goal: Original goal/instruction
            context: Optional context data
            tenant_id: Tenant ID for anchor scoping (default: "_default")

        Returns:
            True if anchor was captured (or feature flag off), False on error
        """
        try:
            # Validate tenant_id
            if not tenant_id or not isinstance(tenant_id, str):
                logger.warning(f"[SessionRenewal] Invalid tenant_id: {tenant_id}, using _default")
                tenant_id = "_default"

            # Check if anchor feature is enabled
            try:
                from core.console.corvin_core.feature_flags import is_enabled
                if not is_enabled("cel_load_bearing_anchor", tenant_id=tenant_id):
                    return True  # Feature off; graceful no-op
            except ImportError:
                return True  # Feature flag module absent; graceful no-op

            # Import anchor module
            try:
                from corvin_operator.context_engineering import anchor
            except ImportError:
                logger.warning("[SessionRenewal] anchor module not available; skipping capture")
                return True  # Graceful degradation when anchor module absent

            # Capture session split reason as a constraint fact
            if checkpoint_hash:
                split_fact = f"Session split: checkpoint {checkpoint_hash[:12]} → new session {new_session_id[:12]}"
                try:
                    anchor.add_fact(
                        tenant_id=tenant_id,
                        session_key=new_session_id,
                        kind="constraint",
                        text=split_fact,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"[SessionRenewal] Failed to capture split constraint "
                        f"(tenant={tenant_id}): {e}"
                    )

            # Capture original goal if provided
            if goal and goal.strip():
                try:
                    anchor.add_fact(
                        tenant_id=tenant_id,
                        session_key=new_session_id,
                        kind="goal",
                        text=goal,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"[SessionRenewal] Failed to capture goal (tenant={tenant_id}): {e}"
                    )

            # Capture token budget context as a constraint (informational)
            if context and isinstance(context, dict):
                tokens_used = context.get("tokens_used", 0)
                tokens_available = context.get("tokens_available", 0)
                if tokens_available > 0:
                    budget_fact = (
                        f"Token budget: {tokens_used:,}/{tokens_available:,} "
                        f"({100*tokens_used/tokens_available:.1f}%)"
                    )
                    try:
                        anchor.add_fact(
                            tenant_id=tenant_id,
                            session_key=new_session_id,
                            kind="constraint",
                            text=budget_fact,
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            f"[SessionRenewal] Failed to capture budget (tenant={tenant_id}): {e}"
                        )

            logger.info(
                f"[SessionRenewal] Load-bearing anchor captured for {new_session_id} "
                f"(tenant={tenant_id})"
            )
            return True

        except Exception as e:  # noqa: BLE001 — anchor capture is best-effort
            logger.exception(
                f"[SessionRenewal] _capture_load_bearing_anchor failed (tenant={tenant_id}): {e}"
            )
            return True  # Graceful failure; never break a session split

    async def trigger_auto_session_split(
        self,
        chat_key: str,
        old_session_id: str,
        goal: str = "",
    ) -> Optional[str]:
        """Create checkpoint and spawn new session.

        Transaction-based implementation with automatic rollback on failure (FIX A2).
        Ensures that either:
        - ALL operations succeed and new session is fully initialized, OR
        - ALL rollback and no partial state is left behind

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

        new_session_id = None  # Track new session for rollback

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

            logger.info(f"[SessionRenewal] New session created (pre-transaction): {new_session_id}")

            # === TRANSACTION BLOCK: all or nothing (FIX A2) ===
            # All following operations must succeed; on ANY failure, rollback new_session_id

            try:
                # 3.5. Transfer MEMORY.md to new session (ADR-0668)
                # CRITICAL: must succeed before continuing (FIX A2: with timeout)
                try:
                    from session_memory_pool import transfer_session_memory
                    memory_transferred = await asyncio.wait_for(
                        transfer_session_memory(
                            old_session_id=old_session_id,
                            new_session_id=new_session_id,
                            goal=goal
                        ),
                        timeout=10.0  # 10s timeout to prevent indefinite hang
                    )
                    if memory_transferred:
                        logger.info(
                            f"[SessionRenewal] Memory transferred: {old_session_id} → {new_session_id}"
                        )
                    else:
                        logger.error(f"[SessionRenewal] Memory transfer failed for {old_session_id}")
                        raise RuntimeError("Memory transfer returned False")
                except asyncio.TimeoutError:
                    logger.error(f"[SessionRenewal] Memory transfer timeout for {old_session_id}")
                    raise RuntimeError("Memory transfer timeout (>10s)")
                except Exception as e:  # noqa: BLE001
                    logger.error(f"[SessionRenewal] Memory transfer exception: {e}")
                    raise RuntimeError(f"Memory transfer failed: {e}")

                # 4. Capture load-bearing anchor facts (ADR-0407 amendment)
                # Non-blocking within transaction: log warning but continue on failure
                try:
                    await self._capture_load_bearing_anchor(
                        old_session_id=old_session_id,
                        new_session_id=new_session_id,
                        checkpoint_hash=checkpoint.checkpoint_id if checkpoint else "",
                        goal=goal,
                        context=context,
                    )
                    logger.info(
                        f"[SessionRenewal] Load-bearing anchor captured for {new_session_id}"
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[SessionRenewal] Load-bearing anchor capture failed: {e}")
                    # Continue; anchor is best-effort within the transaction

                # 5. Export and link plans across session boundary (ADR-0407 amendment)
                # Non-blocking within transaction: log warning but continue on failure
                try:
                    exporter = get_plan_exporter()
                    plan_mapping = await exporter.export_and_link_plans(
                        old_session_id=old_session_id,
                        new_session_id=new_session_id,
                        split_reason="token_budget",
                    )
                    if plan_mapping:
                        logger.info(
                            f"[SessionRenewal] Linked {len(plan_mapping)} plans "
                            f"to new session {new_session_id}"
                        )
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[SessionRenewal] Plan export failed (non-fatal): {e}")
                    # Continue; plan export is best-effort within the transaction

                # === END TRANSACTION BLOCK ===
                # All critical operations succeeded; now finalize new session

                # 6. Register budget for new session (finalization)
                self.context_budget.register_session_budget(
                    new_session_id,
                    quota=15_000_000,
                    oom_policy="reject"
                )

                logger.info(
                    f"[SessionRenewal] Session split complete: {old_session_id} → {new_session_id}"
                )

                # 7. **GDPR Art. 30/32: Emit audit event to hash-chain (load-bearing)**
                # Non-blocking: audit emit failure must never break the session split
                try:
                    from core.compliance.audit_backend import write_event
                    write_event(
                        event_type="session_split",
                        tenant_id="_default",
                        old_session_id=old_session_id,
                        new_session_id=new_session_id,
                        reason="token_budget_limit",
                        checkpoint_hash=(checkpoint.checkpoint_hash if checkpoint else ""),
                        goal=goal,
                        timestamp=datetime.now().isoformat() + "Z",
                    )
                    logger.info(
                        f"[SessionRenewal] Audit event emitted: session_split "
                        f"({old_session_id} → {new_session_id})"
                    )
                except ImportError:
                    logger.warning(
                        "[SessionRenewal] audit_backend module not available; "
                        "session split will not be audited (GDPR Art. 30/32 gap)"
                    )
                except Exception as audit_err:
                    logger.warning(
                        f"[SessionRenewal] Audit emit failed: {audit_err} "
                        f"(continuing anyway; non-blocking)"
                    )

                return new_session_id

            except Exception as e:
                # ROLLBACK: transaction failed; unregister new_session (FIX A2)
                logger.error(
                    f"[SessionRenewal] Transaction failed for new session {new_session_id}; "
                    f"initiating rollback: {e}"
                )
                try:
                    if new_session_id and hasattr(self.context_budget, 'unregister_session'):
                        self.context_budget.unregister_session(new_session_id)
                        logger.info(
                            f"[SessionRenewal] Rolled back session {new_session_id} "
                            f"(old session {old_session_id} still active)"
                        )
                except Exception as rollback_error:  # noqa: BLE001
                    logger.exception(
                        f"[SessionRenewal] Rollback failed for {new_session_id}: {rollback_error}"
                    )
                return None

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
            # ADR-0007: Tenant-scoped checkpoints — checkpoint_dir defaults to tenant-scoped path
            checkpoint_mgr = CheckpointManager(tenant_id="_default")
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
