"""
Phase 2: Context Filter Skill

Filters context based on classified intent.
Reduces noise while preserving signal.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Dict, Any, Tuple
from intent_classifier_skill import IntentClassifier, IntentType


@dataclass
class FilterResult:
    """Result of context filtering."""
    original_size: int
    filtered_size: int
    reduction_pct: float
    filtered_context: Dict[str, Any]
    audit_hash: str = ""

    def __post_init__(self):
        if not self.audit_hash:
            content = f"{self.original_size}:{self.filtered_size}:{json.dumps(self.filtered_context, sort_keys=True)}"
            self.audit_hash = hashlib.sha256(content.encode()).hexdigest()[:16]


class ContextFilter:
    """Filter context based on intent classification."""

    def __init__(self, confidence_threshold: float = 0.50):
        self.confidence_threshold = confidence_threshold
        self.classifier = IntentClassifier()

    def filter(self, full_context: Dict[str, Any]) -> Tuple[Dict[str, Any], bool, str]:
        """
        Filter context.

        Returns:
            (filtered_context, used_filtering, reason)
            - used_filtering: True if intent-based filtering was used
            - reason: "intent_confident", "fallback_low_confidence", "fallback_disabled", etc.
        """
        # Classify intent
        classified = self.classifier.classify(full_context)

        # Check confidence
        if classified.confidence < self.confidence_threshold:
            # Low confidence → fall back to full context
            return full_context, False, f"fallback:confidence_too_low:{classified.confidence:.2f}"

        # Use filtered context
        filtered = classified.filtered_context
        reduction_pct = 100 * (1 - len(json.dumps(filtered)) / max(len(json.dumps(full_context)), 1))

        return filtered, True, f"intent:{classified.intent_type.value}"

    def validate_no_pii(self, filtered_context: Dict[str, Any]) -> Tuple[bool, list]:
        """
        Validate that filtered context has no PII.

        Returns:
            (is_safe, found_pii_fields)
        """
        pii_patterns = ["email", "phone", "password", "ssn", "credit_card", "secret", "token", "api_key"]
        found_pii = []

        for field, value in filtered_context.items():
            field_lower = field.lower()
            for pii_pattern in pii_patterns:
                if pii_pattern in field_lower:
                    found_pii.append(field)
                    break

        return len(found_pii) == 0, found_pii


def filter_context(full_context: Dict[str, Any], force_full: bool = False) -> Dict[str, Any]:
    """
    Top-level function to filter context.

    Args:
        full_context: Complete context from Phase 1
        force_full: If True, skip filtering (use full context)

    Returns:
        Filtered context (or full context if filtering is disabled)
    """
    if force_full:
        return full_context

    filter_obj = ContextFilter()
    filtered, used_filtering, reason = filter_obj.filter(full_context)

    # Validate no PII
    is_safe, pii_fields = filter_obj.validate_no_pii(filtered)
    if not is_safe:
        # PII detected → fall back to full context (safety-first)
        return full_context

    return filtered
