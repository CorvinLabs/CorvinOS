"""
PII Detection & Fail-Closed Scrubber — ADR-0297

Detects personally identifiable information (email, phone, credit card, SSN, IP, etc.)
and scrubs it before logging. Audit trail of detections.
"""

from core.pii.detector import PIIDetector, PIIScrubber
from core.pii.patterns import PII_PATTERNS

__all__ = ["PIIDetector", "PIIScrubber", "PII_PATTERNS"]
