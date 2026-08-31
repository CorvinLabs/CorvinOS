"""
Queue Corruption Detection Integration — ADR-0298

Integrates QueueIntegrityMonitor with AuditChain and feature flags.
Provides a unified interface for detecting, recovering, and auditing corruption.
"""

import logging
from pathlib import Path
from typing import Optional

from core.audit.chain import AuditChain, AuditEntry
from core.audit.corruption_detection import QueueIntegrityMonitor, QueueIntegrityReport

logger = logging.getLogger(__name__)


class AuditChainWithCorruptionDetection:
    """
    AuditChain wrapper that adds corruption detection and recovery.

    When corruption is detected, automatically logs an audit event and attempts repair.
    Tenant-scoped monitoring with feature flag integration.

    GDPR Art. 30, 32: Every corruption detection is recorded in the audit trail.
    """

    def __init__(
        self,
        log_file: Path,
        *,
        tenant_id: str = "_default",
        feature_enabled: bool = False,
    ):
        """Initialize wrapped audit chain with corruption detection.

        Args:
            log_file: Path to audit queue JSON-L file
            tenant_id: Tenant identifier (keyword-only)
            feature_enabled: Whether corruption detection is enabled (keyword-only)
        """
        self.log_file = Path(log_file)
        self.tenant_id = tenant_id
        self.feature_enabled = feature_enabled
        self.chain = AuditChain(log_file)
        self.monitor = QueueIntegrityMonitor(log_file, tenant_id=tenant_id)

    def record(self, entry: AuditEntry) -> None:
        """Record an entry in the audit chain.

        After recording, check for corruption if feature is enabled.

        Args:
            entry: AuditEntry to record
        """
        # Record normally
        self.chain.record(entry)

        # Check for corruption if enabled
        if self.feature_enabled:
            self._check_corruption()

    def verify_chain(self) -> bool:
        """Verify chain integrity (always runs, regardless of feature flag).

        Returns:
            True if chain is valid

        Raises:
            ChainVerificationError: If chain is tampered
        """
        return self.chain.verify_chain()

    def detect_and_repair_corruption(
        self, auto_repair: bool = True
    ) -> Optional[QueueIntegrityReport]:
        """Detect corruption and optionally repair it.

        On-demand corruption check with optional automatic repair.
        Always logs findings to audit trail.

        Args:
            auto_repair: If True, mark corrupted records and repair file

        Returns:
            QueueIntegrityReport if corruption found, None otherwise
        """
        report = self.monitor.verify_queue_integrity(
            skip_corrupt=True, auto_repair=auto_repair
        )

        if not report.is_valid:
            # Log corruption audit event
            self._log_corruption_event(report)

        return report if not report.is_valid else None

    def _check_corruption(self) -> None:
        """Internal method: check queue for corruption (when feature enabled)."""
        corruption = self.monitor.detect_corruption()
        if corruption:
            logger.warning(
                f"Corruption detected at line {corruption.line_number}: "
                f"{corruption.corruption_type.value} — {corruption.details}",
                extra={"tenant_id": self.tenant_id},
            )
            # Attempt repair
            report = self.monitor.verify_queue_integrity(
                skip_corrupt=True, auto_repair=True
            )
            self._log_corruption_event(report)

    def _log_corruption_event(self, report: QueueIntegrityReport) -> None:
        """Log corruption detection to audit chain.

        GDPR Art. 30, 32: Document every integrity issue.

        Args:
            report: QueueIntegrityReport from detection
        """
        try:
            event_dict = self.monitor.create_corruption_audit_event(
                report, source="audit_chain_monitor"
            )
            # Create audit entry
            entry = AuditEntry(
                event_type=event_dict["event_type"],
                actor=event_dict["actor"],
                action=event_dict["action"],
                resource=event_dict["resource"],
                result=event_dict["result"],
                timestamp=event_dict["timestamp"],
                details=event_dict["details"],
            )
            # Record to chain directly (bypass wrapper to avoid recursion)
            self.chain.record(entry)
        except Exception as e:
            logger.error(
                f"Failed to log corruption event: {e}",
                extra={"tenant_id": self.tenant_id},
            )

    def get_entries(self):
        """Get all entries from chain."""
        return self.chain.get_entries()

    def entry_count(self) -> int:
        """Get number of entries in chain."""
        return self.chain.entry_count()

    def last_hash(self) -> str:
        """Get hash of last entry."""
        return self.chain.last_hash()


def create_audit_chain_with_flag(
    log_file: Path, *, tenant_id: str = "_default", feature_flags: Optional[dict] = None
) -> AuditChainWithCorruptionDetection:
    """Factory function: create audit chain with corruption detection if enabled.

    Args:
        log_file: Path to audit queue JSON-L file
        tenant_id: Tenant identifier (keyword-only)
        feature_flags: Dict of feature flags {flag_id: enabled_bool}

    Returns:
        AuditChainWithCorruptionDetection instance with feature_enabled set per flag
    """
    feature_enabled = False
    if feature_flags and feature_flags.get("queue_corruption_detection_enabled"):
        feature_enabled = True

    return AuditChainWithCorruptionDetection(
        log_file, tenant_id=tenant_id, feature_enabled=feature_enabled
    )
