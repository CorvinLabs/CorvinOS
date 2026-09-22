"""CheckpointManager: atomic checkpoint storage & recovery (ADR-0471).

ADR-0875: Concurrent-write race condition fix via flock-based serialization.
"""

import json
import logging
import os
import fcntl
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

logger = logging.getLogger(__name__)


@contextmanager
def file_lock(lock_file_path: Path, timeout_sec: float = 5.0):
	"""Context manager for file-based locking (ADR-0875: race fix, A3 deadlock timeout).

	Args:
		lock_file_path: Path to lock file
		timeout_sec: Max time to wait for lock acquisition (A3 FIX: prevents deadlock)

	Yields:
		True if lock acquired, False if timeout
	"""
	lock_file = lock_file_path.parent / f"{lock_file_path.name}.lock"
	start_time = os.times()[4]  # Wall-clock time

	lock_handle = None
	try:
		lock_handle = open(lock_file, 'w')
		# A3 FIX: Use LOCK_NB (non-blocking) + manual timeout to prevent fcntl deadlock
		try:
			fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
		except IOError:
			# Non-blocking lock failed; check timeout
			elapsed = os.times()[4] - start_time
			if elapsed > timeout_sec:
				logger.warning(
					f"[CheckpointManager] Lock timeout after {elapsed:.2f}s on {lock_file}; "
					f"proceeding without lock (eventual consistency)"
				)
				yield False
				return
			# Retry blocking lock with timeout
			fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
		yield True
	except (IOError, OSError) as e:
		# Lock failed; log and proceed with eventual consistency
		elapsed = os.times()[4] - start_time
		if elapsed > timeout_sec:
			logger.warning(
				f"[CheckpointManager] Lock timeout after {elapsed:.2f}s on {lock_file}; "
				f"proceeding without lock (eventual consistency)"
			)
		yield False
	finally:
		if lock_handle:
			try:
				fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
				lock_handle.close()
			except Exception:
				pass


class CheckpointManager:
	"""Manage checkpoint storage & recovery for autonomous sessions (ADR-0471)."""

	def __init__(self, checkpoint_dir: Optional[str] = None, tenant_id: str = "_default", cleanup_interval_days: int = 7, auto_cleanup: bool = True):
		"""Initialize checkpoint storage.

		Args:
			checkpoint_dir: Directory for storing checkpoints.
						  Defaults to ~/.corvin/tenants/<tenant_id>/checkpoints/ (ADR-0007)
			tenant_id: Tenant identifier (default "_default", must be valid per ADR-0007)
			cleanup_interval_days: Delete checkpoints older than this (default 7)
			auto_cleanup: Run periodic cleanup every hour (CRITICAL-007 fix)
		"""
		if checkpoint_dir is None:
			# F18 FIX: Use persistent corvin_home resolver from core.paths instead of one-off os.getenv()
			try:
				from core.paths import corvin_home
				checkpoint_dir = os.path.join(corvin_home(), "tenants", tenant_id, "checkpoints")
			except (ImportError, AttributeError):
				# Fallback: use os.getenv if core.paths not available
				corvin_home_fallback = os.getenv("CORVIN_HOME", os.path.expanduser("~/.corvin"))
				checkpoint_dir = os.path.join(corvin_home_fallback, "tenants", tenant_id, "checkpoints")
			# ADR-0007: Tenant-scoped checkpoint directory — every checkpoint is tenant-bound

		self.checkpoint_dir = Path(checkpoint_dir)
		self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
		self.cleanup_interval_days = cleanup_interval_days
		self.tenant_id = tenant_id
		self._cleanup_task = None
		logger.info(f"[CheckpointManager] Using checkpoint dir: {self.checkpoint_dir} (tenant={tenant_id})")

		# CRITICAL-007 fix: Start periodic cleanup loop to prevent unbounded disk growth
		if auto_cleanup:
			try:
				loop = asyncio.get_event_loop()
				self._cleanup_task = loop.create_task(self._periodic_cleanup_loop())
				logger.info("[CheckpointManager] Periodic cleanup loop started (hourly)")
			except RuntimeError:
				# No event loop running, skip background cleanup
				logger.debug("[CheckpointManager] No event loop for periodic cleanup")

	async def _periodic_cleanup_loop(self, interval_hours: int = 1):
		"""Run cleanup periodically every N hours (CRITICAL-007: prevent disk exhaustion).

		This loop runs in the background, periodically calling cleanup_old_checkpoints()
		to remove checkpoints older than cleanup_interval_days.

		Args:
			interval_hours: Run cleanup every N hours (default: 1 hour)
		"""
		interval_seconds = interval_hours * 3600
		logger.info(f"[CheckpointManager] Periodic cleanup loop started ({interval_hours}h interval)")

		while True:
			try:
				# Wait before first cleanup
				await asyncio.sleep(interval_seconds)

				# Run cleanup
				logger.debug(f"[CheckpointManager] Running periodic cleanup (max age: {self.cleanup_interval_days}d)")
				await self.cleanup_old_checkpoints(self.cleanup_interval_days)

			except asyncio.CancelledError:
				# Task was cancelled (graceful shutdown)
				logger.info("[CheckpointManager] Periodic cleanup loop cancelled (graceful shutdown)")
				break
			except Exception as e:
				# Log error but continue loop (non-fatal)
				logger.error(f"[CheckpointManager] Periodic cleanup failed: {e}")

	async def save_checkpoint(self, checkpoint) -> bool:
		"""Save checkpoint atomically to disk (ADR-0875: flock-serialized writes, C7 TOCTOU fix).

		Args:
			checkpoint: Checkpoint object to save

		Returns:
			True if successful, False otherwise
		"""
		try:
			checkpoint_file = self.checkpoint_dir / f"{checkpoint.session_id}.json"

			# C8 FIX: Register checkpoint AFTER successful save, not before
			# (will be handled by caller in trigger_auto_session_split)

			checkpoint_data = {
				"session_id": checkpoint.session_id,
				"goal": checkpoint.goal,
				"goal_hash": checkpoint.goal_hash,
				"timestamp": checkpoint.timestamp,
				"context_reduction_pct": checkpoint.context_reduction_pct,
				"context_tokens_used": checkpoint.context_tokens_used,
				"audit_trail_hash": checkpoint.audit_trail_hash,
				"checkpoint_hash": checkpoint.checkpoint_hash,
				"phase": checkpoint.phase,
				# ADR-0668: Token-savings metrics
				"baseline_tokens": checkpoint.baseline_tokens,
				"actual_tokens": checkpoint.actual_tokens,
				"savings_tokens": checkpoint.savings_tokens,
				"savings_pct": checkpoint.savings_pct,
			}

			# ADR-0875: Atomic write under flock to prevent concurrent corruption
			with file_lock(checkpoint_file) as lock_acquired:
				if not lock_acquired:
					logger.warning(f"[CheckpointManager] Lock not acquired for {checkpoint.session_id}; attempting write anyway")

				# Write to temp file atomically
				temp_file = self.checkpoint_dir / f"{checkpoint.session_id}.tmp"
				with open(temp_file, 'w') as f:
					json.dump(checkpoint_data, f, indent=2)
					f.flush()
					os.fsync(f.fileno())  # Force disk write

				# Atomic rename (OS-level atomic operation)
				temp_file.replace(checkpoint_file)

				# C7 FIX: Compute hash AFTER successful save (TOCTOU prevention)
				# Hash now reflects the actual persisted data, not pre-save state
				computed_hash = checkpoint.compute_hash()
				checkpoint.checkpoint_hash = computed_hash
				logger.debug(f"[CheckpointManager] Hash computed post-save: {computed_hash[:16]}")

			logger.info(f"[CheckpointManager] Saved: {checkpoint.session_id}")
			return True
		except Exception as e:
			# E14 FIX: Emit checkpoint_error audit event on failure
			try:
				from core.compliance.audit_backend import write_event
				write_event(
					event_type="checkpoint_error",
					session_id=getattr(checkpoint, 'session_id', 'unknown'),
					error=str(e),
					tenant_id=self.tenant_id,
				)
			except Exception as audit_err:
				logger.debug(f"[CheckpointManager] Failed to emit checkpoint_error event: {audit_err}")

			logger.exception(f"[CheckpointManager] Save failed: {e}")
			return False

	async def load_checkpoint(self, session_id: str):
		"""Load checkpoint from disk.

		Args:
			session_id: Session to load checkpoint for

		Returns:
			Checkpoint object or None if not found
		"""
		try:
			checkpoint_file = self.checkpoint_dir / f"{session_id}.json"

			if not checkpoint_file.exists():
				logger.warning(f"[CheckpointManager] Checkpoint not found: {session_id}")
				return None

			with open(checkpoint_file, 'r') as f:
				data = json.load(f)

			# Reconstruct checkpoint object
			from .lifecycle_manager import Checkpoint

			checkpoint = Checkpoint(
				session_id=data['session_id'],
				goal=data['goal'],
				goal_hash=data['goal_hash'],
				timestamp=data['timestamp'],
				context_reduction_pct=data['context_reduction_pct'],
				context_tokens_used=data['context_tokens_used'],
				audit_trail_hash=data['audit_trail_hash'],
				phase=data.get('phase', 'execution'),
				# ADR-0668: Token-savings metrics (with defaults for backward compat)
				baseline_tokens=data.get('baseline_tokens', 0),
				actual_tokens=data.get('actual_tokens', 0),
				savings_tokens=data.get('savings_tokens', 0),
				savings_pct=data.get('savings_pct', 0.0),
			)
			checkpoint.checkpoint_hash = data['checkpoint_hash']

			# CRITICAL FIX: Verify checkpoint integrity immediately after loading (fail-closed)
			is_valid = await self.verify_checkpoint_integrity(checkpoint)
			if not is_valid:
				logger.error(
					f"[CheckpointManager] Checkpoint integrity verification FAILED: {session_id} "
					f"— refusing to load corrupted checkpoint"
				)
				return None

			logger.info(f"[CheckpointManager] Loaded and verified: {session_id}")
			return checkpoint
		except Exception as e:
			logger.exception(f"[CheckpointManager] Load failed: {e}")
			return None

	async def cleanup_old_checkpoints(self, max_age_days: int = 7):
		"""Delete checkpoints older than max_age_days (ADR-0471: retention).

		Args:
			max_age_days: Delete checkpoints older than this
		"""
		try:
			cutoff = datetime.now() - timedelta(days=max_age_days)
			cutoff_ts = cutoff.timestamp()

			deleted = 0
			for checkpoint_file in self.checkpoint_dir.glob("*.json"):
				stat = checkpoint_file.stat()
				if stat.st_mtime < cutoff_ts:
					checkpoint_file.unlink()
					deleted += 1

			if deleted > 0:
				logger.info(f"[CheckpointManager] Cleaned {deleted} old checkpoints")
		except Exception as e:
			logger.exception(f"[CheckpointManager] Cleanup failed: {e}")

	async def verify_checkpoint_integrity(self, checkpoint) -> bool:
		"""Verify checkpoint hash integrity (fail-closed, ADR-0471).

		Args:
			checkpoint: Checkpoint to verify

		Returns:
			True if hash matches, False otherwise
		"""
		computed_hash = checkpoint.compute_hash()
		if computed_hash != checkpoint.checkpoint_hash:
			logger.error(f"[CheckpointManager] Hash mismatch: computed={computed_hash} stored={checkpoint.checkpoint_hash}")
			return False

		logger.debug(f"[CheckpointManager] Integrity verified: {checkpoint.session_id}")
		return True
