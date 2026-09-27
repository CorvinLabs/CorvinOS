"""
Phase 5: Safe Auto-Remediation Engine

Intended to execute automatic fixes for low-risk drifts (PLUGIN_MISSING,
PLUGIN_VERSION_MISMATCH, CONFIG_OVERRIDE) with rollback and audit logging.

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).

NOT IMPLEMENTED: none of the fix executors, the post-fix verification or the
rollback exist. They used to return SUCCESS / ROLLED_BACK after doing nothing
("For now, simulate success"). They now report ``NOT_IMPLEMENTED`` (nothing
was changed, so nothing is rolled back) and verification reports
"not measured" — a drift is never reported fixed that was not fixed. Every
outcome is written to the tenant's core audit chain.
"""

import json
import logging
import shutil
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, Dict, Any
from pathlib import Path

from .drift_categories import DriftCategory, RemediationType, RiskAssessment
from ._audit import remediation_audit

#: Status of a remediation whose executor does not exist. Distinct from
#: FAILED (an attempt that went wrong): nothing was attempted or changed.
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


logger = logging.getLogger(__name__)


@dataclass
class RemediationResult:
    """Result of remediation attempt"""
    status: str  # SUCCESS, FAILED, ROLLED_BACK, NOT_IMPLEMENTED
    drift_id: str
    remediation_type: str
    instance_id: Optional[str] = None
    error: Optional[str] = None
    duration_seconds: float = 0.0
    timestamp: str = None
    previous_state: Optional[Dict[str, Any]] = None
    current_state: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow().isoformat()


_ERROR_CODES = frozenset({
    "plugin_install_executor_not_implemented",
    "plugin_update_executor_not_implemented",
    "config_sync_executor_not_implemented",
    "rollback_not_implemented",
})


def _error_code(error: Optional[str]) -> Optional[str]:
    """A closed code for the chain — free-text errors (``str(exc)``) never enter it."""
    if not error:
        return None
    return error if error in _ERROR_CODES else "remediation_error"


class SafeAutoRemediator:
    """Executes automatic fixes for safe drifts"""

    def __init__(self, audit_backend=None, plugin_installer=None, *, tenant_id: str = "_default"):
        """
        Initialize remediator.

        Args:
            audit_backend: Audit backend for logging (from Phase 4)
            plugin_installer: PluginInstaller from Phase 4 (for install/update)
        """
        self.audit_backend = audit_backend
        self.plugin_installer = plugin_installer
        self.tenant_id = tenant_id
        self.rollback_snapshots = {}  # Store state before remediation

    def remediate_safe_drift(self, assessment: RiskAssessment) -> RemediationResult:
        """
        Execute remediation for a safe drift.

        Args:
            assessment: RiskAssessment from DriftCategorizer

        Returns:
            RemediationResult with status (SUCCESS/FAILED/ROLLED_BACK/NOT_IMPLEMENTED)
        """
        if assessment.category != DriftCategory.SAFE_AUTO_FIX:
            return RemediationResult(
                status="FAILED",
                drift_id=assessment.drift_id,
                remediation_type=assessment.remediation_type.value if assessment.remediation_type else "unknown",
                error=f"Drift {assessment.drift_id} is not safe for auto-remediation (category: {assessment.category.value})",
            )

        # Validate applicability first
        if not self.validate_fix_applicability(assessment):
            return RemediationResult(
                status="FAILED",
                drift_id=assessment.drift_id,
                remediation_type=assessment.remediation_type.value if assessment.remediation_type else "unknown",
                error=f"Remediation not applicable for {assessment.drift_type}",
            )

        # Capture state before remediation (for rollback)
        previous_state = self._capture_state(assessment)

        start_time = datetime.utcnow()
        result = None

        try:
            # Route to correct remediation handler
            if assessment.remediation_type == RemediationType.PLUGIN_INSTALL:
                result = self._remediate_plugin_install(assessment)
            elif assessment.remediation_type == RemediationType.PLUGIN_UPDATE:
                result = self._remediate_plugin_update(assessment)
            elif assessment.remediation_type == RemediationType.CONFIG_SYNC:
                result = self._remediate_config_sync(assessment)
            else:
                raise ValueError(f"Unknown remediation type: {assessment.remediation_type}")

            # Calculate duration
            duration = (datetime.utcnow() - start_time).total_seconds()
            result.duration_seconds = duration
            result.previous_state = previous_state

            if result.status == NOT_IMPLEMENTED:
                # Nothing was changed, so there is nothing to verify or roll back.
                logger.warning(f"Remediation not implemented: {assessment.drift_id} ({result.error})")
                self._audit_remediation(result, "not_implemented")
            # Verify remediation success
            elif result.status == "SUCCESS":
                if self.verify_remediation_success(assessment, result):
                    logger.info(f"✅ Remediation SUCCESS: {assessment.drift_id} ({assessment.drift_type})")
                    self._audit_remediation(result, "success")
                else:
                    # Verification failed; trigger rollback
                    logger.warning(f"⚠️ Remediation verification failed for {assessment.drift_id}; rolling back")
                    result = self._execute_rollback(assessment, previous_state)
                    self._audit_remediation(result, "verification_failed")
            else:
                # Remediation failed; attempt rollback
                logger.error(f"❌ Remediation failed: {assessment.drift_id} ({result.error})")
                if assessment.requires_rollback and previous_state:
                    result = self._execute_rollback(assessment, previous_state)
                self._audit_remediation(result, "failed")

        except Exception as e:
            logger.exception(f"💥 Remediation error for {assessment.drift_id}: {e}")
            duration = (datetime.utcnow() - start_time).total_seconds()
            result = RemediationResult(
                status="FAILED",
                drift_id=assessment.drift_id,
                remediation_type=assessment.remediation_type.value if assessment.remediation_type else "unknown",
                error=str(e),
                duration_seconds=duration,
                previous_state=previous_state,
            )
            if assessment.requires_rollback and previous_state:
                result = self._execute_rollback(assessment, previous_state)
            self._audit_remediation(result, "error")

        return result

    def validate_fix_applicability(self, assessment: RiskAssessment) -> bool:
        """
        Check if safe to apply remediation.

        Returns True if applicable, False otherwise.
        """
        # Check if required backends are available
        if assessment.remediation_type in [
            RemediationType.PLUGIN_INSTALL,
            RemediationType.PLUGIN_UPDATE,
        ]:
            if not self.plugin_installer:
                logger.warning("PluginInstaller not available; cannot remediate plugin drift")
                return False

        # Check if remediation type is in safe list
        safe_types = [
            RemediationType.PLUGIN_INSTALL,
            RemediationType.PLUGIN_UPDATE,
            RemediationType.CONFIG_SYNC,
        ]
        if assessment.remediation_type not in safe_types:
            return False

        return True

    def _remediate_plugin_install(self, assessment: RiskAssessment) -> RemediationResult:
        """Auto-install missing plugin — NOT IMPLEMENTED (fail-closed; nothing is changed)."""
        return RemediationResult(
            status=NOT_IMPLEMENTED,
            drift_id=assessment.drift_id,
            remediation_type=RemediationType.PLUGIN_INSTALL.value,
            error="plugin_install_executor_not_implemented",
        )

    def _remediate_plugin_update(self, assessment: RiskAssessment) -> RemediationResult:
        """Auto-update plugin to correct version — NOT IMPLEMENTED (fail-closed; nothing is changed)."""
        return RemediationResult(
            status=NOT_IMPLEMENTED,
            drift_id=assessment.drift_id,
            remediation_type=RemediationType.PLUGIN_UPDATE.value,
            error="plugin_update_executor_not_implemented",
        )

    def _remediate_config_sync(self, assessment: RiskAssessment) -> RemediationResult:
        """Auto-sync config from canonical source — NOT IMPLEMENTED (fail-closed; nothing is changed)."""
        return RemediationResult(
            status=NOT_IMPLEMENTED,
            drift_id=assessment.drift_id,
            remediation_type=RemediationType.CONFIG_SYNC.value,
            error="config_sync_executor_not_implemented",
        )

    def verify_remediation_success(
        self, assessment: RiskAssessment, result: RemediationResult
    ) -> bool:
        """
        Verify that remediation actually fixed the drift.

        Returns True if verified, False otherwise.
        """
        if result.status != "SUCCESS":
            return False

        # Re-running drift detection to confirm the fix is not implemented.
        # "Not measured" is never "verified" (fail-closed).
        logger.warning(f"Remediation verification not implemented for {assessment.drift_id}")
        return False

    def _capture_state(self, assessment: RiskAssessment) -> Dict[str, Any]:
        """Capture system state before remediation (for rollback)"""
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "drift_id": assessment.drift_id,
            "drift_type": assessment.drift_type,
            # In real implementation, capture plugin versions, config values, etc.
            "snapshot": {},
        }

    def _execute_rollback(
        self, assessment: RiskAssessment, previous_state: Dict[str, Any]
    ) -> RemediationResult:
        """Restore ``previous_state`` — NOT IMPLEMENTED: reports FAILED, never
        ROLLED_BACK (there is no snapshot to restore; ``_capture_state`` records
        none)."""
        logger.error(f"Rollback not implemented for {assessment.drift_id}")
        return RemediationResult(
            status="FAILED",
            drift_id=assessment.drift_id,
            remediation_type=assessment.remediation_type.value if assessment.remediation_type else "unknown",
            error="rollback_not_implemented",
            previous_state=previous_state,
        )

    def _audit_remediation(self, result: RemediationResult, status_detail: str):
        """Write the outcome to the tenant's core chain (raises if it does not
        commit), then an additive copy to an injected ``audit_backend``."""
        remediation_audit(
            "remediation.executed",
            tenant_id=self.tenant_id,
            drift_id=result.drift_id,
            remediation_type=result.remediation_type,
            status=result.status,
            status_detail=status_detail,
            error_code=_error_code(result.error),
        )
        if not self.audit_backend:
            return
        try:
            self.audit_backend.write_event({
                "event_type": "remediation_executed",
                "drift_id": result.drift_id,
                "remediation_type": result.remediation_type,
                "status": result.status,
                "status_detail": status_detail,
                "duration_seconds": result.duration_seconds,
                "timestamp": result.timestamp,
            })
        except Exception as e:
            logger.error(f"audit_backend copy failed (core chain record committed): {e}")
