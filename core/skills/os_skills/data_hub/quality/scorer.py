"""Quality scoring — deterministic, reproducible metrics."""

import math
from typing import Dict, Any


class QualityScorer:
    """Compute quality scores deterministically."""

    def __init__(self):
        self.weights = {
            "relevance": 0.4,
            "freshness": 0.3,
            "coverage": 0.2,
            "completeness": 0.1,
        }

    def compute_relevance(
        self, embeddings_distance: float, use_case_description: str
    ) -> float:
        """
        Relevance: how well does this document match the use case?
        Embeddings distance (cosine): 0.0 = identical, 1.0 = orthogonal
        Returns: 0–1 where 1 = perfect match
        """
        # Assume embeddings distance is 0–1
        # Convert distance to score: closer = higher score
        if not (0 <= embeddings_distance <= 1):
            return 0.5  # neutral if unknown
        return 1.0 - embeddings_distance

    def compute_freshness(self, age_hours: int) -> float:
        """
        Freshness: how recent is the data?
        Exponential decay: fresh (24h) = 0.9, old (7d) = 0.3, very old (30d) = 0.05
        Returns: 0–1 where 1 = perfectly fresh
        """
        # Exponential decay: score = exp(-age / half_life)
        # half_life = 48 hours (48h → 0.5 score)
        half_life = 48.0
        score = math.exp(-age_hours / half_life)
        return min(1.0, max(0.0, score))  # clamp to [0, 1]

    def compute_coverage(
        self, document_count: int, expected_count: int
    ) -> float:
        """
        Coverage: how complete is the data set?
        Returns: min(1.0, actual / expected)
        """
        if expected_count == 0:
            return 1.0  # unknown expectation = full coverage
        score = document_count / expected_count
        return min(1.0, max(0.0, score))

    def compute_completeness(
        self, has_metadata: bool, has_examples: bool, placeholder_count: int
    ) -> float:
        """
        Completeness: is the data well-structured?
        Returns: 0–1 based on metadata, examples, and absence of placeholders
        """
        score = 1.0
        if not has_metadata:
            score -= 0.3
        if not has_examples:
            score -= 0.2
        if placeholder_count > 0:
            score -= min(0.5, placeholder_count * 0.05)  # -5% per placeholder
        return max(0.0, score)

    def compute_document_quality(
        self,
        relevance: float,
        freshness: float,
        coverage: float,
        completeness: float,
    ) -> float:
        """
        Weighted sum of all dimensions.
        All inputs should be in [0, 1].
        """
        # Validate inputs
        for val in [relevance, freshness, coverage, completeness]:
            if not (0 <= val <= 1):
                raise ValueError(f"Score out of bounds: {val}")

        score = (
            self.weights["relevance"] * relevance
            + self.weights["freshness"] * freshness
            + self.weights["coverage"] * coverage
            + self.weights["completeness"] * completeness
        )
        return min(1.0, max(0.0, score))  # ensure [0, 1]

    def compute_manifest_quality(self, document_scores: list) -> float:
        """Average quality across all documents."""
        if not document_scores:
            return 0.5  # neutral if no documents
        return sum(document_scores) / len(document_scores)
