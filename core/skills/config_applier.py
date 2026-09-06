"""Task 1: Skill Config-Apply Wiring (L5 k=2 Week 2).

SkillConfigApplier integrates OptimizerWithApprovalGate with Skill execution.

When operator approves a config change:
1. Apply new config to Skill instance
2. Audit the change
3. Log results

When operator revokes:
1. Restore previous config
2. Audit the rollback
3. Log results

Handles failures gracefully: config apply failure is audited but approval is NOT revoked
(allows operator to investigate and decide next step).
"""

import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
import hashlib
import json

logger = logging.getLogger(__name__)


@dataclass
class ConfigApplyResult:
    """Result of attempting to apply a config."""
    success: bool
    config_hash: str
    previous_hash: str
    error: Optional[str] = None
    timestamp: str = ""


class SkillConfigApplier:
    """
    Wires approval gate to skill config application.

    Responsibilities:
    1. Store previous config (for rollback)
    2. Apply approved config changes
    3. Rollback on revoke
    4. Audit all changes
    5. Handle failures without breaking approval state

    Thread-safe: all config mutations protected by skill's internal lock (if present).
    """

    def __init__(
        self,
        skill_id: str,
        optimizer_with_gate,  # OptimizerWithApprovalGate
        audit_backend=None,
        tenant_id: str = "_default",
        *,
        config_getter=None,
        config_applier=None,
        config_restorer=None,
    ):
        """
        Initialize config applier.

        Args:
            skill_id: ID of the skill being optimized
            optimizer_with_gate: OptimizerWithApprovalGate instance
            audit_backend: Audit backend for logging (optional but recommended)
            tenant_id: Tenant ID for audit logging
            config_getter: ``() -> dict`` returning the skill's CURRENT config
                (hashed before/after every apply). Explicit injection replaces
                the former "set the private attribute after construction"
                contract, which every caller silently missed.
            config_applier: ``(new_config_hash) -> dict`` applying a config.
            config_restorer: ``(prev_config_hash) -> dict`` restoring one.
        """
        if config_getter is not None:
            self._config_getter = config_getter
        if config_applier is not None:
            self._config_applier = config_applier
        if config_restorer is not None:
            self._config_restorer = config_restorer
        self.skill_id = skill_id
        self.optimizer = optimizer_with_gate
        self.audit_backend = audit_backend
        self.tenant_id = tenant_id

        # Track previous config for rollback
        # Format: approval_id -> {config_hash, config_snapshot, applied_timestamp}
        self.previous_configs: Dict[str, Dict[str, Any]] = {}

        # Wire callbacks to optimizer
        self.optimizer.on_approval_callback = self._on_approval_callback
        self.optimizer.on_rejection_callback = self._on_rejection_callback

        # TODO: Add rollback callback (requires extension of OptimizerWithApprovalGate)
        # For now, rollback is triggered via explicit call (see handle_revoke method)

        logger.info(f"[ConfigApplier] Initialized for skill {skill_id}")

    def _on_approval_callback(self, approval_id: str, new_config_hash: str) -> None:
        """
        Called by OptimizerWithApprovalGate when operator approves.

        Args:
            approval_id: UUID of the approval
            new_config_hash: SHA256 hash of the new config
        """
        logger.info(
            f"[ConfigApplier] Approval callback triggered: {approval_id}, "
            f"config_hash={new_config_hash[:8]}..."
        )

        # Get the approval record to understand what was approved
        record = self.optimizer.approval_gate.get_approval_status(approval_id)
        if not record:
            logger.error(f"[ConfigApplier] Approval record not found: {approval_id}")
            return

        metric_name = record.scrubbed_alert.metric_name

        # Apply config (subclass must implement _get_skill() to return the Skill instance)
        try:
            prev_hash = self._get_current_config_hash()
            result = self._apply_config(approval_id, new_config_hash, metric_name)

            if result.success:
                logger.info(
                    f"[ConfigApplier] Config applied successfully: {approval_id}, "
                    f"metric={metric_name}, new_hash={result.config_hash[:8]}..."
                )
                # Store previous config for potential rollback
                self.previous_configs[approval_id] = {
                    "config_hash": prev_hash,
                    "new_config_hash": new_config_hash,
                    "metric_name": metric_name,
                    "timestamp": result.timestamp,
                }

                # Audit success
                self._audit_config_apply(
                    approval_id, metric_name, prev_hash, new_config_hash,
                    success=True, error=None
                )
            else:
                logger.error(
                    f"[ConfigApplier] Config apply failed: {approval_id}, "
                    f"metric={metric_name}, error={result.error}"
                )
                # Audit failure
                self._audit_config_apply(
                    approval_id, metric_name, prev_hash, new_config_hash,
                    success=False, error=result.error
                )

        except Exception as e:
            logger.error(
                f"[ConfigApplier] Exception in approval callback: {approval_id}, error={e}",
                exc_info=True
            )
            self._audit_config_apply(
                approval_id, record.scrubbed_alert.metric_name,
                self._get_current_config_hash(), new_config_hash,
                success=False, error=f"Exception: {type(e).__name__}: {str(e)}"
            )

    def _on_rejection_callback(self, approval_id: str) -> None:
        """
        Called by OptimizerWithApprovalGate when operator rejects.

        Args:
            approval_id: UUID of the rejected approval
        """
        logger.info(f"[ConfigApplier] Rejection callback: {approval_id}")

        # Audit rejection
        self._audit_config_rejected(approval_id)

    def handle_revoke(self, approval_id: str) -> bool:
        """
        Rollback config when operator revokes an approval.

        Called from approval routes when operator revokes.

        Args:
            approval_id: UUID of the approval to revoke

        Returns:
            True if rollback successful, False if config not found
        """
        logger.warning(f"[ConfigApplier] Revoke triggered: {approval_id}")

        if approval_id not in self.previous_configs:
            logger.warning(
                f"[ConfigApplier] No previous config stored for {approval_id}, "
                f"cannot rollback"
            )
            return False

        prev_config_info = self.previous_configs[approval_id]
        prev_hash = prev_config_info["config_hash"]
        metric_name = prev_config_info["metric_name"]

        try:
            # Restore previous config
            result = self._restore_config(approval_id, prev_hash, metric_name)

            if result.success:
                logger.info(
                    f"[ConfigApplier] Config rolled back: {approval_id}, "
                    f"restored_hash={result.config_hash[:8]}..."
                )
                # Audit success
                self._audit_config_rollback(
                    approval_id, metric_name, prev_hash,
                    success=True, error=None
                )
                # Clean up stored config
                del self.previous_configs[approval_id]
                return True
            else:
                logger.error(
                    f"[ConfigApplier] Config rollback failed: {approval_id}, "
                    f"error={result.error}"
                )
                # Audit failure
                self._audit_config_rollback(
                    approval_id, metric_name, prev_hash,
                    success=False, error=result.error
                )
                return False

        except Exception as e:
            logger.error(
                f"[ConfigApplier] Exception during revoke: {approval_id}, error={e}",
                exc_info=True
            )
            self._audit_config_rollback(
                approval_id, metric_name, prev_hash,
                success=False, error=f"Exception: {type(e).__name__}: {str(e)}"
            )
            return False

    def _get_current_config_hash(self) -> str:
        """
        Get hash of current skill config.

        MUST be overridden by subclass or configured with a config getter.

        Returns:
            SHA256 hex string of current config

        Raises:
            ValueError if _config_getter not configured
        """
        if hasattr(self, '_config_getter'):
            config = self._config_getter()
            return self._hash_config(config)
        raise ValueError("_config_getter not configured for SkillConfigApplier")

    def _apply_config(
        self,
        approval_id: str,
        new_config_hash: str,
        metric_name: str,
    ) -> ConfigApplyResult:
        """
        Apply new config to skill.

        MUST be overridden by subclass to actually update the skill config.

        Args:
            approval_id: UUID of the approval (for tracking)
            new_config_hash: Hash of the new config
            metric_name: What metric was changed (for logging)

        Returns:
            ConfigApplyResult with success/error
        """
        if not hasattr(self, '_config_applier'):
            raise ValueError("_config_applier not configured for SkillConfigApplier")

        # Save previous hash BEFORE attempting to apply (issue #8, #10)
        prev_hash = self._get_current_config_hash()

        try:
            new_config = self._config_applier(new_config_hash)
            from datetime import datetime
            return ConfigApplyResult(
                success=True,
                config_hash=new_config_hash,
                previous_hash=prev_hash,
                timestamp=datetime.utcnow().isoformat() + "Z"
            )
        except Exception as e:
            return ConfigApplyResult(
                success=False,
                config_hash=new_config_hash,
                previous_hash=prev_hash,
                error=str(e),
            )

    def _restore_config(
        self,
        approval_id: str,
        prev_config_hash: str,
        metric_name: str,
    ) -> ConfigApplyResult:
        """
        Restore previous config (rollback).

        MUST be overridden by subclass to actually restore the skill config.

        Args:
            approval_id: UUID of the approval
            prev_config_hash: Hash of the config to restore to
            metric_name: What metric is being restored

        Returns:
            ConfigApplyResult with success/error
        """
        if not hasattr(self, '_config_restorer'):
            raise ValueError("_config_restorer not configured for SkillConfigApplier")

        # Save current hash BEFORE attempting to restore (issue #8)
        current_hash = self._get_current_config_hash()

        try:
            restored_config = self._config_restorer(prev_config_hash)
            from datetime import datetime
            return ConfigApplyResult(
                success=True,
                config_hash=prev_config_hash,
                previous_hash=current_hash,
                timestamp=datetime.utcnow().isoformat() + "Z"
            )
        except Exception as e:
            return ConfigApplyResult(
                success=False,
                config_hash=prev_config_hash,
                previous_hash=current_hash,
                error=str(e),
            )

    def _audit_config_apply(
        self,
        approval_id: str,
        metric_name: str,
        prev_hash: str,
        next_hash: str,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        """Audit a config apply attempt."""
        if not self.audit_backend:
            return

        try:
            event = {
                "tenant_id": self.tenant_id,
                "event_type": "skill_config_applied",
                "approval_id": approval_id,
                "skill_id": self.skill_id,
                "metric_name": metric_name,
                "prev_config_hash": prev_hash,
                "next_config_hash": next_hash,
                "success": success,
                "error": error,
            }
            self.audit_backend.write_event(event)
        except Exception as e:
            logger.warning(f"[ConfigApplier] Audit write failed: {e}")

    def _audit_config_rejected(self, approval_id: str) -> None:
        """Audit a config rejection."""
        if not self.audit_backend:
            return

        try:
            event = {
                "tenant_id": self.tenant_id,
                "event_type": "skill_config_apply_skipped",
                "approval_id": approval_id,
                "skill_id": self.skill_id,
                "reason": "approval_rejected",
            }
            self.audit_backend.write_event(event)
        except Exception as e:
            logger.warning(f"[ConfigApplier] Audit write failed: {e}")

    def _audit_config_rollback(
        self,
        approval_id: str,
        metric_name: str,
        rolled_back_hash: str,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        """Audit a config rollback."""
        if not self.audit_backend:
            return

        try:
            event = {
                "tenant_id": self.tenant_id,
                "event_type": "skill_config_rolled_back",
                "approval_id": approval_id,
                "skill_id": self.skill_id,
                "metric_name": metric_name,
                "rolled_back_hash": rolled_back_hash,
                "success": success,
                "error": error,
            }
            self.audit_backend.write_event(event)
        except Exception as e:
            logger.warning(f"[ConfigApplier] Audit write failed: {e}")

    @staticmethod
    def _hash_config(config: Any) -> str:
        """
        Hash a config object to SHA256 hex.

        Args:
            config: Config object (dict, etc.)

        Returns:
            SHA256 hex string
        """
        if isinstance(config, dict):
            config_json = json.dumps(config, sort_keys=True)
        else:
            config_json = str(config)

        return hashlib.sha256(config_json.encode()).hexdigest()
