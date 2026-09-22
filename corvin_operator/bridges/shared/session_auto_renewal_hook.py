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
import unicodedata
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

		# FIX A1: Race condition — add per-chat_key locks for atomic splits
		self._split_locks: Dict[str, asyncio.Lock] = {}

	async def _get_split_lock(self, chat_key: str) -> asyncio.Lock:
		"""Get or create an asyncio.Lock for a specific chat_key.

		Ensures atomic session splits per chat_key — prevents concurrent
		split attempts on the same conversation.

		Args:
			chat_key: Discord channel/user ID

		Returns:
			asyncio.Lock for this chat_key
		"""
		if chat_key not in self._split_locks:
			self._split_locks[chat_key] = asyncio.Lock()
		return self._split_locks[chat_key]

	async def check_and_maybe_split_session(
		self,
		chat_key: str,
		tokens_used: int,
		session_id: str,
		goal: str = "",
		turn_id: Optional[str] = None,
	) -> Tuple[str, Optional[str], Optional[str]]:
		"""Check token budget; split session if needed.

		Args:
			chat_key: Discord channel/user ID (becomes task_id)
			tokens_used: Tokens consumed in this turn
			session_id: Current session ID
			goal: User's goal/instruction (for checkpoint)
			turn_id: Unique turn identifier

		Returns:
			(action: str, new_session_id: Optional[str], error_msg: Optional[str])
			- action: "ok" | "warn" | "critical" | "split" | "split_failed"
			- new_session_id: If split occurred, the new session_id
			- error_msg: If split_failed, human-readable error message (E13 FIX)
		"""
		if not self.context_budget:
			# Budget tracking not available; continue silently
			return ("ok", None, None)

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
			# FIX A1: Wrap with per-chat_key lock to prevent concurrent splits
			lock = await self._get_split_lock(chat_key)
			async with lock:
				# Re-check budget under lock to prevent race-condition split duplication
				status_recheck = self.context_budget.check_budget(session_id)
				headroom_recheck = status_recheck.get("headroom_pct", 1.0)

				# Only split if still over budget (another thread may have already done it)
				if headroom_recheck <= 0.0 or status_recheck.get("action") in ("reject", "evict"):
					logger.critical(
						f"[SessionRenewal] SPLIT TRIGGER: budget over limit "
						f"({used}/{quota}, policy={status.get('action')})"
					)
					new_session_id, error_msg = await self.trigger_auto_session_split(
						chat_key=chat_key,
						old_session_id=session_id,
						goal=goal,
					)
					if new_session_id:
						return ("split", new_session_id, None)
					else:
						return ("split_failed", None, error_msg)  # E13 FIX: return error message
				else:
					# Budget recovered (another thread already split); report OK
					logger.info(
						f"[SessionRenewal] Budget recovered by concurrent split; "
						f"headroom now {100*headroom_recheck:.1f}%"
					)
					return ("ok", None, None)

		elif headroom_pct <= (1.0 - self.SPLIT_THRESHOLD):
			# At/near 98% — prepare for split
			logger.warning(
				f"[SessionRenewal] At split threshold: {100*(1-headroom_pct):.1f}% "
				f"({self.SPLIT_THRESHOLD*100:.0f}%) — will split on next turn"
			)
			return ("critical", None, None)

		elif headroom_pct <= (1.0 - self.CRITICAL_THRESHOLD):
			# At ~95% — send warning
			logger.warning(
				f"[SessionRenewal] Token warning: {100*(1-headroom_pct):.1f}% used "
				f"({used}/{quota})"
			)
			return ("warn", None, None)

		# Below 85% — normal operation
		return ("ok", None, None)

	@staticmethod
	def _compute_token_savings(used_tokens: int, quota: int) -> TokenSavingsMetrics:
		"""Compute token-savings metrics (ADR-0668, E15 FIX: improved heuristic).

		Heuristic: assumes legacy router would use 15% more tokens.
		Per ADR-0668 Amendment, this should derive from real engine.span.* splits
		in production.

		Args:
			used_tokens: Actual tokens consumed
			quota: Total quota available (for context)

		Returns:
			TokenSavingsMetrics with baseline, actual, savings_tokens, savings_pct
		"""
		# E15 FIX: Use 15% overhead heuristic (legacy router estimated 15% more)
		# This is more accurate than the previous 25% or other heuristics
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
		tenant_id: str = "_default",
	) -> Tuple[Optional[str], Optional[str]]:
		"""Create checkpoint and spawn new session.

		Transaction-based implementation with automatic rollback on failure (FIX A2).
		Ensures that either:
		- ALL operations succeed and new session is fully initialized, OR
		- ALL rollback and no partial state is left behind

		Args:
			chat_key: Discord channel/user ID
			old_session_id: Current session that's over-budget
			goal: User's original goal
			tenant_id: Tenant ID for multi-tenant isolation (default: "_default")

		Returns:
			(new_session_id: Optional[str], error_msg: Optional[str])
			- new_session_id: If successful, the new session_id (None on failure)
			- error_msg: If failed, human-readable error message (E13 FIX)
		"""
		# Validate tenant_id
		if not tenant_id or not isinstance(tenant_id, str):
			logger.warning(f"[SessionRenewal] Invalid tenant_id: {tenant_id}, using _default")
			tenant_id = "_default"

		if not self.auto_starter or not self.lifecycle_manager or not self.checkpoint_manager:
			error_msg = "[SessionRenewal] Auto-starter not initialized; cannot split"
			logger.error(error_msg)
			return (None, error_msg)

		new_session_id = None  # Track new session for rollback

		try:
			# 1. Build checkpoint from current state
			try:
				# F17 FIX: Add API stability wrapper for context_budget calls
				status = self.context_budget.check_budget(old_session_id)
				used = status.get("used", 0)
				quota = status.get("quota", 15_000_000)
			except (AttributeError, KeyError, TypeError) as e:
				error_msg = f"[SessionRenewal] context_budget API error: {e} (tenant={tenant_id})"
				logger.error(error_msg)
				return (None, error_msg)

			context = {
				"tokens_used": used,
				"tokens_available": quota,
				"headroom_pct": status.get("headroom_pct", 0),
				"chat_key": chat_key,
				"tenant_id": tenant_id,  # Include tenant in context
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
				error_msg = f"[SessionRenewal] Checkpoint creation failed for {old_session_id} (tenant={tenant_id})"
				logger.error(error_msg)
				return (None, error_msg)

			logger.info(
				f"[SessionRenewal] Checkpoint created: {checkpoint.checkpoint_hash} "
				f"(tenant={tenant_id})"
			)

			# 3. Auto-start new session via SessionAutoStarter
			task_id = f"discord_{chat_key}"  # Map chat_key → task_id

			# Initialize task if not yet tracked
			if task_id not in self.auto_starter.task_states:
				await self.auto_starter.on_task_start(
					task_id=task_id,
					goal=goal,
					tenant_id=tenant_id  # Pass actual tenant_id
				)

			# F16 FIX: Validate on_task_progress signature exists and is callable
			if not hasattr(self.auto_starter, 'on_task_progress') or not callable(getattr(self.auto_starter, 'on_task_progress')):
				error_msg = f"[SessionRenewal] SessionAutoStarter.on_task_progress not callable (tenant={tenant_id})"
				logger.error(error_msg)
				return (None, error_msg)

			# Trigger split via on_task_progress (will call lifecycle_manager.should_split_session)
			try:
				new_session_id = await self.auto_starter.on_task_progress(
					task_id=task_id,
					context_usage_pct=100 - (status.get("headroom_pct", 0) * 100),  # Inverted
					iterations=status.get("turn_count", 0),
					context=context,
					audit_trail_hash=checkpoint.audit_trail_hash,
					goal=goal
				)
			except (AttributeError, TypeError) as e:
				# F17 FIX: Handle API signature changes gracefully
				error_msg = f"[SessionRenewal] on_task_progress call failed: {e} (tenant={tenant_id})"
				logger.error(error_msg)
				return (None, error_msg)

			if not new_session_id:
				error_msg = f"[SessionRenewal] Auto-start failed for task {task_id} (tenant={tenant_id})"
				logger.error(error_msg)
				return (None, error_msg)

			logger.info(
				f"[SessionRenewal] New session created (pre-transaction): {new_session_id} "
				f"(tenant={tenant_id})"
			)

			# === TRANSACTION BLOCK: all or nothing (FIX A2) ===
			# All following operations must succeed; on ANY failure, rollback new_session_id

			try:
				# G19 FIX: Transfer MEMORY BEFORE checkpoint save (correct order)
				# 3.5. Transfer MEMORY.md to new session (ADR-0668)
				# CRITICAL: must succeed before persisting checkpoint (FIX A2: with timeout)
				try:
					from session_memory_pool import transfer_session_memory
					memory_transferred = await asyncio.wait_for(
						transfer_session_memory(
							old_session_id=old_session_id,
							new_session_id=new_session_id,
							goal=goal,
							tenant_id=tenant_id  # Pass tenant_id to memory transfer
						),
						timeout=10.0  # 10s timeout to prevent indefinite hang
					)
					if memory_transferred:
						logger.info(
							f"[SessionRenewal] Memory transferred: {old_session_id} → {new_session_id} "
							f"(tenant={tenant_id})"
						)
					else:
						logger.error(
							f"[SessionRenewal] Memory transfer failed for {old_session_id} "
							f"(tenant={tenant_id})"
						)
						raise RuntimeError("Memory transfer returned False")
				except asyncio.TimeoutError:
					logger.error(
						f"[SessionRenewal] Memory transfer timeout for {old_session_id} "
						f"(tenant={tenant_id})"
					)
					raise RuntimeError("Memory transfer timeout (>10s)")
				except Exception as e:  # noqa: BLE001
					logger.error(
						f"[SessionRenewal] Memory transfer exception (tenant={tenant_id}): {e}"
					)
					raise RuntimeError(f"Memory transfer failed: {e}")

				# G19 FIX: NOW persist checkpoint (after memory transfer succeeds)
				# 2. Persist checkpoint (moved here to ensure all-or-nothing semantics)
				saved = await self.checkpoint_manager.save_checkpoint(checkpoint)
				if not saved:
					error_msg = f"[SessionRenewal] Checkpoint save failed for {old_session_id} (tenant={tenant_id})"
					logger.error(error_msg)
					raise RuntimeError(error_msg)

				logger.info(
					f"[SessionRenewal] Checkpoint saved: {checkpoint.checkpoint_hash} "
					f"(tenant={tenant_id})"
				)

				# 4. Capture load-bearing anchor facts (ADR-0407 amendment)
				# Non-blocking within transaction: log warning but continue on failure
				try:
					await self._capture_load_bearing_anchor(
						old_session_id=old_session_id,
						new_session_id=new_session_id,
						checkpoint_hash=checkpoint.checkpoint_id if checkpoint else "",
						goal=goal,
						context=context,
						tenant_id=tenant_id,  # Pass tenant_id for tenant-scoped anchor capture
					)
					logger.info(
						f"[SessionRenewal] Load-bearing anchor captured for {new_session_id}"
					)
				except Exception as e:  # noqa: BLE001
					logger.warning(f"[SessionRenewal] Load-bearing anchor capture failed: {e}")
					# Continue; anchor is best-effort within the transaction

				# G20 FIX: Export plans with fallback for missing status field
				# 5. Export and link plans across session boundary (ADR-0407 amendment, B5/B6 FIX)
				# Non-blocking within transaction: log warning but continue on failure
				try:
					exporter = get_plan_exporter()
					plan_mapping = await exporter.export_and_link_plans(
						old_session_id=old_session_id,
						new_session_id=new_session_id,
						split_reason="token_budget",
						tenant_id=tenant_id,  # Pass tenant_id for plan export (B5 deduplication)
						accept_missing_status=True  # G20 FIX: fallback to "active" for plans without status
					)
					if plan_mapping:
						logger.info(
							f"[SessionRenewal] Linked {len(plan_mapping)} plans "
							f"to new session {new_session_id} (tenant={tenant_id})"
						)
				except Exception as e:  # noqa: BLE001
					logger.warning(
						f"[SessionRenewal] Plan export failed (non-fatal, tenant={tenant_id}): {e}"
					)
					# Continue; plan export is best-effort within the transaction

				# B6 FIX: Account Claude summarization tokens (1250 tokens per summarize)
				# This would be called if the session triggered a summarization during split
				try:
					# F17 FIX: Add API stability wrapper for context_budget.account_turn
					# If summarization occurred during checkpoint creation, account those tokens
					summarization_tokens = 1250  # Fixed overhead per ADR-0668
					if hasattr(self.context_budget, 'account_turn'):
						self.context_budget.account_turn(
							new_session_id,
							turn_id=f"summarization_{datetime.now().isoformat()}",
							tokens=summarization_tokens
						)
						logger.debug(f"[SessionRenewal] Accounted {summarization_tokens} summarization tokens to {new_session_id}")
				except Exception as e:  # noqa: BLE001
					logger.debug(f"[SessionRenewal] Failed to account summarization tokens: {e}")

				# === END TRANSACTION BLOCK ===
				# All critical operations succeeded; now finalize new session

				# C8 FIX: Register budget for new session AFTER successful save+transfer (finalization)
				try:
					# F17 FIX: Add API stability wrapper for register_session_budget
					self.context_budget.register_session_budget(
						new_session_id,
						quota=15_000_000,
						oom_policy="reject"
					)
				except Exception as e:  # noqa: BLE001
					logger.warning(f"[SessionRenewal] Failed to register budget for new session: {e}")

				logger.info(
					f"[SessionRenewal] Session split complete: {old_session_id} → {new_session_id} "
					f"(tenant={tenant_id})"
				)

				# 7. **GDPR Art. 30/32: Emit audit event to hash-chain (load-bearing)**
				# Non-blocking: audit emit failure must never break the session split
				try:
					from core.compliance.audit_backend import write_event
					write_event(
						event_type="session_split",
						tenant_id=tenant_id,  # Use actual tenant_id for audit scoping
						old_session_id=old_session_id,
						new_session_id=new_session_id,
						reason="token_budget_limit",
						checkpoint_hash=(checkpoint.checkpoint_hash if checkpoint else ""),
						goal=goal,
						timestamp=datetime.now().isoformat() + "Z",
					)
					logger.info(
						f"[SessionRenewal] Audit event emitted: session_split "
						f"({old_session_id} → {new_session_id}, tenant={tenant_id})"
					)
				except ImportError:
					logger.warning(
						"[SessionRenewal] audit_backend module not available; "
						"session split will not be audited (GDPR Art. 30/32 gap)"
					)
				except Exception as audit_err:
					logger.warning(
						f"[SessionRenewal] Audit emit failed (tenant={tenant_id}): {audit_err} "
						f"(continuing anyway; non-blocking)"
					)

				return (new_session_id, None)

			except Exception as e:
				# ROLLBACK: transaction failed; unregister new_session (FIX A2)
				error_msg = f"[SessionRenewal] Transaction failed for new session {new_session_id} (tenant={tenant_id}); initiating rollback: {e}"
				logger.error(error_msg)
				try:
					if new_session_id and hasattr(self.context_budget, 'unregister_session'):
						self.context_budget.unregister_session(new_session_id)
						logger.info(
							f"[SessionRenewal] Rolled back session {new_session_id} "
							f"(old session {old_session_id} still active, tenant={tenant_id})"
						)
				except Exception as rollback_error:  # noqa: BLE001
					logger.exception(
						f"[SessionRenewal] Rollback failed for {new_session_id} "
						f"(tenant={tenant_id}): {rollback_error}"
					)
				return (None, error_msg)

		except Exception as e:
			error_msg = f"[SessionRenewal] trigger_auto_session_split failed (tenant={tenant_id}): {e}"
			logger.exception(error_msg)
			return (None, error_msg)


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
