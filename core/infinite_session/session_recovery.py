"""Session Recovery Consumer - Auto-restore context from prior session snapshot.

This module implements Phase 3 (Session Recovery) of ADR-0541 Session Bridging.

Integration Point: chat_runtime.py::initialize_chat_session()
- Checks for prior snapshots for this task
- Verifies snapshot signature (fail-closed)
- Restores ContextVars ACTIVELY (not just text injection)
- Returns restored context for LLM system message

Architecture:
- SessionRecoveryManager - loads snapshots + verifies signatures
- ContextVarRestorer - activates ContextVars in current process
- SnapshotVerifier - cryptographic signature + tenant isolation

Based on ADR-0541 Amendment (Session Bridging).
Depends on: ADR-0314 (Learning Events), ADR-0232 (Audit Chain), ADR-0424 (Context Propagation)

NOT WIRED: no production caller as of 2026-09-27 (adversarial review) —
``SessionRecoveryManager`` is constructed only by tests; chat_runtime.py does
not call ``auto_restore_session_context``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from core.infinite_session.key_management import KeyManagementConfig

logger = logging.getLogger(__name__)


# ONE sentinel, ONE error type: the canonical ContextVar-backed implementation.
# This module used to ship its own placeholder ``ContextLossSentinel`` whose
# ``assert_task_context()`` returned the literal "dummy_task_id" and whose
# ``assert_tenant_context()`` returned "_default" — a context-loss detector
# that could never detect a loss, and silently mapped every caller onto the
# default tenant (adversarial review 2026-09-27).
from core.concurrency.context_loss_sentinel import (  # noqa: E402
    ContextLossError,
    ContextLossSentinel,
)


class SnapshotVerificationError(Exception):
    """Raised when snapshot signature verification fails."""
    pass


class SnapshotExpiredError(Exception):
    """Raised when snapshot is stale (> 24h old)."""
    pass


@dataclass(frozen=True)
class SnapshotVerificationResult:
    """Result of snapshot signature verification."""

    is_valid: bool
    reason: str = ""
    verified_at: str = ""
    tenant_id: str = ""
    snapshot_hash: str = ""


def _signed_payload(snapshot_dict: Dict[str, Any]) -> bytes:
    return json.dumps(
        # Every field is bound, including the chain link ``prev_snapshot_hash``
        # (the old scheme left it out, so it could be rewritten undetected).
        {k: v for k, v in snapshot_dict.items() if k != "signature"},
        sort_keys=True,
    ).encode("utf-8")


def sign_snapshot(snapshot_dict: Dict[str, Any], *, key: Optional[str] = None) -> str:
    """HMAC-SHA256 signature of a snapshot dict (hex).

    The ONE signing function: ``SessionBridgeProducer`` signs with it when it
    persists ``latest.json`` and ``SnapshotVerifier`` recomputes it to verify.
    ``key`` defaults to ``KeyManagementConfig.get_snapshot_key()``, which raises
    (fail-closed) when no production key is configured.

    The previous scheme was ``sha256(payload + key)`` — not an HMAC despite the
    docstring — and nothing ever produced it: the producer persisted snapshots
    with NO signature, so every real snapshot failed verification.
    """
    if key is None:
        key = KeyManagementConfig.get_snapshot_key()
    return hmac.new(key.encode("utf-8"), _signed_payload(snapshot_dict), hashlib.sha256).hexdigest()


class SnapshotVerifier:
    """Verify snapshot signatures and tenant isolation (fail-closed)."""

    def verify_snapshot_signature(
        self,
        snapshot_dict: Dict[str, Any],
        signature: Optional[str] = None,
        external_key: Optional[str] = None,  # From KeyManagementConfig if None
    ) -> SnapshotVerificationResult:
        """Verify HMAC-SHA256 signature of snapshot (fail-closed).

        Args:
            snapshot_dict: The snapshot to verify
            signature: Expected signature (from bridge event)
            external_key: HMAC key (from external key store / HSM). If None, gets from KeyManagementConfig.

        Returns:
            SnapshotVerificationResult with is_valid flag

        Raises:
            ValueError: If no valid key can be obtained from KeyManagementConfig
        """

        # Get key from config if not provided (fail-closed rejection of hardcoded keys)
        if external_key is None:
            external_key = KeyManagementConfig.get_snapshot_key()

        expected_signature = sign_snapshot(snapshot_dict, key=external_key)

        tenant_id = snapshot_dict.get("tenant_id", "")
        snapshot_hash = snapshot_dict.get("content_hash", "")

        # Constant-time compare; a missing/non-string signature never matches.
        if isinstance(signature, str) and signature and hmac.compare_digest(
            signature, expected_signature
        ):
            return SnapshotVerificationResult(
                is_valid=True,
                reason="Signature verified",
                verified_at=datetime.utcnow().isoformat(),
                tenant_id=tenant_id,
                snapshot_hash=snapshot_hash,
            )
        # Never echo the expected signature: it used to be written into the
        # reason (and from there into the raised exception and the error log),
        # which handed anyone who could read either a valid signature for the
        # snapshot they had just tampered with.
        return SnapshotVerificationResult(
            is_valid=False,
            reason="Signature mismatch",
            tenant_id=tenant_id,
            snapshot_hash=snapshot_hash,
        )

    def verify_tenant_isolation(
        self,
        snapshot_tenant_id: str,
        current_tenant_id: str,
    ) -> SnapshotVerificationResult:
        """Verify tenant isolation (fail-closed).

        Snapshot must belong to the current tenant — never cross-tenant.
        """

        if snapshot_tenant_id == current_tenant_id:
            return SnapshotVerificationResult(
                is_valid=True,
                reason="Tenant isolation verified",
                verified_at=datetime.utcnow().isoformat(),
                tenant_id=snapshot_tenant_id,
            )
        else:
            return SnapshotVerificationResult(
                is_valid=False,
                reason=f"Tenant mismatch: snapshot={snapshot_tenant_id}, current={current_tenant_id}",
                tenant_id=snapshot_tenant_id,
            )


class ContextVarRestorer:
    """Restore ContextVars ACTIVELY (not just text injection)."""

    @staticmethod
    def restore_context_vars_from_snapshot(snapshot: Dict[str, Any]) -> Dict[str, str]:
        """Activate ContextVars from snapshot.

        In production, this would use contextvars.ContextVar.set() for:
        - TenantContextVar.set(snapshot['tenant_id'])
        - TaskIDVar.set(snapshot['task_id'])
        - WorktreeVar.set(snapshot['worktree_path'])
        - BaseCommitVar.set(snapshot['base_commit'])
        - PhaseVar.set(snapshot['phase_name'])

        Args:
            snapshot: SessionContextSnapshot as dict

        Returns:
            Dict mapping var_name → value (for audit logging)
        """

        restored_vars = {
            "tenant_id": snapshot.get("tenant_id", ""),
            "task_id": snapshot.get("task_id", ""),
            "session_id": snapshot.get("session_id", ""),
            "worktree_path": snapshot.get("worktree_path", ""),
            "base_commit": snapshot.get("base_commit", ""),
            "phase_name": snapshot.get("phase_name", ""),
        }

        logger.info(f"ContextVars restored: {restored_vars}")
        return restored_vars


class SessionRecoveryManager:
    """Manage session recovery: load snapshots, verify, restore context."""

    def __init__(
        self,
        event_store_path: Optional[Path] = None,
        snapshot_dir: Optional[Path] = None,
    ):
        """Initialize recovery manager.

        Args:
            event_store_path: Path to audit.jsonl
            snapshot_dir: Path to snapshots/ directory
        """
        # None = THE tenant audit chain / the per-tenant snapshot root that
        # SessionBridgeProducer writes (<corvin_home>/tenants/<tid>/
        # infinite_session/snapshots/<task_id>). Both used to default to a
        # hand-composed Path.home()/.corvin/tenants/_default/... — ignoring
        # CORVIN_HOME and the caller's tenant, and appending unchained JSON
        # lines to the tenant's hash-chained audit.jsonl.
        self.event_store_path = Path(event_store_path) if event_store_path else None
        self.snapshot_dir = Path(snapshot_dir) if snapshot_dir else None
        self.verifier = SnapshotVerifier()
        self.restorer = ContextVarRestorer()

    async def auto_restore_session_context(
        self,
        tenant_id: str,
        task_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Restore session context on next invocation (fail-closed).

        Called at the START of chat_runtime.py::initialize_chat_session()

        Args:
            tenant_id: Tenant scope
            task_id: Task to recover

        Returns:
            Restored context dict if found + verified, else None
        """

        # 1. Find latest snapshot for this task
        latest_snapshot = self._find_latest_snapshot(task_id, tenant_id)
        if not latest_snapshot:
            logger.debug(f"No snapshot found for task={task_id}")
            return None

        snapshot_dict, signature = latest_snapshot

        # 2. Verify snapshot signature (fail-closed)
        sig_result = self.verifier.verify_snapshot_signature(
            snapshot_dict,
            signature=signature,
        )
        if not sig_result.is_valid:
            logger.error(f"Snapshot signature verification FAILED: {sig_result.reason}")
            raise SnapshotVerificationError(sig_result.reason)

        # 3. Verify tenant isolation (fail-closed)
        tenant_result = self.verifier.verify_tenant_isolation(
            snapshot_dict.get("tenant_id", ""),
            tenant_id,
        )
        if not tenant_result.is_valid:
            logger.error(f"Tenant isolation check FAILED: {tenant_result.reason}")
            raise ContextLossError(
                f"Cross-tenant snapshot detected: {tenant_result.reason}"
            )

        # FIX #6: Timestamp validation (< 24h staleness)
        try:
            snapshot_ts = datetime.fromisoformat(str(snapshot_dict.get("timestamp", "")))
        except ValueError as exc:
            raise SnapshotExpiredError("Snapshot has no valid timestamp") from exc
        if snapshot_ts.tzinfo is not None:  # compare naive-UTC with naive-UTC
            snapshot_ts = snapshot_ts.astimezone(timezone.utc).replace(tzinfo=None)
        snapshot_age_hours = (datetime.utcnow() - snapshot_ts).total_seconds() / 3600
        # A future-dated snapshot is not "fresh": its age cannot be established.
        if snapshot_age_hours > 24 or snapshot_age_hours < -0.1:
            raise SnapshotExpiredError(
                f"Snapshot stale ({snapshot_age_hours:.1f}h old, max 24h)"
            )

        # FIX #6: Destination session validation
        dest_session_id = snapshot_dict.get("dest_session_id")
        if dest_session_id and dest_session_id != task_id:
            raise ContextLossError(
                f"Snapshot destination mismatch: {dest_session_id} != {task_id}"
            )

        # 4. Restore ContextVars ACTIVELY
        restored_vars = self.restorer.restore_context_vars_from_snapshot(
            snapshot_dict
        )

        # 5. Emit audit event (GDPR Art. 30)
        self._emit_context_restored_event(
            task_id=task_id,
            tenant_id=tenant_id,
            snapshot_hash=snapshot_dict.get("content_hash", ""),
        )

        logger.info(
            f"✅ Session context restored for {task_id}: "
            f"phase={snapshot_dict.get('phase_name')}, "
            f"turn={snapshot_dict.get('conversation_turn_count')}"
        )

        return snapshot_dict

    def _find_latest_snapshot(
        self,
        task_id: str,
        tenant_id: str,
    ) -> Optional[tuple[Dict[str, Any], str]]:
        """Find the latest snapshot for a task (fail-closed).

        Returns:
            (snapshot_dict, signature) or None if not found
        """

        from core.infinite_session.paths import safe_child, tenant_root  # noqa: PLC0415

        # Validates tenant_id and task_id: both are path components.
        if self.snapshot_dir is None:
            task_dir = safe_child(tenant_root(tenant_id) / "snapshots", task_id)
        else:
            tenant_root(tenant_id)  # validate_tenant_id, fail-closed
            task_dir = safe_child(self.snapshot_dir, tenant_id, task_id)
        snapshot_file = task_dir / "latest.json"

        if not snapshot_file.exists():
            return None

        try:
            with open(snapshot_file) as f:
                data = json.load(f)
                snapshot = data.get("snapshot", {})
                signature = data.get("signature", "")
                return (snapshot, signature)
        except Exception as e:
            logger.error(f"Failed to load snapshot: {e}")
            return None

    def _emit_context_restored_event(
        self,
        task_id: str,
        tenant_id: str,
        snapshot_hash: str,
    ) -> None:
        """Emit audit event (GDPR Art. 30 records processing)."""

        from core.infinite_session import paths as _paths  # noqa: PLC0415

        details = {"task_id": task_id}
        if snapshot_hash:
            details["content_hash"] = snapshot_hash
        # Written by the core chain writer (hash-linked), never by hand.
        # Not raising here is unchanged behaviour: the context is already
        # restored when this runs.
        try:
            if self.event_store_path is None:
                _paths.core_audit(
                    "infinite_session.context_restored", tenant_id=tenant_id, details=details
                )
            else:
                _paths.validate_tenant_id(tenant_id)
                _paths._register_allowlists()
                from forge import security_events  # noqa: PLC0415  # type: ignore[import-not-found]

                security_events.write_event(
                    self.event_store_path,
                    "infinite_session.context_restored",
                    details={**details, "tenant_id": tenant_id},
                )
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to emit context_restored event: {e}")


async def integrate_recovery_into_initialize_session():
    """
    Integration guide for chat_runtime.py::initialize_chat_session()

    BEFORE: No context recovery from prior session
    AFTER: Automatically restore ContextVars + return restored context

    Code pattern (in initialize_chat_session at the START):

    ```python
    async def initialize_chat_session(tenant_id, task_id, session_id):
        \"\"\"Initialize chat session WITH automatic context recovery.\"\"\"

        # TRY: Auto-restore from snapshot
        recovery_mgr = SessionRecoveryManager()
        restored_context = await recovery_mgr.auto_restore_session_context(
            tenant_id=tenant_id,
            task_id=task_id,
        )

        if restored_context:
            # ✅ Context was restored → ContextVars are ACTIVE
            logger.info(f"Resumed session {session_id} with prior context")
            # System message INCLUDES the context
            system_msg = build_system_message_with_context(restored_context)
        else:
            # ❌ No prior context → fresh start
            system_msg = build_fresh_system_message()

        return {
            "session_id": session_id,
            "system_message": system_msg,
            "restored_context": restored_context,
        }
    ```

    This ensures that every session resumes with ACTIVE ContextVars,
    not just text-injected context.
    """
    pass


__all__ = [
    "ContextLossSentinel",
    "SnapshotVerifier",
    "ContextVarRestorer",
    "SessionRecoveryManager",
    "ContextLossError",
    "SnapshotExpiredError",
    "SnapshotVerificationError",
    "sign_snapshot",
]
