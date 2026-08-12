"""
Audit Durability + L16 — ADR-0299, ADR-0298

Hash-chained, fsync'd audit log with verification and corruption detection.
Foundation for all compliance (GDPR Art. 30/32, EU AI Act).
"""

from core.audit.chain import AuditChain, AuditEntry, ChainVerificationError
from core.audit.corruption_detection import (
    QueueIntegrityMonitor,
    QueueIntegrityReport,
    CorruptionRecord,
    CorruptionType,
)
from core.audit.durability import (
    AuditDurabilityManager,
    WALRecord,
    WALRecordType,
    CrashRecoveryReport,
    DurabilityMetrics,
)

__all__ = [
    "AuditChain",
    "AuditEntry",
    "ChainVerificationError",
    "QueueIntegrityMonitor",
    "QueueIntegrityReport",
    "CorruptionRecord",
    "CorruptionType",
    "AuditDurabilityManager",
    "WALRecord",
    "WALRecordType",
    "CrashRecoveryReport",
    "DurabilityMetrics",
]
