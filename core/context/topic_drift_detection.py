"""Topic Drift Detection — k=3 of Context-Pipeline v2 LDD.

Detects unexpected topic shifts before injecting context into system prompt.
Classifies additions as same-family, prerequisite, tangential, or topic-shift.

ADR-0399: Context-Pipeline v2
k=3: Topic Drift Detection (target: 95%+ accuracy, <10% false positives)
"""

import logging
import re
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

    Matching is on WORD TOKENS (punctuation stripped), never substrings: the
    previous ``kw in text`` matched "arch" inside "architecture" and "the"
    inside "other", and ``"the"`` / ``"by"`` / ``"way"`` were tangential
    keywords on their own — almost any sentence was "tangential". Multi-word
    signals ("by the way") are matched as phrases.

    Precedence (first hit wins) — the order encodes the k=3 accuracy contract
    (``tests/unit/test_context_pipeline_k3_drift.py``):

    1. TOPIC_SHIFT   — explicit redirect language ("instead", "forget", ...)
                       dominates: "forget X, rebuild the architecture" is a
                       shift even though "architecture" is a precedent word.
    2. SAME_FAMILY   — >20% keyword overlap with the goal: an addition ABOUT
                       the goal is on-topic even when it mentions "access" or
                       "best practice".
    3. HARD_BLOCKER  — safety/audit/compliance prerequisites (include).
    4. TANGENTIAL    — "by the way", "also", "related" (skip).
    5. ORDER_SUGGESTION — ADR/pattern/precedent (flag).
    6. default       — SAME_FAMILY at low confidence (include).
    Both HARD_BLOCKER and SAME_FAMILY recommend "include", so ranking overlap
    above blocking never drops a safety note; it only labels it correctly.
    """

    # Keywords that indicate blocking/safety concerns
    BLOCKING_KEYWORDS = {
        "prerequisite", "requires", "required", "must", "blocking", "audit",
        "safety", "compliance", "constraint", "critical", "fail-closed",
        "verify", "verification", "validate", "validation", "permission",
        "access", "security", "protection",
    }

    # Keywords that indicate architectural guidance (precedent)
    PRECEDENT_KEYWORDS = {
        "adr", "pattern", "practice", "practices", "convention", "standard",
        "approach", "design", "architecture", "best", "follows", "follow",
        "consistent", "aligned", "precedent",
    }

    # Keywords/phrases that indicate tangential/optional info
    TANGENTIAL_KEYWORDS = {
        "also", "related", "meanwhile", "consider", "might", "could",
        "optional", "alternative", "aside", "tangent", "info",
    }
    TANGENTIAL_PHRASES = ("by the way", "as an aside", "side note")

    # Keywords that indicate topic shift (different area)
    SHIFT_KEYWORDS = {
        "instead", "rather", "forget", "skip", "ignore", "abandon", "redirect",
        "switch", "separate", "unrelated", "drop",
    }

    def __init__(self, original_goal: str):
        """Initialize detector with original goal.

        Args:
            original_goal: User's stated goal (immutable)
        """
        self.original_goal = original_goal.lower()
        self.goal_keywords = self._tokens(self.original_goal)

    @staticmethod
    def _tokens(text: str) -> set:
        """Word tokens: lowercase, punctuation stripped (hyphens kept)."""
        return {
            t for t in re.findall(r"[a-z0-9][a-z0-9\-']*", text.lower())
            if t
        }

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
        tokens = self._tokens(combined_text)

        # 1. Explicit topic-shift language dominates everything else.
        if self._has_shift_signals(tokens):
            return DriftAnalysis(
                classification=DriftClassification.TOPIC_SHIFT,
                confidence=0.80,
                reasoning="Contains topic-shift keywords (instead, forget, redirect)",
                recommended_action="ask_user",
            )

        # 2. On-topic by keyword overlap with the goal.
        if self._same_topic_family(tokens):
            return DriftAnalysis(
                classification=DriftClassification.SAME_FAMILY,
                confidence=0.90,
                reasoning="Addition is in same topic family as original goal",
                recommended_action="include",
            )

        # 3. Blocking/safety prerequisites.
        if self._has_blocking_signals(tokens):
            return DriftAnalysis(
                classification=DriftClassification.HARD_BLOCKER,
                confidence=0.95,
                reasoning="Contains blocking/safety keywords (prerequisite, audit, compliance)",
                recommended_action="include",
            )

        # 4. Tangential asides.
        if self._has_tangential_signals(tokens, combined_text):
            return DriftAnalysis(
                classification=DriftClassification.TANGENTIAL,
                confidence=0.75,
                reasoning="Addition is related but tangential to goal",
                recommended_action="skip",
            )

        # 5. Architectural precedent.
        if self._has_precedent_signals(tokens):
            return DriftAnalysis(
                classification=DriftClassification.ORDER_SUGGESTION,
                confidence=0.85,
                reasoning="Contains architectural/precedent keywords (ADR, pattern, practice)",
                recommended_action="flag",
            )

        # Default: same family (optimistic)
        return DriftAnalysis(
            classification=DriftClassification.SAME_FAMILY,
            confidence=0.60,
            reasoning="No drift signals detected; assuming same topic family",
            recommended_action="include",
        )

    def _has_blocking_signals(self, tokens: set) -> bool:
        """Check for blocking/safety signals."""
        return bool(tokens & self.BLOCKING_KEYWORDS)

    def _has_precedent_signals(self, tokens: set) -> bool:
        """Check for architectural precedent signals."""
        return bool(tokens & self.PRECEDENT_KEYWORDS)

    def _has_shift_signals(self, tokens: set) -> bool:
        """Check for topic shift signals."""
        return bool(tokens & self.SHIFT_KEYWORDS)

    def _has_tangential_signals(self, tokens: set, text: str) -> bool:
        """Check for tangential signals (single words or phrases)."""
        if tokens & self.TANGENTIAL_KEYWORDS:
            return True
        return any(phrase in text for phrase in self.TANGENTIAL_PHRASES)

    def _same_topic_family(self, tokens: set) -> bool:
        """Check if text is in same topic family as goal.

        Simple heuristic: overlap of keywords between goal and text.
        """
        overlap = self.goal_keywords.intersection(tokens)

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
