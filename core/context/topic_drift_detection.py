"""Topic Drift Detection — k=3 of Context-Pipeline v2 LDD.

Detects unexpected topic shifts before injecting context into system prompt.
Classifies additions as same-family, prerequisite, tangential, or topic-shift.

ADR-0399: Context-Pipeline v2
k=3: Topic Drift Detection (target: 95%+ accuracy, <10% false positives)
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from core.context import PipelineAddition

logger = logging.getLogger(__name__)


class DriftClassification(Enum):
    """Classification of whether addition causes topic drift."""
    SAME_FAMILY = "same_family"        # Same topic domain
    HARD_BLOCKER = "hard_blocker"      # Blocking prerequisite (safety/audit)
    ORDER_SUGGESTION = "order_suggest" # Soft prerequisite (consider doing X first)
    TANGENTIAL = "tangential"          # Related but not blocking
    TOPIC_SHIFT = "topic_shift"        # Different topic, should ask


@dataclass
class DriftAnalysis:
    """Result of drift classification for an addition."""

    classification: DriftClassification
    confidence: float  # 0.0-1.0
    reasoning: str
    recommended_action: str  # "include", "flag", "ask_user", "skip"

    def is_justified_addition(self) -> bool:
        """Whether this addition should be included in context."""
        return self.classification != DriftClassification.TOPIC_SHIFT


class TopicDriftDetector:
    """Detects topic drift in pipeline additions.

    Analyzes whether an addition is on-topic, blocks the goal, or shifts topics.
    """

    # Keywords that indicate blocking/safety concerns
    BLOCKING_KEYWORDS = {
        "prerequisite", "requires", "must", "blocking", "audit", "safety",
        "compliance", "constraint", "critical", "fail-closed", "verify",
        "validate", "permission", "access", "security", "protection",
    }

    # Keywords that indicate architectural guidance (precedent)
    PRECEDENT_KEYWORDS = {
        "adr", "pattern", "practice", "convention", "standard", "approach",
        "design", "architecture", "best", "follows", "consistent", "aligned",
    }

    # Keywords that indicate tangential/optional info
    TANGENTIAL_KEYWORDS = {
        "also", "related", "meanwhile", "by", "the", "way", "consider",
        "might", "could", "optional", "alternative", "different", "instead",
    }

    # Keywords that indicate topic shift (different area)
    SHIFT_KEYWORDS = {
        "instead", "rather", "forget", "focus", "skip", "ignore", "abandon",
        "redirect", "change", "switch", "different", "other", "separate",
    }

    def __init__(self, original_goal: str):
        """Initialize detector with original goal.

        Args:
            original_goal: User's stated goal (immutable)
        """
        self.original_goal = original_goal.lower()
        self.goal_keywords = set(self.original_goal.split())

    def analyze_addition(self, addition: PipelineAddition) -> DriftAnalysis:
        """Analyze whether this addition causes topic drift.

        Args:
            addition: Pipeline addition to analyze

        Returns:
            DriftAnalysis with classification and recommendation
        """
        combined_text = (
            f"{addition.source} {addition.relevance} {addition.content}"
        ).lower()

        # Check for blocking/safety signals (HARD_BLOCKER)
        if self._has_blocking_signals(combined_text):
            return DriftAnalysis(
                classification=DriftClassification.HARD_BLOCKER,
                confidence=0.95,
                reasoning="Contains blocking/safety keywords (prerequisite, audit, compliance)",
                recommended_action="include",
            )

        # Check for architectural precedent signals (PRECEDENT)
        if self._has_precedent_signals(combined_text):
            return DriftAnalysis(
                classification=DriftClassification.ORDER_SUGGESTION,
                confidence=0.85,
                reasoning="Contains architectural/precedent keywords (ADR, pattern, practice)",
                recommended_action="flag",
            )

        # Check for same-family topic match
        if self._same_topic_family(combined_text):
            return DriftAnalysis(
                classification=DriftClassification.SAME_FAMILY,
                confidence=0.90,
                reasoning="Addition is in same topic family as original goal",
                recommended_action="include",
            )

        # Check for topic shift signals
        if self._has_shift_signals(combined_text):
            return DriftAnalysis(
                classification=DriftClassification.TOPIC_SHIFT,
                confidence=0.80,
                reasoning="Contains topic-shift keywords (instead, forget, redirect)",
                recommended_action="ask_user",
            )

        # Check for tangential signals
        if self._has_tangential_signals(combined_text):
            return DriftAnalysis(
                classification=DriftClassification.TANGENTIAL,
                confidence=0.75,
                reasoning="Addition is related but tangential to goal",
                recommended_action="skip",
            )

        # Default: same family (optimistic)
        return DriftAnalysis(
            classification=DriftClassification.SAME_FAMILY,
            confidence=0.60,
            reasoning="No drift signals detected; assuming same topic family",
            recommended_action="include",
        )

    def _has_blocking_signals(self, text: str) -> bool:
        """Check for blocking/safety signals."""
        return any(kw in text for kw in self.BLOCKING_KEYWORDS)

    def _has_precedent_signals(self, text: str) -> bool:
        """Check for architectural precedent signals."""
        return any(kw in text for kw in self.PRECEDENT_KEYWORDS)

    def _has_shift_signals(self, text: str) -> bool:
        """Check for topic shift signals."""
        return any(kw in text for kw in self.SHIFT_KEYWORDS)

    def _has_tangential_signals(self, text: str) -> bool:
        """Check for tangential signals."""
        return any(kw in text for kw in self.TANGENTIAL_KEYWORDS)

    def _same_topic_family(self, text: str) -> bool:
        """Check if text is in same topic family as goal.

        Simple heuristic: overlap of keywords between goal and text.
        """
        text_keywords = set(text.split())
        overlap = self.goal_keywords.intersection(text_keywords)

        # If >20% keyword overlap, likely same family
        overlap_pct = len(overlap) / max(len(self.goal_keywords), 1)
        return overlap_pct > 0.2


def create_topic_drift_detector(original_goal: str) -> TopicDriftDetector:
    """Factory to create a topic drift detector."""
    return TopicDriftDetector(original_goal)


def should_include_addition(
    original_goal: str,
    addition: PipelineAddition,
) -> bool:
    """Determine if an addition should be included based on drift analysis.

    Args:
        original_goal: User's original goal
        addition: Pipeline addition to check

    Returns:
        True if addition should be included, False if should skip/ask
    """
    detector = TopicDriftDetector(original_goal)
    analysis = detector.analyze_addition(addition)

    return analysis.recommended_action in ["include", "flag"]
