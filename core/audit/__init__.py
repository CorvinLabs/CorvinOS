"""
Audit Durability + L16 — ADR-0299

Hash-chained, fsync'd audit log with verification.
Foundation for all compliance (GDPR Art. 30/32, EU AI Act).
"""

from core.audit.chain import AuditChain, AuditEntry, ChainVerificationError

__all__ = ["AuditChain", "AuditEntry", "ChainVerificationError"]
