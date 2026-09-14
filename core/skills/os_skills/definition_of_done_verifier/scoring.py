"""ScoringEngine: Compute DoD score from check results."""

from typing import Dict, Optional


class ScoringEngine:
    """Compute weighted DoD score (0.0–1.0) from 5 checks."""

    DEFAULT_WEIGHTS = {
        "w_reach": 0.20,
        "w_audit": 0.25,
        "w_test": 0.20,
        "w_docs": 0.20,
        "w_repro": 0.15,
    }

    def __init__(self, weights: Optional[Dict[str, float]] = None):
        """Initialize with weights (default or custom)."""
        self.weights = weights or self.DEFAULT_WEIGHTS.copy()

    def compute_score(self, checks: Dict[str, bool]) -> float:
        """
        Compute score = Σ(weight_i × check_i) / Σ(weight_i)

        Args:
            checks: {check_name: passed(bool), ...}
                   e.g., {"reachability": True, "audit_trail": False, ...}

        Returns:
            float between 0.0 and 1.0
        """
        check_mapping = {
            "reachability": "w_reach",
            "audit_trail": "w_audit",
            "test_evidence": "w_test",
            "docs_sync": "w_docs",
            "reproducibility": "w_repro",
        }

        numerator = 0.0
        denominator = 0.0

        for check_name, weight_name in check_mapping.items():
            if check_name in checks:
                weight = self.weights.get(weight_name, 0.0)
                check_val = 1.0 if checks[check_name] else 0.0
                numerator += weight * check_val
                denominator += weight

        if denominator == 0:
            return 0.0

        return numerator / denominator

    def is_passed(self, score: float, threshold: float = 0.80) -> bool:
        """Determine if score meets threshold."""
        return score >= threshold

    def get_weights(self) -> Dict[str, float]:
        """Return current weights."""
        return self.weights.copy()
