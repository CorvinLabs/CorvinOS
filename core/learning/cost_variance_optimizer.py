"""
Cost-Variance Feedback Loop — ADR-0377 Phase 2

Learns from cost variance (actual_cost - estimated_cost) to adapt model selection
thresholds over time. Uses Bayesian online learning with convergence detection.

This module:
1. Listens for cost tracking events (subsystem cost events)
2. Calculates cost variance per (task_type, subsystem) pair
3. Updates per-(task_type, subsystem) variance stats using Bayesian method
4. Recommends threshold adjustments based on variance + quality
5. Emits cost_variance_updated audit events
6. Provides threshold recommendations for ModelSelector

Cost Variance Calculation:
  variance = actual_cost - estimated_cost
  positive variance = task cost more than expected (estimate too low)
  negative variance = task cost less than expected (estimate too high)

Bayesian Update (for cost variance distribution):
  Prior: normal(μ, σ²)
  Observe: cost_variance sample
  Posterior: normal(μ', σ'²) via Kalman-like update

Threshold Adjustment Logic:
  If cost_variance < -0.1 (significantly cheaper than expected):
    - Lower complexity_threshold (prefer cheaper models like Haiku)
  If quality < 0.7 despite low cost:
    - Raise complexity_threshold (avoid underestimating complexity)
  Base threshold: 0.5 (configurable per operator style)

Constraints (ADR-0377 Phase 2):
- Per-tenant isolation: all queries filter by tenant_id
- Audit-first: cost_variance_updated event logged before persisting
- Convergence detection: variance stabilizes within N samples
- Minimum samples: N >= 10 before recommendations
- Fail-closed: if optimizer unavailable, use base threshold 0.5 (no surprises)
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
class CostVarianceStats:
    """Per-(task_type, subsystem) cost variance statistics (immutable)."""
    task_type: str
    subsystem: str
    tenant_id: str
    n_samples: int

    # Running statistics for cost variance
    variance_sum: float  # Sum of cost variances (actual - estimated)
    variance_squared_sum: float  # Sum of (cost_variance)²

    # Quality tracking (for threshold adjustment)
    quality_sum: float  # Sum of quality scores

    # Threshold tracking
    recommended_threshold: float  # [0.1, 0.9], base 0.5
    base_threshold: float = 0.5  # Immutable baseline

    last_updated: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def mean_variance(self) -> float:
        """Average cost variance."""
        if self.n_samples == 0:
            return 0.0
        return self.variance_sum / self.n_samples

    @property
    def variance(self) -> float:
        """Variance of cost variances (spread)."""
        if self.n_samples < 2:
            return float('inf')
        mean = self.mean_variance
        return (self.variance_squared_sum / self.n_samples) - (mean ** 2)

    @property
    def std_dev(self) -> float:
        """Standard deviation of cost variances."""
        var = self.variance
        if var < 0:  # Numerical error
            var = 0.0
        return math.sqrt(var)

    @property
    def mean_quality(self) -> float:
        """Average quality score."""
        if self.n_samples == 0:
            return 0.5
        return self.quality_sum / self.n_samples

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return asdict(self)


class CostVarianceOptimizer:
    """Bayesian online learning optimizer for cost variance.

    Tracks cost variance per (task_type, subsystem) and recommends threshold
    adjustments to ModelSelector based on observed variance patterns.

    Example:
        optimizer = CostVarianceOptimizer(store, audit_backend)
        optimizer.process_cost_variance(
            task_type="code_gen",
            subsystem="code_analyzer",
            quality_score=0.95,
            cost_variance=-0.02,  # Cheaper than expected
            tenant_id="_default"
        )
        threshold = optimizer.get_threshold_recommendation(
            "code_gen", "code_analyzer", "_default"
        )
    """

    # Constants (hard-coded, never user-adjustable per ADR-0377)
    MIN_SAMPLES = 10  # Before making recommendations
    CONVERGENCE_THRESHOLD = 0.01  # Cost variance spread < $0.01
    CONVERGENCE_WINDOW = 50

    # Threshold adjustment rules
    VARIANCE_ADJUSTMENT_FACTOR = 0.2  # How much to adjust threshold per unit variance
    QUALITY_PENALTY = 0.15  # Lower threshold if quality is low

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
        self.store = store if store is not None else {}
        self.audit_backend = audit_backend
        self._stats_cache: Dict[Tuple[str, str, str], CostVarianceStats] = {}
        self._variance_history: Dict[Tuple[str, str, str], List[float]] = defaultdict(list)

    def process_cost_variance(
        self,
        task_type: str,
        subsystem: str,
        quality_score: float,
        cost_variance: float,
        tenant_id: str = "_default",
        base_threshold: float = 0.5,
    ) -> Tuple[float, bool]:
        """Process a cost variance event and update threshold recommendation.

        Args:
            task_type: task classification (e.g., "code_gen")
            subsystem: subsystem name (e.g., "code_analyzer")
            quality_score: [0.0, 1.0] quality assessment
            cost_variance: actual_cost - estimated_cost (can be negative)
            tenant_id: tenant scope
            base_threshold: base complexity threshold (default 0.5)

        Returns:
            (recommended_threshold, is_converged)

        Raises:
            ValueError: if quality_score out of range
        """
        # Validate inputs
        if not 0.0 <= quality_score <= 1.0:
            raise ValueError(f"Quality score out of range: {quality_score}")

        key = (task_type, subsystem, tenant_id)

        # Load or initialize stats
        stats = self._load_stats(key, base_threshold)
        old_threshold = stats.recommended_threshold

        # Update sample counts
        n_samples = stats.n_samples + 1

        # New statistics (immutable, so create new dataclass)
        new_stats = CostVarianceStats(
            task_type=task_type,
            subsystem=subsystem,
            tenant_id=tenant_id,
            n_samples=n_samples,
            variance_sum=stats.variance_sum + cost_variance,
            variance_squared_sum=stats.variance_squared_sum + (cost_variance ** 2),
            quality_sum=stats.quality_sum + quality_score,
            recommended_threshold=self._compute_threshold(
                variance_sum=stats.variance_sum + cost_variance,
                n_samples=n_samples,
                quality_sum=stats.quality_sum + quality_score,
                base_threshold=base_threshold,
            ),
            base_threshold=base_threshold,
            last_updated=datetime.now(timezone.utc).isoformat(),
        )

        # Track variance history (for convergence detection)
        history = self._variance_history[key]
        history.append(cost_variance)
        del history[:-self.CONVERGENCE_WINDOW]  # Keep only recent window

        # Try to persist variance history
        try:
            from . import confidence_persistence  # noqa: PLC0415
            confidence_persistence.save_confidence_history(
                f"cost_variance:{task_type}:{subsystem}:{tenant_id}", history,
            )
        except Exception as e:  # noqa: BLE001 — in-memory history still works
            logger.debug(f"Failed to persist variance history: {e}")

        # Check convergence
        is_converged = self._check_convergence(key)

        # Audit log (FIRST, fail-closed) — audit backend is REQUIRED
        if not self.audit_backend:
            raise RuntimeError("Cost variance optimizer requires audit backend (fail-closed)")

        try:
            self.audit_backend.write_event(
                event_type="cost_variance_updated",
                tenant_id=tenant_id,
                task_type=task_type,
                subsystem=subsystem,
                old_threshold=old_threshold,
                new_threshold=new_stats.recommended_threshold,
                cost_variance_observed=cost_variance,
                quality_observed=quality_score,
                n_samples=n_samples,
                is_converged=is_converged,
            )
        except Exception as e:
            logger.error(f"Failed to write audit event: {e}")
            raise RuntimeError(f"Audit chain write failed: {e}")

        # Persist (SECOND, safe to fail with log)
        self._cache_stats(key, new_stats)
        if self.store is not None:
            try:
                store_key = f"cost_variance:{task_type}:{subsystem}:{tenant_id}"
                self.store[store_key] = new_stats.to_dict()
            except Exception as e:
                logger.warning(f"Failed to persist cost variance stats: {e}")

        logger.info(
            f"Cost variance updated: {task_type}/{subsystem} "
            f"threshold={old_threshold:.3f} -> {new_stats.recommended_threshold:.3f}, "
            f"variance={cost_variance:.4f}, quality={quality_score:.2f}, "
            f"n={n_samples}, converged={is_converged}"
        )

        return new_stats.recommended_threshold, is_converged

    def get_threshold_recommendation(
        self,
        task_type: str,
        subsystem: str,
        tenant_id: str = "_default",
        base_threshold: float = 0.5,
    ) -> float:
        """Get recommended complexity threshold for (task_type, subsystem).

        Returns:
            threshold [0.1, 0.9], or base_threshold if no data
        """
        key = (task_type, subsystem, tenant_id)
        stats = self._load_stats(key, base_threshold)

        # Only return learned threshold if converged (minimum samples met)
        if stats.n_samples < self.MIN_SAMPLES:
            return stats.base_threshold

        return stats.recommended_threshold

    def get_stats(
        self,
        task_type: str,
        subsystem: str,
        tenant_id: str = "_default",
        base_threshold: float = 0.5,
    ) -> CostVarianceStats:
        """Get full statistics for (task_type, subsystem, tenant)."""
        key = (task_type, subsystem, tenant_id)
        return self._load_stats(key, base_threshold)

    def is_converged(
        self,
        task_type: str,
        subsystem: str,
        tenant_id: str = "_default",
    ) -> bool:
        """Has this (task_type, subsystem) converged?"""
        key = (task_type, subsystem, tenant_id)
        return self._check_convergence(key)

    def reset_learning(self, tenant_id: str = "_default"):
        """Reset all learning data for a tenant.

        WARNING: This deletes all stored variance stats and history.
        Only call from console reset button or explicit operator action.
        """
        # Clear cache
        keys_to_remove = [k for k in self._stats_cache.keys() if k[2] == tenant_id]
        for key in keys_to_remove:
            del self._stats_cache[key]
            if key in self._variance_history:
                del self._variance_history[key]

        # Clear store
        if self.store is not None:
            try:
                keys_to_delete = [k for k in self.store.keys() if tenant_id in k and "cost_variance:" in k]
                for k in keys_to_delete:
                    del self.store[k]
            except Exception as e:
                logger.warning(f"Failed to clear store: {e}")

        logger.info(f"Cost variance learning reset for tenant {tenant_id}")

    # ── Private helpers ────────────────────────────────────────────────────

    def _load_stats(
        self,
        key: Tuple[str, str, str],
        base_threshold: float = 0.5,
    ) -> CostVarianceStats:
        """Load stats from cache or initialize."""
        if key in self._stats_cache:
            return self._stats_cache[key]

        task_type, subsystem, tenant_id = key

        # Try to load from store
        if self.store is not None:
            store_key = f"cost_variance:{task_type}:{subsystem}:{tenant_id}"
            if store_key in self.store:
                data = self.store[store_key]
                stats = CostVarianceStats(**data)
                self._stats_cache[key] = stats
                # Load variance history
                try:
                    from . import confidence_persistence  # noqa: PLC0415
                    self._variance_history[key] = confidence_persistence.load_confidence_history(store_key)
                except Exception:  # noqa: BLE001 — history is optimization
                    pass
                return stats

        # Initialize with default stats
        stats = CostVarianceStats(
            task_type=task_type,
            subsystem=subsystem,
            tenant_id=tenant_id,
            n_samples=0,
            variance_sum=0.0,
            variance_squared_sum=0.0,
            quality_sum=0.0,
            recommended_threshold=base_threshold,
            base_threshold=base_threshold,
        )
        self._cache_stats(key, stats)
        return stats

    def _cache_stats(self, key: Tuple[str, str, str], stats: CostVarianceStats):
        """Cache stats locally."""
        self._stats_cache[key] = stats

    def _compute_threshold(
        self,
        variance_sum: float,
        n_samples: int,
        quality_sum: float,
        base_threshold: float = 0.5,
    ) -> float:
        """Compute recommended complexity threshold based on variance + quality.

        Logic:
        - If mean variance is negative (cheaper than expected), lower threshold
        - If mean variance is positive (more expensive), raise threshold slightly
        - If quality is low despite cost savings, raise threshold
        - Clamp to [0.1, 0.9]
        """
        if n_samples == 0:
            return base_threshold

        mean_variance = variance_sum / n_samples
        mean_quality = quality_sum / n_samples

        # Start with base threshold
        threshold = base_threshold

        # Adjust for cost variance
        # Negative variance: tasks cheaper than expected → lower threshold (prefer cheap models)
        # Positive variance: tasks more expensive → raise threshold (avoid cheap models)
        if mean_variance < -0.01:  # Consistently cheaper than expected
            threshold -= abs(mean_variance) * self.VARIANCE_ADJUSTMENT_FACTOR
        elif mean_variance > 0.01:  # Consistently more expensive than expected
            threshold += abs(mean_variance) * self.VARIANCE_ADJUSTMENT_FACTOR

        # Adjust for quality
        # If quality is low, we're being too aggressive with cheap models
        # Raise threshold to use more expensive (better) models
        if mean_quality < 0.7:
            threshold += self.QUALITY_PENALTY

        # Clamp to valid range [0.1, 0.9]
        threshold = max(0.1, min(0.9, threshold))

        return threshold

    def _check_convergence(self, key: Tuple[str, str, str]) -> bool:
        """Check if this (task_type, subsystem) has converged.

        Convergence criteria:
        - n_samples >= MIN_SAMPLES
        - std_dev of variance < CONVERGENCE_THRESHOLD over last CONVERGENCE_WINDOW
        """
        stats = self._stats_cache.get(key)
        if not stats or stats.n_samples < self.MIN_SAMPLES:
            return False

        history = self._variance_history[key]
        if len(history) < self.CONVERGENCE_WINDOW:
            return False

        recent = history[-self.CONVERGENCE_WINDOW:]
        mean = sum(recent) / len(recent)
        variance = sum((x - mean) ** 2 for x in recent) / len(recent)
        std_dev = math.sqrt(variance) if variance > 0 else 0.0

        return std_dev < self.CONVERGENCE_THRESHOLD


class _SkillAuditBackend:
    """Adapts CostVarianceOptimizer's ``audit_backend.write_event(**kwargs)``
    call to the tenant's real hash-chained core chain (skill_audit.py)."""

    _ALLOWLIST_REGISTERED = False

    def write_event(self, *, event_type: str, tenant_id: str, **details: Any) -> None:
        try:
            from core.skills.skill_audit import emit_skill_audit  # noqa: PLC0415
        except Exception:  # noqa: BLE001 — stripped install
            return

        if not self._ALLOWLIST_REGISTERED:
            try:
                import corvin_core._bootstrap  # noqa: F401
            except Exception:  # noqa: BLE001
                pass
            try:
                from forge.security_events import register_event_allowlist  # type: ignore[import-not-found]  # noqa: PLC0415
                register_event_allowlist("cost_variance_updated", {
                    "task_type", "subsystem", "old_threshold", "new_threshold",
                    "cost_variance_observed", "quality_observed", "n_samples",
                    "is_converged",
                })
                type(self)._ALLOWLIST_REGISTERED = True
            except Exception:  # noqa: BLE001 — best-effort
                pass

        emit_skill_audit(
            tenant_id=tenant_id,
            event_type=event_type,
            tool="os.model_selector",
            details=details,
        )


# Singleton instance
_optimizer: Optional[CostVarianceOptimizer] = None


def initialize_optimizer(
    store: Optional[Any] = None,
    audit_backend: Optional[Any] = None,
) -> CostVarianceOptimizer:
    """Initialize the global optimizer."""
    global _optimizer
    _optimizer = CostVarianceOptimizer(store, audit_backend)
    return _optimizer


def get_optimizer() -> CostVarianceOptimizer:
    """Get the global optimizer — real, persistent, audited by default."""
    global _optimizer
    if _optimizer is None:
        try:
            from . import confidence_persistence  # noqa: PLC0415
            _optimizer = CostVarianceOptimizer(
                store=confidence_persistence.PersistentConfidenceStore(),
                audit_backend=_SkillAuditBackend(),
            )
        except Exception as e:
            logger.warning(f"Failed to initialize persistent optimizer: {e}, using in-memory")
            _optimizer = CostVarianceOptimizer()
    return _optimizer
