"""
PII Detector & Scrubber — ADR-0297

Detects and scrubs personally identifiable information.
Audit trail of detections logged (fail-closed).
"""

from typing import Any, Optional

from core.pii.patterns import PII_PATTERNS, detect_pii_types, scrub_pii


class PIIDetector:
    """Detect PII in text/data."""

    @staticmethod
    def detect(text: str) -> list[str]:
        """Detect PII types present in text."""
        if not isinstance(text, str):
            return []
        return detect_pii_types(text)

    @staticmethod
    def has_pii(text: str) -> bool:
        """Check if text contains any PII."""
        if not isinstance(text, str):
            return False
        return len(detect_pii_types(text)) > 0


class PIIScrubber:
    """Scrub PII from text/data."""

    def __init__(self, audit_log_fn=None):
        """
        Initialize scrubber.

        Args:
            audit_log_fn: Optional function to call when PII detected
                         (for compliance logging)
        """
        self.audit_log_fn = audit_log_fn

    def scrub(self, text: str, log_detection: bool = True) -> str:
        """
        Scrub PII from text.

        Args:
            text: Text to scrub
            log_detection: If True, log any detections to audit trail

        Returns:
            Scrubbed text (PII replaced with [TYPE])
        """
        if not isinstance(text, str):
            return text

        # Detect before scrubbing
        detected = detect_pii_types(text)

        # Scrub
        scrubbed = scrub_pii(text)

        # Audit log if detections found
        if detected and log_detection and self.audit_log_fn:
            self.audit_log_fn(
                {
                    "event": "pii_detected_and_scrubbed",
                    "types": detected,
                    "action": "scrubbed",
                    "text_length": len(text),
                }
            )

        return scrubbed

    def scrub_dict(self, data: dict[str, Any], log_detection: bool = True) -> dict[str, Any]:
        """
        Scrub PII from all string values in a dict.

        Recursively scrubs nested dicts/lists.
        """
        if not isinstance(data, dict):
            return data

        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                result[key] = self.scrub(value, log_detection=log_detection)
            elif isinstance(value, dict):
                result[key] = self.scrub_dict(value, log_detection=log_detection)
            elif isinstance(value, list):
                result[key] = [
                    self.scrub(item, log_detection=log_detection) if isinstance(item, str) else item
                    for item in value
                ]
            else:
                result[key] = value

        return result

    def should_log_raw(self, text: str) -> bool:
        """Check if text is safe to log as-is (contains no PII)."""
        return not PIIDetector.has_pii(text)
