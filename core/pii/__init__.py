"""
PII Detection & Fail-Closed Scrubber — ADR-0297

Detects personally identifiable information (email, phone, credit card, SSN, IP, etc.)
and scrubs it before logging. Audit trail of detections.

Exports:
- PIIDetector: Main detector with confidence scoring
- PIIScrubber: Scrubber with audit logging
- PIIFinding: Detection result object
- PII_PATTERNS: Pattern registry
- Utility functions: detect_pii_in_value, is_value_suspicious
"""

from core.pii.detector import PIIDetector, PIIScrubber, PIIFinding
from core.pii.patterns import (
    PII_PATTERNS,
    PIIPattern,
    detect_pii_types,
    scrub_pii,
    EMAIL_RFC5322,
    PHONE_US,
    PHONE_INTL,
    US_SSN,
    CREDIT_CARD,
    IBAN,
    PASSPORT,
    NATIONAL_ID,
    DATE_OF_BIRTH,
    NAME,
    ADDRESS,
    AWS_ACCESS_KEY,
    API_KEY,
    SQL_PASSWORD,
    IPv4,
    IPv6,
)
from core.pii.redactor import (
    PIIRedactor,
    redact_pii,
    redact_dict_for_audit,
)
# Fail-CLOSED free-text + secret gate (ADR-0297). `has_sensitive` is a bool GATE,
# not a scrubber: callers use it to DROP a whole field before injection (a
# partially-scrubbed secret still leaks). It RAISES PIIDetectionFailedClosed on a
# scan error, which the caller MUST treat as "sensitive" (drop), never as clean.
from core.pii.sensitive import (
    PIIDetectionFailedClosed,
    detect_sensitive_types,
    has_sensitive,
)


# Utility functions for convenience
def detect_pii_in_value(value: str, *, tenant_id: str = "_default") -> PIIFinding:
    """Detect PII in a single value.

    Args:
        value: Value to check
        tenant_id: Tenant identifier (keyword-only)

    Returns:
        PIIFinding if PII detected, None otherwise
    """
    detector = PIIDetector()
    return detector.detect(value, tenant_id=tenant_id)


def is_value_suspicious(value: str, *, tenant_id: str = "_default") -> bool:
    """Check if value is suspicious (contains PII).

    Args:
        value: Value to check
        tenant_id: Tenant identifier (keyword-only)

    Returns:
        True if PII detected
    """
    detector = PIIDetector()
    return detector.has_pii(value, tenant_id=tenant_id)


__all__ = [
    # Classes
    "PIIDetector",
    "PIIScrubber",
    "PIIRedactor",
    "PIIFinding",
    "PIIPattern",
    # Patterns
    "PII_PATTERNS",
    "EMAIL_RFC5322",
    "PHONE_US",
    "PHONE_INTL",
    "US_SSN",
    "CREDIT_CARD",
    "IBAN",
    "PASSPORT",
    "NATIONAL_ID",
    "DATE_OF_BIRTH",
    "NAME",
    "ADDRESS",
    "AWS_ACCESS_KEY",
    "API_KEY",
    "SQL_PASSWORD",
    "IPv4",
    "IPv6",
    # Functions
    "detect_pii_types",
    "scrub_pii",
    "redact_pii",
    "redact_dict_for_audit",
    "detect_pii_in_value",
    "is_value_suspicious",
    # Fail-closed sensitive-content gate (ADR-0297)
    "has_sensitive",
    "detect_sensitive_types",
    # Exceptions
    "PIIDetectionFailedClosed",
]
