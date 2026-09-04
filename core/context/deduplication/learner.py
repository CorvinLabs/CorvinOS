"""Phase 4: Semantic Dedup Learning (ADR-0314 integration)."""

from typing import Dict, Any


class SemanticDedupLearner:
    """Learn from feedback to improve semantic dedup (fail-closed)."""

    def __init__(self):
        self._feedback_history: Dict[str, list] = {}
        self._learned_dedups: Dict[str, float] = {}

    def process_feedback(self, semantic_hash: str, feedback: Dict[str, Any]) -> None:
        """Process user feedback on dedup correctness."""
        if semantic_hash not in self._feedback_history:
            self._feedback_history[semantic_hash] = []

        self._feedback_history[semantic_hash].append(feedback)

        # Only learn after 2+ confirmations (fail-closed)
        if len(self._feedback_history[semantic_hash]) >= 2:
            # Compute quality score (task success = high quality)
            quality_scores = [
                1.0 if f.get('task_outcome') == 'success' else 0.1
                for f in self._feedback_history[semantic_hash]
            ]
            avg_quality = sum(quality_scores) / len(quality_scores)

            if avg_quality >= 0.5:  # Good quality
                self._learned_dedups[semantic_hash] = avg_quality

    def get_dedup_score(self, semantic_hash: str) -> float:
        """Get learned dedup score (0.0–1.0)."""
        return self._learned_dedups.get(semantic_hash, 0.0)

    def detect_divergence(self, semantic_hash: str) -> bool:
        """Detect conflicting feedback (signal divergence)."""
        if semantic_hash not in self._feedback_history:
            return False

        feedback = self._feedback_history[semantic_hash]
        if len(feedback) < 3:
            return False

        # Simple divergence: outcomes wildly differ
        outcomes = [f.get('task_outcome') for f in feedback]
        success_count = sum(1 for o in outcomes if o == 'success')
        failure_count = len(outcomes) - success_count

        return success_count > 0 and failure_count > 0
