"""PII Detection & Fail-Closed — ADR-0297

Compliance layer for detecting personally identifiable information (PII) in input data.
Fail-closed: unknown patterns default to SUSPICIOUS and must be explicitly whitelisted.

Design principles:
  - Tenant isolation (keyword-only tenant_id)
  - Fail-closed (unknown = reject)
  - Audit logging (every detection logged + redacted)
  - Composition with validators (ADR-0296)
  - GDPR Art. 5 (integrity/confidentiality) compliance

Public API:
  - PIIDetector: main detector class
  - PIIPattern: detected pattern metadata
  - detect_pii_in_value(): convenience function
  - redact_pii(): safety utility for logging
"""

from __future__ import annotations

from .detector import (
    PIIDetector,
    PIIPattern,
    PIIDetectionError,
    PIIDetectionFailedClosed,
    detect_pii_in_value,
    is_value_suspicious,
)
from .redactor import redact_pii, redact_dict_for_audit, PIIRedactor

__all__ = [
    "PIIDetector",
    "PIIPattern",
    "PIIDetectionError",
    "PIIDetectionFailedClosed",
    "detect_pii_in_value",
    "is_value_suspicious",
    "redact_pii",
    "redact_dict_for_audit",
    "PIIRedactor",
]
