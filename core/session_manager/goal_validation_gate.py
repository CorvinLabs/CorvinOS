"""GoalAlignmentValidator: Prevents context reduction from erasing the original goal.

Phase 2 of Task Context Drift Prevention System (ADR-0404, ADR-0407).

Validates that reduced context still covers the original goal semantically:
- Semantic similarity: TF-IDF based (0.0-1.0)
- Goal completeness: Keyword coverage (0.0-1.0)
- Composite score: (similarity * 0.7) + (completeness * 0.3)
- Threshold: 0.65 (fail-closed: score < 0.65 → use FULL context)

GDPR Art. 30, 32: Every validation event logged with score + decision (never goal text).
"""

import logging
import hashlib
from dataclasses import dataclass
from typing import Optional, Dict, Set, List
from collections import Counter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidationResult:
    """Result of goal alignment validation.

    Attributes:
        is_valid: True if reduced context preserves goal sufficiently
        semantic_similarity_score: TF-IDF similarity (0.0-1.0)
        completeness_score: Keyword coverage (0.0-1.0)
        composite_score: (similarity * 0.7) + (completeness * 0.3)
        threshold: Score threshold (default 0.65)
        reason: Human-readable explanation of result
        goal_hash: SHA256 hash of goal (GDPR Art. 32: never log goal text)
    """

    is_valid: bool
    semantic_similarity_score: float
    completeness_score: float
    composite_score: float
    threshold: float
    reason: str
    goal_hash: str  # Never log goal text; use hash for audit trail

    def to_audit_event(self) -> dict:
        """Convert to audit.jsonl format (GDPR Art. 30, 32).

        Returns:
            Dictionary with validation scores + decision (NO goal text)
        """
        return {
            "event_type": "context_reduction_validated",
            "is_valid": self.is_valid,
            "semantic_similarity_score": round(self.semantic_similarity_score, 4),
            "completeness_score": round(self.completeness_score, 4),
            "composite_score": round(self.composite_score, 4),
            "threshold": self.threshold,
            "goal_hash": self.goal_hash,
            "decision": "USE_FULL_CONTEXT" if not self.is_valid else "USE_REDUCED_CONTEXT",
        }


class GoalAlignmentValidator:
    """Validates goal alignment before and after context reduction.

    Uses semantic similarity (TF-IDF) and keyword completeness to detect
    when context reduction erases critical goal information.

    Fail-closed design: If validation fails, returns full context unchanged.
    """

    # Threshold: score must be ≥0.65 for reduction to be safe
    DEFAULT_THRESHOLD = 0.65

    # Performance target: <5ms per validation
    # TF-IDF cache helps achieve this
    _tf_idf_cache: Dict[str, Dict[str, float]] = {}

    def __init__(self, threshold: float = DEFAULT_THRESHOLD):
        """Initialize GoalAlignmentValidator.

        Args:
            threshold: Minimum composite score for safe reduction (0.0-1.0)
                Default: 0.65 (fail-closed, prefer full context if uncertain)
        """
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"Threshold must be 0.0-1.0, got {threshold}")
        self.threshold = threshold
        self.name = "goal_alignment_validator"
        self.version = "0.1.0"

    def validate_reduction(
        self, original_goal: str, reduced_context: str
    ) -> ValidationResult:
        """Validate that reduced context preserves goal.

        Args:
            original_goal: Original task goal (required)
            reduced_context: Reduced context to validate

        Returns:
            ValidationResult with composite score and decision

        Raises:
            ValueError: If goal is empty or context is invalid
        """
        if not isinstance(original_goal, str) or not original_goal.strip():
            raise ValueError("Goal must be non-empty string")
        if not isinstance(reduced_context, str):
            raise ValueError("Context must be string")

        # Compute semantic similarity (TF-IDF)
        similarity = self._semantic_similarity(original_goal, reduced_context)

        # Compute goal completeness (keyword coverage)
        completeness = self._goal_completeness(original_goal, reduced_context)

        # Composite score: similarity * 0.7 + completeness * 0.3
        composite = (similarity * 0.7) + (completeness * 0.3)

        # Determine validity
        is_valid = composite >= self.threshold

        # Create hash of goal for audit trail (GDPR Art. 32)
        goal_hash = hashlib.sha256(original_goal.encode("utf-8")).hexdigest()

        # Reason
        if is_valid:
            reason = (
                f"Composite score {composite:.2f} ≥ threshold {self.threshold}; "
                f"reduced context preserves goal sufficiently"
            )
        else:
            reason = (
                f"Composite score {composite:.2f} < threshold {self.threshold}; "
                f"reduced context may lose goal; using FULL context (fail-closed)"
            )

        result = ValidationResult(
            is_valid=is_valid,
            semantic_similarity_score=similarity,
            completeness_score=completeness,
            composite_score=composite,
            threshold=self.threshold,
            reason=reason,
            goal_hash=goal_hash,
        )

        # Log validation (GDPR Art. 30)
        logger.info(f"Goal alignment validation: {reason}")

        return result

    def _semantic_similarity(self, goal: str, context: str) -> float:
        """Compute TF-IDF semantic similarity between goal and context.

        Uses simplified TF-IDF approach:
        1. Tokenize goal and context
        2. Compute term frequency in each
        3. Calculate cosine similarity of TF vectors
        4. Return score (0.0-1.0), capped to avoid edge cases

        Args:
            goal: Original goal text
            context: Reduced context text

        Returns:
            Similarity score (0.0-1.0), higher = more similar
        """
        goal_tokens = self._tokenize(goal)
        context_tokens = self._tokenize(context)

        if not goal_tokens or not context_tokens:
            return 0.0

        # Compute TF vectors (using cache for performance)
        goal_key = hashlib.md5(goal.encode()).hexdigest()
        context_key = hashlib.md5(context.encode()).hexdigest()

        if goal_key not in self._tf_idf_cache:
            self._tf_idf_cache[goal_key] = self._tf_vector(goal_tokens)
        if context_key not in self._tf_idf_cache:
            self._tf_idf_cache[context_key] = self._tf_vector(context_tokens)

        goal_tf = self._tf_idf_cache[goal_key]
        context_tf = self._tf_idf_cache[context_key]

        # Compute cosine similarity
        similarity = self._cosine_similarity(goal_tf, context_tf)

        # Clamp to [0.0, 1.0]
        return min(1.0, max(0.0, similarity))

    def _goal_completeness(self, goal: str, context: str) -> float:
        """Compute goal completeness as keyword coverage in reduced context.

        Algorithm:
        1. Extract goal keywords (non-stop-words, ≥3 chars)
        2. Count how many appear in context
        3. Return coverage score (0.0-1.0)

        Example:
        - Goal: "Implement plugin system with security isolation"
        - Keywords: ["implement", "plugin", "system", "security", "isolation"]
        - If all 5 found in context: completeness = 1.0
        - If 3/5 found: completeness = 0.6
        - If 1/5 found: completeness = 0.2

        Args:
            goal: Original goal text
            context: Reduced context text

        Returns:
            Completeness score (0.0-1.0), higher = more keywords preserved
        """
        goal_tokens = self._tokenize(goal)

        if not goal_tokens:
            return 0.0

        # Extract keywords: non-stop-words, ≥3 chars
        keywords = self._extract_keywords(goal_tokens)

        if not keywords:
            # No keywords extracted (all were stop-words)
            return 1.0

        # Count how many keywords appear in context
        context_lower = context.lower()
        found_count = sum(1 for kw in keywords if kw in context_lower)

        # Completeness score
        completeness = found_count / len(keywords) if keywords else 0.0

        # Clamp to [0.0, 1.0]
        return min(1.0, max(0.0, completeness))

    def _tokenize(self, text: str) -> List[str]:
        """Tokenize text into lowercase words (heuristic, no NLP library).

        Args:
            text: Text to tokenize

        Returns:
            List of lowercase tokens
        """
        if not text:
            return []

        # Simple tokenization: lowercase, split on non-alphanumeric
        import re

        tokens = re.findall(r"\b\w+\b", text.lower())
        return tokens

    def _extract_keywords(self, tokens: List[str]) -> Set[str]:
        """Extract keywords: non-stop-words, ≥3 chars.

        Args:
            tokens: List of tokens

        Returns:
            Set of keywords (unique, non-stop-word, ≥3 chars)
        """
        stop_words = {
            "the",
            "a",
            "an",
            "and",
            "or",
            "but",
            "in",
            "on",
            "at",
            "to",
            "for",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "can",
            "this",
            "that",
            "these",
            "those",
            "it",
            "its",
            "from",
            "with",
            "by",
            "as",
            "if",
            "of",
            "into",
            "through",
            "during",
            "before",
            "after",
            "above",
            "below",
            "over",
            "under",
        }

        keywords = {
            token for token in tokens if token not in stop_words and len(token) >= 3
        }

        return keywords

    def _tf_vector(self, tokens: List[str]) -> Dict[str, float]:
        """Compute term frequency (TF) vector from tokens.

        TF(term) = count(term) / total_tokens

        Args:
            tokens: List of tokens

        Returns:
            Dict mapping token → TF value (0.0-1.0)
        """
        if not tokens:
            return {}

        token_counts = Counter(tokens)
        total = len(tokens)

        tf_vector = {token: count / total for token, count in token_counts.items()}

        return tf_vector

    def _cosine_similarity(
        self, vec1: Dict[str, float], vec2: Dict[str, float]
    ) -> float:
        """Compute cosine similarity between two TF vectors.

        Formula:
            similarity = dot_product(vec1, vec2) / (norm(vec1) * norm(vec2))

        Args:
            vec1: First TF vector
            vec2: Second TF vector

        Returns:
            Cosine similarity (0.0-1.0)
        """
        if not vec1 or not vec2:
            return 0.0

        # Compute dot product of common terms
        dot_product = sum(
            vec1.get(term, 0.0) * vec2.get(term, 0.0)
            for term in set(vec1.keys()) & set(vec2.keys())
        )

        # Compute norms
        norm1 = (sum(v**2 for v in vec1.values()) ** 0.5) if vec1 else 0.0
        norm2 = (sum(v**2 for v in vec2.values()) ** 0.5) if vec2 else 0.0

        if norm1 == 0.0 or norm2 == 0.0:
            return 0.0

        similarity = dot_product / (norm1 * norm2)

        return similarity

    def clear_cache(self):
        """Clear TF-IDF cache to free memory.

        Useful between validation runs to avoid unbounded cache growth.
        """
        self._tf_idf_cache.clear()
        logger.debug("TF-IDF cache cleared")
