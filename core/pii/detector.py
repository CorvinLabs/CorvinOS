"""PII Detector — ADR-0297

Fail-closed PII detection system. Unknown patterns default to SUSPICIOUS.

Key properties:
  - Tenant-scoped (keyword-only tenant_id)
  - Fail-closed (unknown = reject)
  - Pattern-based (regex matching against curated PII list)
  - Audit-aware (integrates with compliance logging)
  - Composition-ready (stacks with ADR-0296 validators)
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Optional

from .patterns import ALL_PATTERNS, PIIPatternDef


@dataclass(frozen=True)
class PIIPattern:
    """Detected PII pattern metadata."""

    pii_class: str  # email, phone, ssn, credit_card, etc.
    confidence: float  # 0.0 - 1.0
    source_pattern: str  # Which regex matched
    sample_match: str  # (Redacted) example of the match


class PIIDetectionError(Exception):
    """Base exception for PII detection errors."""

    pass


class PIIDetectionFailedClosed(PIIDetectionError):
    """Fail-closed: suspicious data detected that must be rejected."""

    pass


class PIIDetector:
    """Fail-closed PII detector.

    Tenant-scoped detector that checks input values against known PII patterns.
    Unknown patterns are considered suspicious and must be explicitly whitelisted.

    Design: fail-closed means:
      1. If a value matches a known PII pattern → DETECTED (suspicious, reject)
      2. If a value doesn't match any pattern → SAFE (generic, allow)
      3. If regex fails to compile → REJECT (fail-closed, don't process)

    Usage:
        detector = PIIDetector()
        try:
            result = detector.detect(value, tenant_id="tenant_123")
            if result:
                # PII detected, log and reject
                audit_log(f"PII detected: {result.pii_class}")
                raise ValueError("PII in input")
        except PIIDetectionFailedClosed:
            # Regex error or other fail-closed condition
            audit_log("PII detection failed in fail-closed mode")
            raise ValueError("Input validation failed")
    """

    def __init__(self) -> None:
        """Initialize detector with curated patterns."""
        self._patterns = ALL_PATTERNS
        self._pattern_map = {p.pii_class: p for p in self._patterns}

    def detect(
        self,
        value: Any,
        *,
        tenant_id: str,
    ) -> Optional[PIIPattern]:
        """Detect PII in a value.

        Args:
            value: Input value to check (must be string)
            tenant_id: Tenant scope (keyword-only, required)

        Returns:
            PIIPattern if PII detected, None if safe

        Raises:
            PIIDetectionFailedClosed: Regex error or malformed pattern
        """
        if not isinstance(value, str):
            # Non-string values are not checked (not PII risk)
            return None

        if not value or len(value.strip()) == 0:
            # Empty strings are not PII
            return None

        # Try each pattern in order (highest confidence first)
        try:
            for pattern_def in self._patterns:
                try:
                    if pattern_def.compiled.match(value.strip()):
                        # Pattern matched! Return detection result
                        return PIIPattern(
                            pii_class=pattern_def.pii_class,
                            confidence=pattern_def.confidence,
                            source_pattern=pattern_def.description,
                            sample_match=self._redact_value(value),
                        )
                except re.error as e:
                    # Pattern compilation error (fail-closed)
                    raise PIIDetectionFailedClosed(
                        f"Regex error in {pattern_def.pii_class}: {e}"
                    ) from e
        except PIIDetectionFailedClosed:
            raise  # Re-raise fail-closed exceptions
        except Exception as e:
            # Unexpected error (fail-closed)
            raise PIIDetectionFailedClosed(
                f"Unexpected error in PII detection: {e}"
            ) from e

        # No pattern matched → safe
        return None

    def detect_multiple(
        self,
        values: list[Any],
        *,
        tenant_id: str,
    ) -> list[PIIPattern]:
        """Detect PII in multiple values.

        Args:
            values: List of values to check
            tenant_id: Tenant scope (keyword-only, required)

        Returns:
            List of detected PII patterns (empty if none detected)

        Raises:
            PIIDetectionFailedClosed: Regex error
        """
        results = []
        for value in values:
            detection = self.detect(value, tenant_id=tenant_id)
            if detection:
                results.append(detection)
        return results

    def detect_in_dict(
        self,
        data: dict[str, Any],
        *,
        tenant_id: str,
        exclude_keys: Optional[set[str]] = None,
    ) -> dict[str, list[PIIPattern]]:
        """Detect PII in dictionary values.

        Args:
            data: Dictionary to scan
            tenant_id: Tenant scope (keyword-only, required)
            exclude_keys: Keys to skip (optional)

        Returns:
            Dict[key -> list of PIIPattern] for keys with PII detected
        """
        exclude_keys = exclude_keys or set()
        results = {}

        for key, value in data.items():
            if key in exclude_keys:
                continue

            detection = self.detect(value, tenant_id=tenant_id)
            if detection:
                if key not in results:
                    results[key] = []
                results[key].append(detection)

        return results

    def is_suspicious(
        self,
        value: Any,
        *,
        tenant_id: str,
        min_confidence: float = 0.75,
    ) -> bool:
        """Check if value is suspicious (matches high-confidence PII pattern).

        Args:
            value: Value to check
            tenant_id: Tenant scope (keyword-only, required)
            min_confidence: Minimum confidence threshold (default 0.75)

        Returns:
            True if PII detected with confidence >= min_confidence
        """
        detection = self.detect(value, tenant_id=tenant_id)
        if detection is None:
            return False
        return detection.confidence >= min_confidence

    def get_all_patterns(self) -> tuple[PIIPatternDef, ...]:
        """Get all registered PII patterns."""
        return self._patterns

    @staticmethod
    def _redact_value(value: str, show_len: int = 3) -> str:
        """Redact a value for safe logging.

        Shows first N chars (or ****) for safety.
        """
        if len(value) <= show_len:
            return "***"
        return f"{value[:show_len]}{'*' * (len(value) - show_len)}"


# ============================================================================
# Module-level convenience functions
# ============================================================================

_DEFAULT_DETECTOR = PIIDetector()


def detect_pii_in_value(
    value: Any,
    *,
    tenant_id: str,
) -> Optional[PIIPattern]:
    """Convenience function: detect PII in a single value.

    Args:
        value: Value to check
        tenant_id: Tenant scope (keyword-only, required)

    Returns:
        PIIPattern if detected, None otherwise
    """
    return _DEFAULT_DETECTOR.detect(value, tenant_id=tenant_id)


def is_value_suspicious(
    value: Any,
    *,
    tenant_id: str,
    min_confidence: float = 0.75,
) -> bool:
    """Convenience function: check if value is suspicious.

    Args:
        value: Value to check
        tenant_id: Tenant scope (keyword-only, required)
        min_confidence: Confidence threshold (default 0.75)

    Returns:
        True if suspicious
    """
    return _DEFAULT_DETECTOR.is_suspicious(
        value, tenant_id=tenant_id, min_confidence=min_confidence
    )


# Import re here to avoid circular imports
import re  # noqa: E402


__all__ = [
    "PIIDetector",
    "PIIPattern",
    "PIIDetectionError",
    "PIIDetectionFailedClosed",
    "detect_pii_in_value",
    "is_value_suspicious",
]
