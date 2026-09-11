"""Quality scoring — deterministic, reproducible metrics."""

import math
from typing import Dict, Any, Tuple
from dataclasses import dataclass


@dataclass
class QualityBreakdown:
    """Detailed quality score breakdown for explainability."""
    relevance: float
    freshness: float
    coverage: float
    authority: float
    uniqueness: float
    completeness: float
    composite_score: float
    reasoning: Dict[str, str]  # Human-readable explanations


class QualityScorer:
    """Compute quality scores deterministically."""

    # Global weight configuration (tunable by operator)
    DEFAULT_WEIGHTS = {
        "relevance": 0.35,
        "freshness": 0.25,
        "coverage": 0.15,
        "authority": 0.10,
        "uniqueness": 0.10,
        "completeness": 0.05,
    }

    # Context-aware weight overrides
    CONTEXT_WEIGHTS = {
        "skill_generation": {
            "relevance": 0.40,  # Higher relevance for skill context
            "freshness": 0.20,
            "coverage": 0.15,
            "authority": 0.10,
            "uniqueness": 0.10,
            "completeness": 0.05,
        },
        "tool_generation": {
            "relevance": 0.35,
            "freshness": 0.30,  # Higher freshness for tool docs
            "coverage": 0.15,
            "authority": 0.10,
            "uniqueness": 0.05,
            "completeness": 0.05,
        },
        "context_extraction": {
            "relevance": 0.30,
            "freshness": 0.25,
            "coverage": 0.20,  # Higher coverage for context
            "authority": 0.10,
            "uniqueness": 0.10,
            "completeness": 0.05,
        },
    }

    # Source authority tiers (higher = more trusted)
    SOURCE_AUTHORITY = {
        # Memory sources
        "memory:tier1": 0.95,
        "memory:tier2": 0.85,
        "memory:tier3": 0.70,
        # RAG sources
        "rag": 0.80,
        "rag:embeddings": 0.80,
        "rag:bm25": 0.75,
        # MCP sources
        "mcp": 0.75,
        # File sources
        "files": 0.65,
        "files:docs": 0.75,
        "files:internal": 0.80,
        # Unknown sources
        "unknown": 0.50,
    }

    def __init__(self, weights: Dict[str, float] = None):
        self.weights = weights or self.DEFAULT_WEIGHTS.copy()
        # Validate weights sum to 1.0
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.001:
            raise ValueError(f"Weights must sum to 1.0, got {total}")
        self.source_reputation = {}  # Cache source authority over time

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

    def compute_authority(self, source: str) -> float:
        """
        Authority scoring: source trust weighting.
        Returns: 0–1 where 1 = maximum trust
        """
        # Check exact match first
        if source in self.SOURCE_AUTHORITY:
            return self.SOURCE_AUTHORITY[source]

        # Check prefix match (e.g., "memory:..." → use "memory:" tier)
        for prefix, authority in self.SOURCE_AUTHORITY.items():
            if source.startswith(prefix + ":"):
                return authority

        # Default to unknown authority
        return self.SOURCE_AUTHORITY.get("unknown", 0.50)

    def compute_uniqueness(
        self, content_hash: str, seen_hashes: Dict[str, int]
    ) -> float:
        """
        Uniqueness scoring: deduplication impact.
        Returns: 0.0 if duplicate, 1.0 if unique, [0, 1) for near-duplicates
        """
        if content_hash in seen_hashes:
            # Exact duplicate: 0.0
            return 0.0

        # Check for near-duplicates (Hamming distance on hash)
        # For simplicity, we'll just count exact duplicates in this version
        return 1.0

    def compute_document_quality_with_breakdown(
        self,
        relevance: float,
        freshness: float,
        coverage: float,
        authority: float,
        uniqueness: float,
        completeness: float,
        use_case_hint: str = "context_extraction",
    ) -> Tuple[float, QualityBreakdown]:
        """
        Weighted sum of all dimensions with explainability.
        Returns: (composite_score, QualityBreakdown)
        """
        # Validate inputs
        for val in [relevance, freshness, coverage, authority, uniqueness, completeness]:
            if not (0 <= val <= 1):
                raise ValueError(f"Score out of bounds: {val}")

        # Select weights based on use case
        weights = self.CONTEXT_WEIGHTS.get(use_case_hint, self.weights)

        # Compute weighted score
        score = (
            weights["relevance"] * relevance
            + weights["freshness"] * freshness
            + weights["coverage"] * coverage
            + weights["authority"] * authority
            + weights["uniqueness"] * uniqueness
            + weights["completeness"] * completeness
        )
        score = min(1.0, max(0.0, score))

        # Generate reasoning
        reasoning = {
            "relevance": f"Match quality: {relevance:.2f} (weight: {weights['relevance']:.1%})",
            "freshness": f"Recency: {freshness:.2f} (weight: {weights['freshness']:.1%})",
            "coverage": f"Data coverage: {coverage:.2f} (weight: {weights['coverage']:.1%})",
            "authority": f"Source trust: {authority:.2f} (weight: {weights['authority']:.1%})",
            "uniqueness": f"Deduplication: {uniqueness:.2f} (weight: {weights['uniqueness']:.1%})",
            "completeness": f"Structure: {completeness:.2f} (weight: {weights['completeness']:.1%})",
        }

        breakdown = QualityBreakdown(
            relevance=relevance,
            freshness=freshness,
            coverage=coverage,
            authority=authority,
            uniqueness=uniqueness,
            completeness=completeness,
            composite_score=score,
            reasoning=reasoning,
        )

        return score, breakdown

    def compute_document_quality(
        self,
        relevance: float,
        freshness: float,
        coverage: float,
        completeness: float,
    ) -> float:
        """
        Legacy method: weighted sum of 4 dimensions (backward compatible).
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
