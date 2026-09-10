"""
Model Selection Confidence Optimizer — Phase 3, Component 2.

Performs Bayesian online learning with EMA smoothing to update model confidence scores.

This module:
1. Listens for model_selection_feedback events
2. Updates per-(task_type, model) stats using Bayesian method
3. Applies EMA smoothing (alpha=0.1) to prevent oscillation
4. Detects convergence (variance check)
5. Emits confidence_updated audit events
6. Persists to learning store

Bayesian Update (for confidence score):
  Prior: beta(α, β) where confidence ≈ α/(α+β)
  Observe feedback quality_score (0.0–1.0)
  Posterior: β(α + quality_score, β + (1 - quality_score))

EMA Smoothing:
  new_confidence = 0.9 * old_confidence + 0.1 * bayesian_estimate

Minimum Sample Requirement:
  Only update if n_samples >= 5 (prevent overfitting to noise)

Convergence Criteria:
  variance < 0.05 over last 50 samples → CONVERGED

Constraints (ADR-0644):
- Per-tenant isolation: all queries filter by tenant_id
- Audit-first: confidence_updated event logged before persisting
- EMA alpha=0.1 (hard constant, never user-adjustable)
- Minimum N=5 (hard constant)
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple, Any
import json
import logging
import math
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelStats:
    """Per-(task_type, model) statistics (immutable)."""
    task_type: str
    model: str
    tenant_id: str
    n_samples: int
    quality_sum: float  # Sum of quality scores
    quality_squared_sum: float  # Sum of quality^2 (for variance)
    confidence_score: float  # [0.0, 1.0]
    alpha: float = 1.0  # Beta distribution shape param (prior)
    beta: float = 1.0   # Beta distribution shape param (prior)
    last_updated: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def mean_quality(self) -> float:
        """Average quality score."""
        if self.n_samples == 0:
            return 0.0
        return self.quality_sum / self.n_samples

    @property
    def variance(self) -> float:
        """Sample variance of quality scores."""
        if self.n_samples < 2:
            return float('inf')
        mean = self.mean_quality
        return (self.quality_squared_sum / self.n_samples) - (mean ** 2)

    @property
    def std_dev(self) -> float:
        """Standard deviation of quality scores."""
        var = self.variance
        if var < 0:  # Numerical error
            var = 0.0
        return math.sqrt(var)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return asdict(self)


class ConfidenceOptimizer:
    """Bayesian online learning optimizer with EMA smoothing.

    Example:
        optimizer = ConfidenceOptimizer(store, audit_backend)
        optimizer.process_feedback(
            task_type="code_gen",
            model="claude-opus-4-1",
            quality_score=0.95,
            tenant_id="_default"
        )
        confidence = optimizer.get_confidence("code_gen", "claude-opus-4-1", "_default")
    """

    # Constants (hard-coded, never user-adjustable per ADR-0644)
    EMA_ALPHA = 0.1  # 90% old, 10% new
    MIN_SAMPLES = 5
    CONVERGENCE_THRESHOLD = 0.05
    CONVERGENCE_WINDOW = 50

    def __init__(
        self,
        store: Optional[Any] = None,
        audit_backend: Optional[Any] = None,
    ):
        """Initialize optimizer.

        Args:
            store: learning_store for persistence (dict-like interface)
            audit_backend: audit backend for logging updates
        """
        self.store = store or {}
        self.audit_backend = audit_backend
        self._stats_cache: Dict[Tuple[str, str, str], ModelStats] = {}  # (task_type, model, tenant) -> stats
        self._confidence_history: Dict[Tuple[str, str, str], List[float]] = defaultdict(list)

    def process_feedback(
        self,
        task_type: str,
        model: str,
        quality_score: float,
        tenant_id: str = "_default",
    ) -> Tuple[float, bool]:
        """Process a feedback event and update confidence.

        Args:
            task_type: task classification (e.g., "code_gen")
            model: model identifier
            quality_score: [0.0, 1.0] quality assessment
            tenant_id: tenant scope

        Returns:
            (new_confidence_score, is_converged)

        Raises:
            ValueError: if quality_score out of range
        """
        # Validate inputs
        if not 0.0 <= quality_score <= 1.0:
            raise ValueError(f"Quality score out of range: {quality_score}")

        key = (task_type, model, tenant_id)

        # Load or initialize stats
        stats = self._load_stats(key)
        old_confidence = stats.confidence_score

        # Bayesian update
        # Treat quality_score as a sample from beta distribution
        # Update posterior: alpha += quality, beta += (1 - quality)
        new_alpha = stats.alpha + quality_score
        new_beta = stats.beta + (1.0 - quality_score)

        # Bayesian estimate of confidence (mean of beta distribution)
        bayesian_confidence = new_alpha / (new_alpha + new_beta)

        # EMA smoothing (only if n >= MIN_SAMPLES)
        n_samples = stats.n_samples + 1
        if n_samples >= self.MIN_SAMPLES:
            new_confidence = (
                (1 - self.EMA_ALPHA) * old_confidence +
                self.EMA_ALPHA * bayesian_confidence
            )
        else:
            # Before minimum samples, trust Bayesian estimate more
            new_confidence = bayesian_confidence

        # Clamp to [0, 1]
        new_confidence = max(0.0, min(1.0, new_confidence))

        # Update stats
        new_stats = ModelStats(
            task_type=task_type,
            model=model,
            tenant_id=tenant_id,
            n_samples=n_samples,
            quality_sum=stats.quality_sum + quality_score,
            quality_squared_sum=stats.quality_squared_sum + (quality_score ** 2),
            confidence_score=new_confidence,
            alpha=new_alpha,
            beta=new_beta,
            last_updated=datetime.now(timezone.utc).isoformat(),
        )

        # Track confidence history
        self._confidence_history[key].append(new_confidence)

        # Check convergence
        is_converged = self._check_convergence(key)

        # Audit log (FIRST, fail-closed)
        if self.audit_backend:
            try:
                self.audit_backend.write_event(
                    event_type="confidence_updated",
                    tenant_id=tenant_id,
                    task_type=task_type,
                    model=model,
                    old_confidence=old_confidence,
                    new_confidence=new_confidence,
                    quality_observed=quality_score,
                    n_samples=n_samples,
                )
            except Exception as e:
                logger.error(f"Failed to write audit event: {e}")
                raise RuntimeError(f"Audit chain write failed: {e}")

        # Persist (SECOND, safe to fail with log)
        self._cache_stats(key, new_stats)
        if self.store:
            try:
                self.store[f"model_stats:{task_type}:{model}:{tenant_id}"] = new_stats.to_dict()
            except Exception as e:
                logger.warning(f"Failed to persist stats: {e}")

        logger.info(
            f"Confidence updated: {task_type}/{model} "
            f"confidence={old_confidence:.3f} -> {new_confidence:.3f}, "
            f"n={n_samples}, converged={is_converged}"
        )

        return new_confidence, is_converged

    def get_confidence(
        self,
        task_type: str,
        model: str,
        tenant_id: str = "_default",
    ) -> float:
        """Get current confidence score for (task_type, model, tenant).

        Returns:
            confidence [0.0, 1.0], or 0.5 if no data
        """
        key = (task_type, model, tenant_id)
        stats = self._load_stats(key)
        return stats.confidence_score

    def get_stats(
        self,
        task_type: str,
        model: str,
        tenant_id: str = "_default",
    ) -> ModelStats:
        """Get full statistics for (task_type, model, tenant)."""
        key = (task_type, model, tenant_id)
        return self._load_stats(key)

    def is_converged(
        self,
        task_type: str,
        model: str,
        tenant_id: str = "_default",
    ) -> bool:
        """Has this model converged for this task type?"""
        key = (task_type, model, tenant_id)
        return self._check_convergence(key)

    def reset_learning(self, tenant_id: str = "_default"):
        """Reset all learning data for a tenant (for testing/debugging).

        WARNING: This deletes all stored confidence scores and sample history.
        Only call from console reset button or explicit operator action.
        """
        # Clear cache
        keys_to_remove = [k for k in self._stats_cache.keys() if k[2] == tenant_id]
        for key in keys_to_remove:
            del self._stats_cache[key]
            del self._confidence_history[key]

        # Clear store
        if self.store:
            try:
                keys_to_delete = [k for k in self.store.keys() if tenant_id in k]
                for k in keys_to_delete:
                    del self.store[k]
            except Exception as e:
                logger.warning(f"Failed to clear store: {e}")

        logger.info(f"Learning reset for tenant {tenant_id}")

    # ── Private helpers ────────────────────────────────────────────────────

    def _load_stats(self, key: Tuple[str, str, str]) -> ModelStats:
        """Load stats from cache or initialize."""
        if key in self._stats_cache:
            return self._stats_cache[key]

        task_type, model, tenant_id = key

        # Try to load from store
        if self.store:
            store_key = f"model_stats:{task_type}:{model}:{tenant_id}"
            if store_key in self.store:
                data = self.store[store_key]
                stats = ModelStats(**data)
                self._stats_cache[key] = stats
                return stats

        # Initialize with default stats (uniform prior)
        stats = ModelStats(
            task_type=task_type,
            model=model,
            tenant_id=tenant_id,
            n_samples=0,
            quality_sum=0.0,
            quality_squared_sum=0.0,
            confidence_score=0.5,  # Uninformed prior
            alpha=1.0,
            beta=1.0,
        )
        self._cache_stats(key, stats)
        return stats

    def _cache_stats(self, key: Tuple[str, str, str], stats: ModelStats):
        """Cache stats locally."""
        self._stats_cache[key] = stats

    def _check_convergence(self, key: Tuple[str, str, str]) -> bool:
        """Check if this model has converged.

        Convergence criteria:
        - n_samples >= MIN_SAMPLES
        - variance < CONVERGENCE_THRESHOLD over last CONVERGENCE_WINDOW samples
        """
        stats = self._stats_cache.get(key)
        if not stats or stats.n_samples < self.MIN_SAMPLES:
            return False

        history = self._confidence_history[key]
        if len(history) < self.CONVERGENCE_WINDOW:
            return False

        recent = history[-self.CONVERGENCE_WINDOW:]
        mean = sum(recent) / len(recent)
        variance = sum((x - mean) ** 2 for x in recent) / len(recent)

        return variance < self.CONVERGENCE_THRESHOLD


# Singleton instance
_optimizer: Optional[ConfidenceOptimizer] = None


def initialize_optimizer(
    store: Optional[Any] = None,
    audit_backend: Optional[Any] = None,
) -> ConfidenceOptimizer:
    """Initialize the global optimizer."""
    global _optimizer
    _optimizer = ConfidenceOptimizer(store, audit_backend)
    return _optimizer


def get_optimizer() -> ConfidenceOptimizer:
    """Get the global optimizer."""
    global _optimizer
    if _optimizer is None:
        _optimizer = ConfidenceOptimizer()
    return _optimizer
