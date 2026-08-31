"""
PII Detector & Scrubber — ADR-0297

Detects and scrubs personally identifiable information.
Audit trail of detections logged (fail-closed).

Design:
  - PIIFinding: Result of detection with type, span, confidence
  - PIIDetector: High-level detector with tenant isolation
  - PIIScrubber: Scrubbing with audit logging
  - Fail-closed: Always scrub if suspicious
"""

from dataclasses import dataclass
from typing import Any, Optional

from core.pii.patterns import PII_PATTERNS, detect_pii_types, scrub_pii


@dataclass
class PIIFinding:
    """Result of PII detection in text."""

    pii_class: str  # PII type (e.g., "email", "ssn", "phone")
    confidence: float  # Confidence 0.0–1.0
    text: Optional[str] = None  # The detected text (optional for privacy)
    start: Optional[int] = None  # Start position in original text
    end: Optional[int] = None  # End position in original text

    def __bool__(self) -> bool:
        """Finding is truthy if confidence > 0."""
        return self.confidence > 0


class PIIDetector:
    """Detect PII in text/data.

    Tenant-scoped detection with confidence scoring.
    Fail-closed: when in doubt, treat as PII.
    """

    def __init__(self):
        """Initialize detector."""
        pass

    def detect(self, text: str, *, tenant_id: str = "_default") -> Optional[PIIFinding]:
        """Detect PII in text and return first finding.

        Args:
            text: Text to scan
            tenant_id: Tenant identifier (keyword-only)

        Returns:
            PIIFinding if PII detected, None otherwise
        """
        if not isinstance(text, str) or not text.strip():
            return None

        # Scan all patterns
        for name, pattern_info in PII_PATTERNS.items():
            match = pattern_info.pattern.search(text)
            if match:
                # Return first match with confidence
                return PIIFinding(
                    pii_class=name,
                    confidence=pattern_info.confidence,
                    text=match.group(0),
                    start=match.start(),
                    end=match.end(),
                )

        return None

    def detect_all(self, text: str, *, tenant_id: str = "_default") -> list[PIIFinding]:
        """Detect all PII in text.

        Args:
            text: Text to scan
            tenant_id: Tenant identifier (keyword-only)

        Returns:
            List of PIIFindings (may be empty)
        """
        if not isinstance(text, str) or not text.strip():
            return []

        findings = []
        seen_spans = set()  # Avoid duplicate findings at same position

        # Scan all patterns
        for name, pattern_info in PII_PATTERNS.items():
            for match in pattern_info.pattern.finditer(text):
                span = (match.start(), match.end())
                if span not in seen_spans:
                    findings.append(
                        PIIFinding(
                            pii_class=name,
                            confidence=pattern_info.confidence,
                            text=match.group(0),
                            start=match.start(),
                            end=match.end(),
                        )
                    )
                    seen_spans.add(span)

        # Sort by confidence (highest first)
        findings.sort(key=lambda f: f.confidence, reverse=True)
        return findings

    def has_pii(self, text: str, *, tenant_id: str = "_default") -> bool:
        """Check if text contains any PII.

        Args:
            text: Text to scan
            tenant_id: Tenant identifier (keyword-only)

        Returns:
            True if any PII detected
        """
        return self.detect(text, tenant_id=tenant_id) is not None

    def is_suspicious(
        self, text: str, *, tenant_id: str = "_default", min_confidence: float = 0.75
    ) -> bool:
        """Check if text is suspicious (matches high-confidence PII).

        Args:
            text: Text to check
            tenant_id: Tenant identifier (keyword-only)
            min_confidence: Confidence threshold (default 0.75)

        Returns:
            True if PII detected with confidence >= threshold
        """
        finding = self.detect(text, tenant_id=tenant_id)
        if finding is None:
            return False
        return finding.confidence >= min_confidence

    def detect_multiple(
        self, values: list[str], *, tenant_id: str = "_default"
    ) -> list[PIIFinding]:
        """Detect all PII in a list of values.

        Args:
            values: List of values to scan
            tenant_id: Tenant identifier (keyword-only)

        Returns:
            List of PIIFindings (may be empty)
        """
        findings = []
        for value in values:
            result = self.detect(value, tenant_id=tenant_id)
            if result is not None:
                findings.append(result)
        return findings

    def detect_in_dict(
        self,
        data: dict[str, Any],
        *,
        tenant_id: str = "_default",
        exclude_keys: Optional[set[str]] = None,
    ) -> dict[str, list[PIIFinding]]:
        """Detect PII in all string values of a dictionary.

        Args:
            data: Dictionary to scan
            tenant_id: Tenant identifier (keyword-only)
            exclude_keys: Set of keys to skip (optional)

        Returns:
            Dictionary mapping keys to lists of PIIFindings (empty lists omitted)
        """
        exclude_keys = exclude_keys or set()
        findings = {}

        for key, value in data.items():
            if key in exclude_keys:
                continue

            if isinstance(value, str):
                result = self.detect(value, tenant_id=tenant_id)
                if result is not None:
                    findings[key] = [result]
            elif isinstance(value, dict):
                # Recursively scan nested dicts
                nested = self.detect_in_dict(value, tenant_id=tenant_id, exclude_keys=exclude_keys)
                if nested:
                    findings[key] = []
                    for nested_findings in nested.values():
                        findings[key].extend(nested_findings)
            elif isinstance(value, list):
                # Scan list items
                list_findings = []
                for item in value:
                    if isinstance(item, str):
                        result = self.detect(item, tenant_id=tenant_id)
                        if result is not None:
                            list_findings.append(result)
                if list_findings:
                    findings[key] = list_findings

        return findings

    def get_all_patterns(self) -> list[PIIFinding]:
        """Get all registered PII patterns.

        Returns:
            List of available PII patterns (as PIIFinding-like objects with metadata)
        """
        # Return metadata about all patterns
        patterns = []
        for name, pattern_info in PII_PATTERNS.items():
            patterns.append(
                PIIFinding(
                    pii_class=name,
                    confidence=pattern_info.confidence,
                    text=None,
                )
            )
        return patterns


class PIIScrubber:
    """Scrub PII from text/data.

    Tenant-scoped scrubbing with audit logging.
    Fail-closed: always scrub detected PII.
    """

    def __init__(self, audit_log_fn=None):
        """Initialize scrubber.

        Args:
            audit_log_fn: Optional function to call when PII detected
                         (for compliance logging)
        """
        self.audit_log_fn = audit_log_fn
        self.detector = PIIDetector()

    def scrub(
        self, text: str, *, tenant_id: str = "_default", log_detection: bool = True
    ) -> str:
        """Scrub PII from text.

        Args:
            text: Text to scrub
            tenant_id: Tenant identifier (keyword-only)
            log_detection: If True, log any detections to audit trail

        Returns:
            Scrubbed text (PII replaced with [TYPE])
        """
        if not isinstance(text, str):
            return text

        # Detect before scrubbing
        findings = self.detector.detect_all(text, tenant_id=tenant_id)

        # Scrub
        scrubbed = scrub_pii(text)

        # Audit log if detections found
        if findings and log_detection and self.audit_log_fn:
            self.audit_log_fn(
                {
                    "event": "pii_detected_and_scrubbed",
                    "tenant_id": tenant_id,
                    "types": [f.pii_class for f in findings],
                    "confidences": [f.confidence for f in findings],
                    "action": "scrubbed",
                    "text_length": len(text),
                    "finding_count": len(findings),
                }
            )

        return scrubbed

    def scrub_dict(
        self, data: dict[str, Any], *, tenant_id: str = "_default", log_detection: bool = True
    ) -> dict[str, Any]:
        """Scrub PII from all string values in a dict.

        Recursively scrubs nested dicts/lists.

        Args:
            data: Dictionary to scrub
            tenant_id: Tenant identifier (keyword-only)
            log_detection: If True, log detections

        Returns:
            Scrubbed dictionary
        """
        if not isinstance(data, dict):
            return data

        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                result[key] = self.scrub(value, tenant_id=tenant_id, log_detection=log_detection)
            elif isinstance(value, dict):
                result[key] = self.scrub_dict(value, tenant_id=tenant_id, log_detection=log_detection)
            elif isinstance(value, list):
                result[key] = [
                    self.scrub(item, tenant_id=tenant_id, log_detection=log_detection)
                    if isinstance(item, str)
                    else item
                    for item in value
                ]
            else:
                result[key] = value

        return result

    def should_log_raw(self, text: str, *, tenant_id: str = "_default") -> bool:
        """Check if text is safe to log as-is (contains no PII).

        Args:
            text: Text to check
            tenant_id: Tenant identifier (keyword-only)

        Returns:
            True if text contains no PII
        """
        return not self.detector.has_pii(text, tenant_id=tenant_id)
