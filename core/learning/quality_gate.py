"""L5 k=3: Quality Gate — Advisory Reliability Scoring for Learned Proposals.

ADR-0580: Quality Gate (Confidence Scoring & Reliability Validation)
Computes four reliability metrics and collapses them into a single composite score.
Gate is ADVISORY ONLY (does not block); operator sees score when making approval.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum
import logging
import math
import threading

logger = logging.getLogger(__name__)


class QualityLevel(str, Enum):
    """Quality level classification based on composite score."""
    EXCELLENT = "excellent"  # score >= 0.85
    GOOD = "good"           # score >= 0.70
    FAIR = "fair"           # score >= 0.55
    POOR = "poor"           # score < 0.55


@dataclass
class QualityMetrics:
    """Four reliability metrics for a learned proposal."""
    skill_id: str
    metric_name: str
    overfitting_risk: float    # [0.0=safe, 1.0=severe]
    noise_ratio: float         # [0.0=clean, 1.0=pure noise]
    convergence_rate: float    # [0.0=diverging, 1.0=converged]
    stability_score: float     # [0.0=unstable, 1.0=stable]
    timestamp: str             # ISO 8601
    confidence: float = 0.0    # Operator-facing confidence [0.0, 1.0]


@dataclass
class QualityScore:
    """Composite reliability score for a proposal."""
    skill_id: str
    metric_name: str
    quality_metrics: QualityMetrics
    composite_score: float     # [0.0, 1.0]
    quality_level: QualityLevel
    recommendation: str        # e.g., "Monitor closely" or "Safe to apply"
    timestamp: str             # ISO 8601


class QualityGate:
    """L5 k=3: Advisory quality scoring for learned proposals.

    Computes four metrics (overfitting, noise, convergence, stability),
    collapses them via PCA-weighted average into a single composite score.

    ADVISORY ONLY: Does not block approval. Operator sees score and decides.
    """

    # PCA-derived weights (from dialectical reasoning)
    WEIGHTS = {
        "overfitting_risk": 0.4,
        "noise_ratio": 0.3,
        "convergence_rate": 0.2,
        "stability_score": 0.1,
    }

    def __init__(
        self,
        tenant_id: str = "_default",
        audit_backend=None,
    ):
        """Initialize quality gate.

        Args:
            tenant_id: Tenant for isolation
            audit_backend: Audit backend (required for logging)
        """
        self.tenant_id = tenant_id
        self.audit_backend = audit_backend

        # Thread safety for score mutations
        self._lock = threading.RLock()

        # State: (tenant_id, skill_id, metric_name) -> QualityScore (tenant-scoped)
        self.scores: Dict[str, QualityScore] = {}

    def compute_quality(
        self,
        skill_id: str,
        metric_name: str,
        recent_deltas: List[float],
        ema_smoothed: float,
        ema_confidence: float,
        config_history: List[float],
    ) -> QualityScore:
        """Compute quality score for a learned proposal.

        Args:
            skill_id: Skill being evaluated
            metric_name: Metric being tuned
            recent_deltas: Last N feedback deltas (for noise/convergence analysis)
            ema_smoothed: EMA-smoothed delta (from k=1 gate)
            ema_confidence: EMA confidence [0.0, 1.0]
            config_history: Recent config values (for stability analysis)

        Returns:
            QualityScore with composite score + recommendation
        """
        import datetime

        if len(recent_deltas) == 0:
            # No data — score is neutral
            return QualityScore(
                skill_id=skill_id,
                metric_name=metric_name,
                quality_metrics=QualityMetrics(
                    skill_id=skill_id,
                    metric_name=metric_name,
                    overfitting_risk=0.5,
                    noise_ratio=0.5,
                    convergence_rate=0.5,
                    stability_score=0.5,
                    timestamp=datetime.datetime.utcnow().isoformat() + "Z",
                    confidence=0.0,
                ),
                composite_score=0.5,
                quality_level=QualityLevel.FAIR,
                recommendation="Insufficient data for quality assessment",
                timestamp=datetime.datetime.utcnow().isoformat() + "Z",
            )

        # Compute four metrics
        overfitting_risk = self._compute_overfitting_risk(
            recent_deltas, ema_smoothed, ema_confidence
        )
        noise_ratio = self._compute_noise_ratio(recent_deltas)
        convergence_rate = self._compute_convergence_rate(recent_deltas)
        stability_score = self._compute_stability_score(config_history)

        # Create metrics object
        metrics = QualityMetrics(
            skill_id=skill_id,
            metric_name=metric_name,
            overfitting_risk=overfitting_risk,
            noise_ratio=noise_ratio,
            convergence_rate=convergence_rate,
            stability_score=stability_score,
            timestamp=datetime.datetime.utcnow().isoformat() + "Z",
            confidence=ema_confidence,
        )

        # Collapse to composite score via weighted average
        # Higher metrics are better EXCEPT overfitting_risk and noise_ratio (inverted)
        composite = (
            self.WEIGHTS["overfitting_risk"] * (1.0 - overfitting_risk)
            + self.WEIGHTS["noise_ratio"] * (1.0 - noise_ratio)
            + self.WEIGHTS["convergence_rate"] * convergence_rate
            + self.WEIGHTS["stability_score"] * stability_score
        )

        # Clamp to [0.0, 1.0]
        composite = max(0.0, min(1.0, composite))

        # Classify quality level
        if composite >= 0.85:
            quality_level = QualityLevel.EXCELLENT
            recommendation = "Safe to apply; high confidence"
        elif composite >= 0.70:
            quality_level = QualityLevel.GOOD
            recommendation = "Recommended; meets quality threshold"
        elif composite >= 0.55:
            quality_level = QualityLevel.FAIR
            recommendation = "Monitor closely; apply with caution"
        else:
            quality_level = QualityLevel.POOR
            recommendation = "Low quality; consider rejecting"

        score = QualityScore(
            skill_id=skill_id,
            metric_name=metric_name,
            quality_metrics=metrics,
            composite_score=composite,
            quality_level=quality_level,
            recommendation=recommendation,
            timestamp=datetime.datetime.utcnow().isoformat() + "Z",
        )

        # Audit-first: log the score before storing
        if self.audit_backend:
            try:
                audit_event = {
                    "tenant_id": self.tenant_id,
                    "event_type": "learning_quality_score_computed",
                    "skill_id": skill_id,
                    "metric_name": metric_name,
                    "composite_score": composite,
                    "quality_level": quality_level.value,
                    "overfitting_risk": overfitting_risk,
                    "noise_ratio": noise_ratio,
                    "convergence_rate": convergence_rate,
                    "stability_score": stability_score,
                }
                self.audit_backend.write_event(audit_event)
            except Exception as e:
                logger.error(f"[L5 Quality] FATAL: audit_backend.write_event() failed: {e}")
                raise RuntimeError(
                    f"[L5 Quality] FATAL: audit_backend.write_event() failed: {e}. "
                    f"State mutation BLOCKED (fail-closed constraint C5)."
                )

        # Store score (after successful audit), with tenant-scoped key and thread safety
        with self._lock:
            score_key = f"{self.tenant_id}:{skill_id}:{metric_name}"
            self.scores[score_key] = score

        logger.info(
            f"[L5 Quality] {skill_id}.{metric_name}: "
            f"composite_score={composite:.2f} ({quality_level.value})"
        )

        return score

    def _compute_overfitting_risk(
        self,
        recent_deltas: List[float],
        ema_smoothed: float,
        ema_confidence: float,
    ) -> float:
        """Compute overfitting risk: divergence between raw deltas and EMA.

        High overfitting when recent deltas diverge from EMA despite high
        reported confidence (suggests the model is fitting noise, not signal).

        Returns:
            [0.0=safe, 1.0=severe overfitting]
        """
        if ema_confidence < 0.1:
            # Very low confidence — can't assess overfitting
            return 0.5

        if len(recent_deltas) < 2:
            # Need ≥2 deltas to assess divergence
            return 0.0

        # Compute divergence between recent deltas and EMA
        divergence = sum(abs(d - ema_smoothed) for d in recent_deltas) / len(
            recent_deltas
        )

        # Normalize by confidence: high confidence should correlate with low divergence
        # If divergence is high despite high confidence, that's overfitting
        overfitting_risk = min(1.0, divergence / (ema_confidence + 0.1))

        return max(0.0, min(1.0, overfitting_risk))

    def _compute_noise_ratio(self, recent_deltas: List[float]) -> float:
        """Estimate what fraction of feedback is random noise.

        If many high-delta events appear singly (not confirmed by history),
        that suggests noise dominates.

        Returns:
            [0.0=clean, 1.0=pure noise]
        """
        if len(recent_deltas) == 0:
            return 0.5

        # Heuristic: count single-high-deltas (isolated outliers)
        # If ≥ 50% of deltas are isolated outliers, noise dominates
        threshold = max(abs(d) for d in recent_deltas) * 0.66
        outlier_count = sum(1 for d in recent_deltas if abs(d) > threshold)

        noise_ratio = min(1.0, outlier_count / max(1, len(recent_deltas)))

        return max(0.0, min(1.0, noise_ratio))

    def _compute_convergence_rate(self, recent_deltas: List[float]) -> float:
        """Measure stability of convergence: recent deltas stabilizing?

        High convergence when recent deltas have low variance (stabilizing).

        Returns:
            [0.0=diverging, 1.0=converged]
        """
        if len(recent_deltas) < 2:
            return 0.5

        # Compute std of recent deltas
        mean_delta = sum(recent_deltas) / len(recent_deltas)
        variance = sum((d - mean_delta) ** 2 for d in recent_deltas) / len(
            recent_deltas
        )
        std = math.sqrt(variance)

        # Compute mean absolute delta
        mean_abs_delta = sum(abs(d) for d in recent_deltas) / len(recent_deltas)

        if mean_abs_delta < 0.01:
            # Deltas are very small — essentially converged
            return 1.0

        # Convergence = 1 - (std / mean_abs_delta)
        convergence = 1.0 - (std / (mean_abs_delta + 0.1))

        return max(0.0, min(1.0, convergence))

    def _compute_stability_score(self, config_history: List[float]) -> float:
        """Measure config variance under repeated feedback.

        Low variance in config path → stable learning.

        Returns:
            [0.0=unstable, 1.0=stable]
        """
        if len(config_history) < 2:
            return 0.5

        # Compute variance in config values
        mean_config = sum(config_history) / len(config_history)
        variance = sum((c - mean_config) ** 2 for c in config_history) / len(
            config_history
        )
        std = math.sqrt(variance)

        # Stability = exp(-std), normalized
        # Very small std → stability ≈ 1.0
        # Large std → stability ≈ 0.0
        stability = math.exp(-std / max(abs(mean_config), 0.1))

        return max(0.0, min(1.0, stability))

    def get_score(self, skill_id: str, metric_name: str) -> Optional[QualityScore]:
        """Get the most recent quality score for a metric.

        Returns:
            QualityScore if available, None otherwise
        """
        with self._lock:
            score_key = f"{self.tenant_id}:{skill_id}:{metric_name}"
            return self.scores.get(score_key)

    def get_scores_by_skill(self, skill_id: str) -> Dict[str, QualityScore]:
        """Get all quality scores for a Skill.

        Returns:
            Dict[metric_name, QualityScore]
        """
        with self._lock:
            prefix = f"{self.tenant_id}:{skill_id}:"
            return {
                k.split(":")[-1]: v
                for k, v in self.scores.items()
                if k.startswith(prefix)
            }

