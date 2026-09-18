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

Two optimizers live in this module, and BOTH are public API. Since 2026-09-18
(ADR-0885 step 0) the first also carries the ONE multi-model ranking,
``ConfidenceOptimizer.rank_models`` / ``select_model`` / ``RankedModel`` — the
replacement for the deleted ``os_skills/model_selector_learning_enhancement.py``
(an unpersisted, unaudited second Beta learner with its own id and price tables).
Its production caller is ``routes/model_selection_analytics.py`` (task-type
breakdown); ``core/console/tests/test_model_ranking_route.py`` drives it over HTTP.

* ``ConfidenceOptimizer`` / ``ModelStats`` / ``get_optimizer()`` — the persisted,
  audit-first per-(task_type, model, tenant) confidence learner (ADR-0644). Read by
  the console (``routes/model_selection_analytics.py``, ``routes/engine_api.py``)
  and fed by ``model_selection_learning_listener.py``.
* ``OSModelSelectorOptimizer`` / ``TaskExecutionOutcome`` / ``HaikuSuccessRateStats``
  / ``create_optimizer()`` — the k=4 in-memory haiku_success_rate learner
  (ADR-0845 Tier 2), covered by ``tests/e2e/test_os_model_selector_learning_loop.py``.

2026-09-16 (9433de4b) replaced the first set with the second without migrating
its three production importers. The running console pre-dated the commit, so
nothing failed until the next ``corvin-webui`` restart (2026-09-17 23:30): the
console package raised ImportError at boot, the gateway's ADR-0015 opt-in mount
swallowed it, and ``/console`` + every ``/v1/console/*`` route answered 404.
Removing either set again means migrating every importer in the same commit —
``tests/test_console_app_importable.py`` fails on the next unmigrated removal.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple, Any, Callable
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


@dataclass(frozen=True)
class RankedModel:
    """One row of :meth:`ConfidenceOptimizer.rank_models` (immutable, audit-safe).

    ``confidence`` is the EMA-smoothed learned score the console already shows;
    ``posterior_mean`` is the raw Beta mean alpha/(alpha+beta). Rates are the
    published rate card, ``None`` when the model is not on it — which a consumer
    must render as unknown, never as free (ADR-0763)."""
    model: str
    task_type: str
    tenant_id: str
    confidence: float
    posterior_mean: float
    n_samples: int
    is_converged: bool
    input_usd_per_1k: Optional[float]
    output_usd_per_1k: Optional[float]

    @property
    def priced(self) -> bool:
        return self.input_usd_per_1k is not None and self.output_usd_per_1k is not None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["priced"] = self.priced
        return d


def _default_price_of(model: str) -> Optional[Tuple[float, float]]:
    """The one published rate card (``model_selection_learner.model_price_per_1k``).
    ``None`` on a stripped install — every model then ranks as unpriced."""
    try:
        from .model_selection_learner import model_price_per_1k  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None
    return model_price_per_1k(model)


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
        # NOT `store or {}` — an empty MutableMapping (len()==0, e.g. a
        # freshly-created PersistentConfidenceStore before its first write)
        # is falsy, so `or` would silently replace it with a throwaway plain
        # dict on every fresh process, discarding all persistence.
        self.store = store if store is not None else {}
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

        # Track confidence history (persisted — see _load_stats/_check_convergence)
        history = self._confidence_history[key]
        history.append(new_confidence)
        del history[:-self.CONVERGENCE_WINDOW]  # cap growth; only the window matters
        try:
            from . import confidence_persistence  # noqa: PLC0415
            confidence_persistence.save_confidence_history(
                f"model_stats:{task_type}:{model}:{tenant_id}", history,
            )
        except Exception as e:  # noqa: BLE001 — in-memory history still works this process
            logger.warning(f"Failed to persist confidence history: {e}")

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
        if self.store is not None:
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

    def rank_models(
        self,
        task_type: str,
        candidates: Optional[List[str]] = None,
        tenant_id: str = "_default",
        *,
        max_output_usd_per_1k: Optional[float] = None,
        price_of: Optional[Callable[[str], Optional[Tuple[float, float]]]] = None,
    ) -> List["RankedModel"]:
        """Rank candidate models for ``task_type`` by what this tenant has learned.

        ADR-0885 step 0. This is the ONE multi-model ranking; it replaced
        ``os_skills/model_selector_learning_enhancement.py``, an in-memory,
        unaudited, unpersisted second Beta learner that carried its own model-id
        table (two of the four ids did not exist) and its own price table. Both
        tables now come from the places that already own them:

        * ``candidates=None`` — every model this tenant has persisted stats for
          (``confidence_persistence.list_entries``). Pass the engine registry's
          ids (``engine_models.registry_as_dict``) to rank a fixed catalogue.
        * ``price_of`` — defaults to ``model_selection_learner.model_price_per_1k``,
          the one published rate card the cost panels bill against.

        Budget: with ``max_output_usd_per_1k`` set, a model survives only if its
        published OUTPUT rate is known and at or under the cap. An unpriced model
        is dropped under a cap — never treated as free (ADR-0763). Output is the
        rate a long generation is dominated by, and the larger of the two on
        every current model, which is why it is the one a budget binds.

        Order: learned confidence desc, then samples desc, then cheaper output
        rate, then model id — stable across calls with equal inputs.

        Pure read. No store write, no audit event: a ranking is a VIEW over
        learned state. The decision a caller makes with it is what gets audited
        (``skill.model_selector.classified``, ``engine.config.updated``).
        """
        if candidates is None:
            try:
                from . import confidence_persistence  # noqa: PLC0415
                candidates = [
                    m for tt, m in confidence_persistence.list_entries(tenant_id)
                    if tt == task_type
                ]
            except Exception as e:  # noqa: BLE001 — stripped install: rank the cache only
                logger.warning(f"list_entries unavailable, ranking cached keys only: {e}")
                candidates = [
                    k[1] for k in self._stats_cache if k[0] == task_type and k[2] == tenant_id
                ]

        if price_of is None:
            price_of = _default_price_of

        seen: set[str] = set()
        ranked: List[RankedModel] = []
        for model in candidates:
            if not model or model in seen:
                continue
            seen.add(model)
            try:
                price = price_of(model)
            except Exception as e:  # noqa: BLE001 — a rate-card fault must not hide the ranking
                logger.warning(f"price lookup failed for {model}: {e}")
                price = None
            in_rate = price[0] if price else None
            out_rate = price[1] if price else None
            if max_output_usd_per_1k is not None and (
                out_rate is None or out_rate > max_output_usd_per_1k
            ):
                continue
            stats = self._load_stats((task_type, model, tenant_id))
            denom = stats.alpha + stats.beta
            ranked.append(RankedModel(
                model=model,
                task_type=task_type,
                tenant_id=tenant_id,
                confidence=stats.confidence_score,
                posterior_mean=(stats.alpha / denom) if denom > 0 else 0.5,
                n_samples=stats.n_samples,
                is_converged=self._check_convergence((task_type, model, tenant_id)),
                input_usd_per_1k=in_rate,
                output_usd_per_1k=out_rate,
            ))

        ranked.sort(key=lambda r: (
            -r.confidence,
            -r.n_samples,
            r.output_usd_per_1k if r.output_usd_per_1k is not None else float("inf"),
            r.model,
        ))
        return ranked

    def select_model(
        self,
        task_type: str,
        candidates: Optional[List[str]] = None,
        tenant_id: str = "_default",
        *,
        max_output_usd_per_1k: Optional[float] = None,
        price_of: Optional[Callable[[str], Optional[Tuple[float, float]]]] = None,
    ) -> Optional["RankedModel"]:
        """The top entry of :meth:`rank_models`, or ``None`` when nothing
        survives the candidate set and budget. ``None`` is the honest answer —
        there is deliberately no hard-coded fallback model here; the caller's
        pinned default (``spec.engine_models`` / ``model_selection_config``)
        is the fallback, and it is the caller who knows it."""
        ranked = self.rank_models(
            task_type, candidates, tenant_id,
            max_output_usd_per_1k=max_output_usd_per_1k, price_of=price_of,
        )
        return ranked[0] if ranked else None

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
        if self.store is not None:
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
        if self.store is not None:
            store_key = f"model_stats:{task_type}:{model}:{tenant_id}"
            if store_key in self.store:
                data = self.store[store_key]
                stats = ModelStats(**data)
                self._stats_cache[key] = stats
                # Convergence needs the confidence trajectory too, not just
                # the latest stats — without this, every process restart
                # (or a cross-process read, e.g. the console reading what
                # the bridge daemon wrote) starts is_converged() from an
                # empty window and reports "not converged" regardless of
                # real history.
                try:
                    from . import confidence_persistence  # noqa: PLC0415
                    self._confidence_history[key] = confidence_persistence.load_confidence_history(store_key)
                except Exception:  # noqa: BLE001 — history is an optimization, not correctness-critical
                    pass
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


class _SkillAuditBackend:
    """Adapts ConfidenceOptimizer's ``audit_backend.write_event(**kwargs)``
    call to the tenant's real hash-chained core chain (skill_audit.py) —
    the same sink os.model_selector's shadow classification and
    os.delegation_router's L5 shadow record use. Without this,
    ``audit_backend=None`` (the dataclass default) means "confidence_updated"
    is never audited even though ADR-0644 calls audit-first non-negotiable."""

    _ALLOWLIST_REGISTERED = False

    def write_event(self, *, event_type: str, tenant_id: str, **details: Any) -> None:
        try:
            from core.skills.skill_audit import emit_skill_audit  # noqa: PLC0415
        except Exception:  # noqa: BLE001 — stripped install without core.skills
            return
        if not self._ALLOWLIST_REGISTERED:
            try:
                import corvin_core._bootstrap  # noqa: F401
            except Exception:  # noqa: BLE001
                pass
            try:
                from forge.security_events import register_event_allowlist  # type: ignore[import-not-found]  # noqa: PLC0415
                register_event_allowlist("confidence_updated", {
                    "task_type", "model", "old_confidence", "new_confidence",
                    "quality_observed", "n_samples",
                })
                type(self)._ALLOWLIST_REGISTERED = True
            except Exception:  # noqa: BLE001 — best-effort; emit still tries
                pass
        emit_skill_audit(
            tenant_id=tenant_id,
            event_type=event_type,
            tool="os.model_selector",
            details=details,
        )


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
    """Get the global optimizer — real, persistent, audited by default.

    2026-09-10: was ``ConfidenceOptimizer()`` (in-memory dict, no audit) —
    every process (the bridge daemon that classifies+feeds outcomes, the
    console gateway that reads analytics) got its OWN invisible-to-each-other
    singleton, and a restart of either wiped it. See confidence_persistence.py.
    """
    global _optimizer
    if _optimizer is None:
        from . import confidence_persistence  # noqa: PLC0415
        _optimizer = ConfidenceOptimizer(
            store=confidence_persistence.PersistentConfidenceStore(),
            audit_backend=_SkillAuditBackend(),
        )
    return _optimizer


# ═══════════════════════════════════════════════════════════════════════════
# k=4 Model Selection Optimizer (ADR-0845, Tier 2 Learning Loop)
#
# Bayesian update for haiku_success_rate per task_type. Receives task execution
# outcomes (success, quality, tokens) and updates heuristics based on observed
# performance:
#   1. TaskManager.record_event(outcome) emits LearningEvent
#   2. Optimizer receives event (task_type, quality_score, success)
#   3. Applies Bayesian Beta distribution update
#   4. Updates haiku_success_rate for task_type
#   5. Emits model_selector_heuristic_updated event (immutable, hash-chained)
#   6. Next task uses updated heuristics
#   7. Convergence measured (std-dev < 5%)
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TaskExecutionOutcome:
    """Immutable task execution outcome (audit-safe)."""
    
    task_id: str
    task_type: str
    model_used: str                    # "haiku" or "sonnet"
    quality_score: float               # 0.0-1.0
    tokens_used: int
    success: bool                      # Did task complete successfully?
    completion_time_ms: float
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    def to_dict(self) -> Dict:
        """Audit-safe serialization (no PII)."""
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "model_used": self.model_used,
            "quality_score": self.quality_score,
            "tokens_used": self.tokens_used,
            "success": self.success,
            "completion_time_ms": self.completion_time_ms,
            "timestamp": self.timestamp,
        }


@dataclass
class HaikuSuccessRateStats:
    """Statistics for Haiku success rate per task type."""
    
    task_type: str
    alpha: float = 1.0                 # Beta distribution alpha (successes + 1)
    beta: float = 1.0                  # Beta distribution beta (failures + 1)
    observations: int = 0
    last_updated: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    @property
    def mean(self) -> float:
        """Bayesian mean of Beta distribution."""
        if self.alpha + self.beta <= 0:
            return 0.5
        return self.alpha / (self.alpha + self.beta)
    
    @property
    def variance(self) -> float:
        """Variance of Beta distribution."""
        n = self.alpha + self.beta
        if n <= 1:
            return 0.25  # Maximum variance for Beta
        return (self.alpha * self.beta) / (n * n * (n + 1))
    
    @property
    def std_dev(self) -> float:
        """Standard deviation of Beta distribution."""
        return math.sqrt(self.variance)
    
    def update(self, success: bool, quality_score: float = 1.0):
        """Bayesian update with outcome."""
        # Treat outcome as success if both success flag and quality >= 0.90
        is_success = success and quality_score >= 0.90
        
        if is_success:
            self.alpha += 1.0
        else:
            self.beta += 1.0
        
        self.observations += 1
        self.last_updated = datetime.utcnow().isoformat()


class OSModelSelectorOptimizer:
    """
    Optimize Haiku success rates via Bayesian learning.
    
    Per-task-type success rate tracking using Beta distribution.
    Updates based on actual task execution outcomes.
    """
    
    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        
        # Task-type-specific success rate stats
        self.haiku_stats: Dict[str, HaikuSuccessRateStats] = {
            "code_review": HaikuSuccessRateStats("code_review"),
            "testing": HaikuSuccessRateStats("testing"),
            "documentation": HaikuSuccessRateStats("documentation"),
            "analysis": HaikuSuccessRateStats("analysis"),
            "refactoring": HaikuSuccessRateStats("refactoring"),
            "code_gen": HaikuSuccessRateStats("code_gen"),
            "summarization": HaikuSuccessRateStats("summarization"),
            "default": HaikuSuccessRateStats("default"),
        }
        
        # Track heuristic updates (for convergence testing)
        self.heuristic_history: Dict[str, list] = {
            task_type: [] for task_type in self.haiku_stats.keys()
        }
    
    def record_outcome(
        self,
        outcome: TaskExecutionOutcome,
    ) -> Tuple[float, bool]:
        """
        Record task execution outcome and update heuristics.
        
        Args:
            outcome: TaskExecutionOutcome with success/quality data
        
        Returns:
            (updated_success_rate, converged)
        """
        task_type = outcome.task_type
        
        # Get or create stats for this task type
        if task_type not in self.haiku_stats:
            self.haiku_stats[task_type] = HaikuSuccessRateStats(task_type)
        
        stats = self.haiku_stats[task_type]
        old_mean = stats.mean
        
        # Bayesian update
        stats.update(outcome.success, outcome.quality_score)
        
        new_mean = stats.mean
        delta = abs(new_mean - old_mean)
        
        # Track for convergence testing
        self.heuristic_history[task_type].append({
            "iteration": stats.observations,
            "mean": new_mean,
            "std_dev": stats.std_dev,
            "timestamp": datetime.utcnow().isoformat(),
        })
        
        # Check convergence (std-dev < 5%)
        converged = stats.std_dev < 0.05
        
        logger.info(
            f"Model selector outcome recorded: task_type={task_type}, "
            f"quality={outcome.quality_score:.2f}, success={outcome.success}, "
            f"haiku_rate={new_mean:.3f} (was {old_mean:.3f}), "
            f"std_dev={stats.std_dev:.3f}, converged={converged}"
        )
        
        return new_mean, converged
    
    def get_haiku_success_rate(self, task_type: Optional[str] = None) -> float:
        """Get current Haiku success rate for task type."""
        if not task_type or task_type not in self.haiku_stats:
            return self.haiku_stats["default"].mean
        
        return self.haiku_stats[task_type].mean
    
    def get_confidence(self, task_type: Optional[str] = None) -> float:
        """Get confidence in success rate (inverse of std_dev)."""
        if not task_type or task_type not in self.haiku_stats:
            stats = self.haiku_stats["default"]
        else:
            stats = self.haiku_stats[task_type]
        
        return max(0.0, 1.0 - (stats.std_dev / 0.20))
    
    def get_all_stats(self) -> Dict[str, Dict]:
        """Get all task-type stats."""
        return {
            task_type: {
                "mean": stats.mean,
                "std_dev": stats.std_dev,
                "observations": stats.observations,
                "alpha": stats.alpha,
                "beta": stats.beta,
            }
            for task_type, stats in self.haiku_stats.items()
        }
    
    def check_convergence(self, target_std_dev: float = 0.05) -> Dict[str, bool]:
        """Check convergence for each task type."""
        return {
            task_type: stats.std_dev < target_std_dev
            for task_type, stats in self.haiku_stats.items()
        }
    
    def get_convergence_summary(self) -> Tuple[float, int, int]:
        """Get convergence summary."""
        if not self.haiku_stats:
            return 0.0, 0, 0
        
        std_devs = [s.std_dev for s in self.haiku_stats.values()]
        converged = sum(1 for s in self.haiku_stats.values() if s.std_dev < 0.05)
        
        avg_std_dev = sum(std_devs) / len(std_devs)
        return avg_std_dev, converged, len(self.haiku_stats)
    
    def emit_heuristic_update_event(
        self,
        task_type: str,
        old_rate: float,
        new_rate: float,
    ) -> Dict:
        """Emit audit event for heuristic update."""
        event = {
            "event_type": "model_selector_heuristic_updated",
            "task_type": task_type,
            "old_haiku_success_rate": old_rate,
            "new_haiku_success_rate": new_rate,
            "delta": new_rate - old_rate,
            "timestamp": datetime.utcnow().isoformat(),
            "tenant_id": self.tenant_id,
        }
        logger.info(f"Emitting heuristic update: {event}")
        return event
    
    def reset_for_testing(self):
        """Reset all stats (testing only)."""
        self.haiku_stats = {
            task_type: HaikuSuccessRateStats(task_type)
            for task_type in self.haiku_stats.keys()
        }
        self.heuristic_history = {
            task_type: [] for task_type in self.haiku_stats.keys()
        }


def create_optimizer(tenant_id: str = "_default") -> OSModelSelectorOptimizer:
    """Factory function to create optimizer."""
    return OSModelSelectorOptimizer(tenant_id)
