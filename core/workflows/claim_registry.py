"""Checkpoint claim registry with TTL-based auto-reap (ADR-0423 Phase 2, Gap 1+5).

Prevents claim deadlocks and stale claims via:
- In-memory claim tracking (claim_id -> claimed_path, timestamp, ttl)
- Atomic claim() with AlreadyClaimedError on conflicts
- Background TTL reaper (runs every 60s, default TTL 3600s)
- Audit trail integration for all claim operations

Used by: resume handlers, background task supervisors, bridge lifecycle
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from .path_resolver import (
    workflow_run_path,
    claimed_file_path,
    resolve_corvin_home,
    resolve_tenant_id,
    PathResolutionError,
)

logger = logging.getLogger(__name__)


class AlreadyClaimedError(RuntimeError):
    """Raised when claim() finds a run already claimed by another resume."""

    pass


class ClaimExpiredError(RuntimeError):
    """Raised when a claim has expired and been reaped."""

    pass


@dataclass
class ClaimRecord:
    """In-memory record of a claimed checkpoint."""

    run_id: str
    claimed_path: Path
    timestamp_s: float
    ttl_s: int  # time-to-live in seconds
    claim_id: str = field(default_factory=lambda: f"claim-{time.time()}")
    tenant_id: str = "_default"

    @property
    def is_expired(self) -> bool:
        """Check if claim has exceeded its TTL."""
        elapsed = time.time() - self.timestamp_s
        return elapsed > self.ttl_s

    def to_dict(self) -> dict:
        """Serialize to dict for audit logging."""
        return {
            "run_id": self.run_id,
            "claimed_path": str(self.claimed_path),
            "timestamp_s": self.timestamp_s,
            "ttl_s": self.ttl_s,
            "claim_id": self.claim_id,
            "tenant_id": self.tenant_id,
        }


class CheckpointClaimRegistry:
    """TTL-aware claim registry for workflow checkpoints.

    Manages:
    - Atomic claim() with race protection
    - TTL-based expiry detection
    - Background reaper coroutine
    - Audit trail integration
    """

    def __init__(self, default_ttl_s: int = 3600, reaper_interval_s: int = 60):
        """Initialize registry.

        Args:
            default_ttl_s: Default claim TTL (seconds). Default: 1 hour.
            reaper_interval_s: How often reaper runs (seconds). Default: 60s.
        """
        self.default_ttl_s = default_ttl_s
        self.reaper_interval_s = reaper_interval_s
        self._claims: Dict[str, ClaimRecord] = {}
        self._reaper_task: Optional[asyncio.Task] = None
        self._is_running = False
        self._lock = asyncio.Lock()

    async def start_reaper(self) -> None:
        """Start background TTL reaper coroutine."""
        if self._is_running:
            return
        self._is_running = True
        self._reaper_task = asyncio.create_task(self._run_reaper())
        logger.info(
            f"CheckpointClaimRegistry reaper started (interval={self.reaper_interval_s}s, "
            f"ttl={self.default_ttl_s}s)"
        )

    async def stop_reaper(self) -> None:
        """Stop background reaper."""
        if not self._is_running:
            return
        self._is_running = False
        if self._reaper_task:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
        logger.info("CheckpointClaimRegistry reaper stopped")

    async def _run_reaper(self) -> None:
        """Background coroutine that reaps expired claims."""
        while self._is_running:
            try:
                await asyncio.sleep(self.reaper_interval_s)
                await self.reap_stale_claims(self.default_ttl_s)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in checkpoint reaper: {e}", exc_info=True)

    def claim(
        self,
        run_id: str,
        tenant_id: Optional[str] = None,
        ttl_s: Optional[int] = None,
    ) -> Path:
        """Atomically claim a paused checkpoint.

        Validates:
        - Run checkpoint exists
        - Not already claimed by another resume
        - Claim record not expired

        Args:
            run_id: Unique run identifier
            tenant_id: Tenant ID (or resolved from env/default)
            ttl_s: Override default TTL for this claim

        Returns:
            Path to claimed checkpoint file

        Raises:
            AlreadyClaimedError: Another resume already holds this claim
            FileNotFoundError: No paused checkpoint found
            ClaimExpiredError: Claim record exists but has expired
        """
        tenant = resolve_tenant_id(tenant_id)
        ttl = ttl_s or self.default_ttl_s

        # Check if claim record exists and is expired
        if run_id in self._claims:
            record = self._claims[run_id]
            if record.is_expired:
                # Expired claim should be reaped; delete the claim record
                del self._claims[run_id]
            else:
                # Claim still valid and held by another resume
                raise AlreadyClaimedError(
                    f"run {run_id!r} is already being resumed "
                    f"(claimed at {record.timestamp_s}, ttl={record.ttl_s}s)"
                )

        # Atomically rename checkpoint to .claimed (POSIX/Windows atomic rename)
        run_path = workflow_run_path(run_id, tenant)
        claimed_path = claimed_file_path(run_id, tenant)

        if not run_path.exists():
            # Check if already claimed (sidecar exists)
            if claimed_path.exists():
                raise AlreadyClaimedError(
                    f"run {run_id!r} is already being resumed (sidecar exists)"
                )
            raise FileNotFoundError(
                f"no paused run found for run_id={run_id!r} in tenant={tenant}"
            )

        try:
            os.rename(run_path, claimed_path)
        except FileNotFoundError as exc:
            if claimed_path.exists():
                raise AlreadyClaimedError(
                    f"run {run_id!r} is already being resumed"
                ) from exc
            raise FileNotFoundError(
                f"no paused run found for run_id={run_id!r}"
            ) from exc

        # Record claim in registry
        record = ClaimRecord(
            run_id=run_id,
            claimed_path=claimed_path,
            timestamp_s=time.time(),
            ttl_s=ttl,
            tenant_id=tenant,
        )
        self._claims[run_id] = record

        logger.info(
            f"Claimed checkpoint: run_id={run_id}, path={claimed_path}, ttl={ttl}s"
        )
        return claimed_path

    def release(self, run_id: str, tenant_id: Optional[str] = None) -> None:
        """Release a claim, making the checkpoint resumable again.

        Used when a resume ends non-terminal (paused again or failed).
        If a fresh checkpoint was written during resume, keeps it and drops stale sidecar.
        Otherwise restores the claimed file to canonical path.

        Args:
            run_id: Unique run identifier
            tenant_id: Tenant ID (or resolved from env/default)
        """
        tenant = resolve_tenant_id(tenant_id)
        run_path = workflow_run_path(run_id, tenant)
        claimed_path = claimed_file_path(run_id, tenant)

        if not claimed_path.exists():
            # Claim already released
            logger.debug(f"Claim not found for release: run_id={run_id}")
            return

        if run_path.exists():
            # Fresh checkpoint was written during resume — keep it, drop stale sidecar
            claimed_path.unlink(missing_ok=True)
            logger.info(
                f"Released claim (fresh checkpoint exists): run_id={run_id}, "
                f"dropped stale sidecar"
            )
        else:
            # Restore claimed file to canonical path
            os.replace(claimed_path, run_path)
            logger.info(f"Released claim (restored to canonical path): run_id={run_id}")

        # Remove from registry
        self._claims.pop(run_id, None)

    def is_claimed(self, run_id: str, tenant_id: Optional[str] = None) -> bool:
        """Check if a run is currently claimed.

        Args:
            run_id: Unique run identifier
            tenant_id: Tenant ID (or resolved from env/default)

        Returns:
            True if claimed and not expired, False otherwise
        """
        if run_id not in self._claims:
            return False

        record = self._claims[run_id]
        if record.is_expired:
            del self._claims[run_id]
            return False

        return True

    async def reap_stale_claims(self, ttl_s: int) -> int:
        """Reap all expired claims and clean up their sidecar files.

        Args:
            ttl_s: TTL threshold (seconds)

        Returns:
            Number of claims reaped
        """
        async with self._lock:
            expired_run_ids = []
            for run_id, record in self._claims.items():
                if record.is_expired:
                    expired_run_ids.append(run_id)

            count = 0
            for run_id in expired_run_ids:
                record = self._claims.pop(run_id)
                # Delete stale .claimed sidecar
                try:
                    if record.claimed_path.exists():
                        record.claimed_path.unlink()
                        logger.warning(
                            f"Reaped stale claim: run_id={run_id}, "
                            f"deleted {record.claimed_path}"
                        )
                        count += 1
                except OSError as e:
                    logger.error(
                        f"Error deleting stale claim sidecar for run {run_id}: {e}"
                    )

            if count > 0:
                logger.info(
                    f"CheckpointClaimRegistry reaped {count} expired claim(s)"
                )

            return count

    def get_all_claims(self) -> list[ClaimRecord]:
        """Get all active claims (for monitoring/debugging).

        Returns:
            List of non-expired ClaimRecords
        """
        return [
            record
            for record in self._claims.values()
            if not record.is_expired
        ]

    def cleanup(self) -> None:
        """Synchronous cleanup (for shutdown). Call before exit."""
        # Try to restore any released claims
        for run_id in list(self._claims.keys()):
            try:
                self.release(run_id)
            except Exception as e:
                logger.error(f"Error releasing claim during cleanup: {e}")
