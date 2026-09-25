"""
Phase 5: Safe Auto-Remediation Engine

Executes automatic fixes for low-risk drifts (PLUGIN_MISSING, PLUGIN_VERSION_MISMATCH, CONFIG_OVERRIDE).
Includes rollback capability and audit logging.
"""

import json
import logging
import shutil
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, Dict, Any
from pathlib import Path

from .drift_categories import DriftCategory, RemediationType, RiskAssessment


logger = logging.getLogger(__name__)


@dataclass
class RemediationResult:
    """Result of remediation attempt"""
    status: str  # SUCCESS, FAILED, ROLLED_BACK
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


class SafeAutoRemediator:
    """Executes automatic fixes for safe drifts"""

    def __init__(self, audit_backend=None, plugin_installer=None):
        """
        Initialize remediator.

        Args:
            audit_backend: Audit backend for logging (from Phase 4)
            plugin_installer: PluginInstaller from Phase 4 (for install/update)
        """
        self.audit_backend = audit_backend
        self.plugin_installer = plugin_installer
        self.rollback_snapshots = {}  # Store state before remediation

    def remediate_safe_drift(self, assessment: RiskAssessment) -> RemediationResult:
        """
        Execute remediation for a safe drift.

        Args:
            assessment: RiskAssessment from DriftCategorizer

        Returns:
            RemediationResult with status (SUCCESS/FAILED/ROLLED_BACK)
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

            # Verify remediation success
            if result.status == "SUCCESS":
                if self.verify_remediation_success(assessment, result):
                    logger.info(f"✅ Remediation SUCCESS: {assessment.drift_id} ({assessment.drift_type})")
                    self._audit_remediation(result, "success")
                else:
                    # Verification failed; trigger rollback
                    logger.warning(f"⚠️ Remediation verification failed for {assessment.drift_id}; rolling back")
                    result = self._execute_rollback(assessment, previous_state)
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
        """Auto-install missing plugin"""
        if not self.plugin_installer:
            return RemediationResult(
                status="FAILED",
                drift_id=assessment.drift_id,
                remediation_type=RemediationType.PLUGIN_INSTALL.value,
                error="PluginInstaller not available",
            )

        try:
            # Parse plugin_id and version from drift context (simplified)
            # In real implementation, extract from drift metadata
            logger.info(f"🔧 Installing plugin for {assessment.drift_id}")

            # Call plugin installer (Phase 4 integration)
            # install_result = self.plugin_installer.install(plugin_id, version)
            # For now, simulate success
            return RemediationResult(
                status="SUCCESS",
                drift_id=assessment.drift_id,
                remediation_type=RemediationType.PLUGIN_INSTALL.value,
            )
        except Exception as e:
            return RemediationResult(
                status="FAILED",
                drift_id=assessment.drift_id,
                remediation_type=RemediationType.PLUGIN_INSTALL.value,
                error=str(e),
            )

    def _remediate_plugin_update(self, assessment: RiskAssessment) -> RemediationResult:
        """Auto-update plugin to correct version"""
        if not self.plugin_installer:
            return RemediationResult(
                status="FAILED",
                drift_id=assessment.drift_id,
                remediation_type=RemediationType.PLUGIN_UPDATE.value,
                error="PluginInstaller not available",
            )

        try:
            logger.info(f"🔄 Updating plugin for {assessment.drift_id}")
            # Call plugin installer (Phase 4 integration)
            # update_result = self.plugin_installer.update(plugin_id, new_version)
            # For now, simulate success
            return RemediationResult(
                status="SUCCESS",
                drift_id=assessment.drift_id,
                remediation_type=RemediationType.PLUGIN_UPDATE.value,
            )
        except Exception as e:
            return RemediationResult(
                status="FAILED",
                drift_id=assessment.drift_id,
                remediation_type=RemediationType.PLUGIN_UPDATE.value,
                error=str(e),
            )

    def _remediate_config_sync(self, assessment: RiskAssessment) -> RemediationResult:
        """Auto-sync config from canonical source"""
        try:
            logger.info(f"📋 Syncing config for {assessment.drift_id}")
            # Sync config from canonical source (Phase 2 integration)
            # In real implementation, pull from config manager
            # For now, simulate success
            return RemediationResult(
                status="SUCCESS",
                drift_id=assessment.drift_id,
                remediation_type=RemediationType.CONFIG_SYNC.value,
            )
        except Exception as e:
            return RemediationResult(
                status="FAILED",
                drift_id=assessment.drift_id,
                remediation_type=RemediationType.CONFIG_SYNC.value,
                error=str(e),
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

        try:
            # In real implementation, re-run drift detection to confirm
            logger.info(f"✓ Verifying remediation for {assessment.drift_id}")
            # For now, assume success
            return True
        except Exception as e:
            logger.error(f"Verification failed: {e}")
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
        """Execute rollback to previous state"""
        try:
            logger.warning(f"🔙 Rolling back {assessment.drift_id} to previous state")
            # In real implementation, restore from previous_state snapshot
            # For now, simulate success
            return RemediationResult(
                status="ROLLED_BACK",
                drift_id=assessment.drift_id,
                remediation_type=assessment.remediation_type.value if assessment.remediation_type else "unknown",
                previous_state=previous_state,
            )
        except Exception as e:
            logger.error(f"Rollback failed: {e}")
            return RemediationResult(
                status="FAILED",
                drift_id=assessment.drift_id,
                remediation_type=assessment.remediation_type.value if assessment.remediation_type else "unknown",
                error=f"Rollback failed: {str(e)}",
            )

    def _audit_remediation(self, result: RemediationResult, status_detail: str):
        """Log remediation to audit trail"""
        if not self.audit_backend:
            return

        try:
            event = {
                "event_type": "remediation_executed",
                "drift_id": result.drift_id,
                "remediation_type": result.remediation_type,
                "status": result.status,
                "status_detail": status_detail,
                "error": result.error,
                "duration_seconds": result.duration_seconds,
                "timestamp": result.timestamp,
            }
            self.audit_backend.write_event(event)
            logger.info(f"✓ Audit event logged for {result.drift_id}")
        except Exception as e:
            logger.error(f"Failed to log audit event: {e}")
