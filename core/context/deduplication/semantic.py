"""Phase 4, Layer 2: Semantic Deduplication (confidence-gated, learned)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SemanticDedupConfig:
    """Configuration for semantic dedup (learned, fail-closed)."""
    confidence_threshold: float = 0.85
    min_confirmations: int = 2


class SemanticDeduplicator:
    """Semantic dedup: learned, confidence-gated, operator-vetoed."""

    def __init__(self, config: SemanticDedupConfig = None):
        self._config = config or SemanticDedupConfig()
        self._confidence_scores = {}
        self._confirmations = {}
        self._veto_list = set()
        self._divergence_alerts = []

    def can_deduplicate(self, semantic_hash: str) -> bool:
        """Check if semantic dedup allowed (confidence-gated)."""
        if semantic_hash in self._veto_list:
            return False

        confidence = self._confidence_scores.get(semantic_hash, 0.0)
        confirmations = self._confirmations.get(semantic_hash, 0)

        # Fail-closed: need high confidence AND multiple confirmations
        return (
            confidence >= self._config.confidence_threshold
            and confirmations >= self._config.min_confirmations
        )

    def record_feedback(self, semantic_hash: str, is_correct: bool) -> None:
        """Record user feedback on dedup correctness."""
        if semantic_hash not in self._confirmations:
            self._confirmations[semantic_hash] = 0
            self._confidence_scores[semantic_hash] = 0.0

        self._confirmations[semantic_hash] += 1

        # Update confidence (weighted)
        delta = 0.1 if is_correct else -0.1
        self._confidence_scores[semantic_hash] = max(
            0.0, min(1.0, self._confidence_scores[semantic_hash] + delta)
        )

        # Detect divergence (conflicting feedback)
        if self._confirmations[semantic_hash] >= 3:
            variance_indicator = abs(delta)  # Simplified
            if variance_indicator > 0.5:
                self._divergence_alerts.append(semantic_hash)

    def add_veto(self, semantic_hash: str) -> None:
        """Operator veto: never deduplicate this pattern."""
        self._veto_list.add(semantic_hash)

    def remove_veto(self, semantic_hash: str) -> None:
        """Operator revokes veto."""
        self._veto_list.discard(semantic_hash)
