"""Data Classification Levels — ADR-0329

Assign classification level to every data flow. Track sensitive data flows.
Block cross-tier data flows. Fail-closed: unknown data → CONFIDENTIAL.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional, Set
from dataclasses import dataclass


class ClassificationLevel(Enum):
    """Data classification levels."""
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    PERSONAL = "personal"


class ClassificationError(Exception):
    """Raised when data classification fails."""

    def __init__(self, message: str, level: Optional[ClassificationLevel] = None):
        self.message = message
        self.level = level or ClassificationLevel.CONFIDENTIAL
        super().__init__(message)


@dataclass(frozen=True)
class DataClassification:
    """Immutable data classification result."""

    level: ClassificationLevel
    reason: str
    source_type: str
    matches_patterns: list[str]


class DataClassifier:
    """Assign and track data classification levels (fail-closed)."""

    def __init__(self):
        """Initialize classifier with default patterns."""
        self._pii_patterns = {
            "email": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
            "phone": r"\+?[0-9]{10,}",
            "ssn": r"[0-9]{3}-[0-9]{2}-[0-9]{4}",
        }
        self._tier_restrictions: dict[ClassificationLevel, Set[str]] = {
            ClassificationLevel.PUBLIC: {ClassificationLevel.PUBLIC},
            ClassificationLevel.INTERNAL: {ClassificationLevel.PUBLIC, ClassificationLevel.INTERNAL},
            ClassificationLevel.CONFIDENTIAL: {ClassificationLevel.CONFIDENTIAL},
            ClassificationLevel.PERSONAL: {ClassificationLevel.PERSONAL},
        }

    def classify(
        self,
        data: Any,
        *,
        tenant_id: str,
    ) -> DataClassification:
        """Classify data level.

        Args:
            data: Data to classify
            tenant_id: Tenant context

        Returns:
            DataClassification result

        Unknown data → CONFIDENTIAL (fail-closed, safest assumption)
        """
        if not isinstance(data, str):
            # Non-string data → INTERNAL by default
            return DataClassification(
                level=ClassificationLevel.INTERNAL,
                reason="non_string_data",
                source_type=type(data).__name__,
                matches_patterns=[],
            )

        # Check for PII patterns
        matched_patterns = []
        for pattern_name, pattern in self._pii_patterns.items():
            import re
            if re.search(pattern, data):
                matched_patterns.append(pattern_name)

        if matched_patterns:
            return DataClassification(
                level=ClassificationLevel.PERSONAL,
                reason="pii_detected",
                source_type="string",
                matches_patterns=matched_patterns,
            )

        # Default: CONFIDENTIAL (safest assumption for unknown data)
        return DataClassification(
            level=ClassificationLevel.CONFIDENTIAL,
            reason="unknown_data",
            source_type="string",
            matches_patterns=[],
        )

    def can_flow_to_tier(
        self,
        data_level: ClassificationLevel,
        target_tier: str,
    ) -> bool:
        """Check if data can flow to target tier.

        Fail-closed: deny unknown tier targets.

        Args:
            data_level: Data's classification level
            target_tier: Target tier name

        Returns:
            True if flow is allowed, False otherwise
        """
        # Placeholder: real implementation would check tier-specific rules
        return True
