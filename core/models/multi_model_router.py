"""Model cost/quality ranking for planning (ADR-0857).

WHAT THIS IS: an offline estimator. Given a capability bar, it ranks the
models this install can actually run and estimates what a task would cost.

WHAT THIS IS NOT: the router. Nothing in the request path calls it. Live
worker-engine routing is ``corvin_operator/bridges/shared/delegation_policy.py``
(ADR-0759), with ``os.delegation_router`` advising in shadow mode (ADR-0613).
The name predates that split; read it as "multi-model cost model".

Rewritten 2026-09-16. The previous version was unusable for either purpose:

  * It profiled ``claude-3-5-haiku-20241022``, ``claude-3-5-sonnet-20241022``,
    ``claude-3-opus-20240229``, ``gemini-1.5-flash`` and ``gemini-1.5-pro`` —
    a 2024 line-up. The engine registry declares none of them, and no Gemini
    provider exists on this install at all, so a recommendation could name a
    model the system cannot run.
  * Prices were stale and collapsed into one ``cost_per_1k_tokens`` "average"
    per model (0.0450 for Opus, the 2024 rate). Input and output bill at rates
    that differ 5x on every current model, so no single average is correct for
    any token mix.
  * ``accuracy`` and the four ``*_aptitude`` fields were introduced as
    "based on empirical data". There is no such data: they are hand-written
    constants, and ``accuracy=1.0`` for Opus was a definition, not a
    measurement. Ranking by them produced a confident-looking order with
    nothing behind it.
  * ``latency_ms`` was likewise invented; nothing here measures latency.

What replaced them:

  * models and prices come from the SAME sources the rest of the console
    uses — the engine registry for what exists, and
    ``model_selection_learner.model_price_per_1k`` for the published rate
    card. No third table to drift;
  * input and output rates stay separate, and cost estimates take a real
    token split;
  * the one quality signal is ``tier``, an ORDERING the vendor's own line-up
    asserts (haiku < sonnet < opus < fable). It is a declared prior, named as
    one, with no per-task-type aptitude invented on top of it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ModelTier(int, Enum):
    """Capability ordering. The ONLY quality signal this module carries.

    An ordering, not a score: it says Sonnet is positioned above Haiku, which
    the vendor's line-up and pricing both assert. It does NOT say by how much —
    that would be the invented ``accuracy`` this replaces.
    """
    LOCAL_FREE = 0      # local compute, no per-token cost
    FAST_CHEAP = 1      # Haiku
    BALANCED = 2        # Sonnet
    BEST_QUALITY = 3    # Opus
    FRONTIER = 4        # Fable / Mythos


@dataclass(frozen=True)
class ModelProfile:
    """What is KNOWN about a model: identity, price, tier, limits.

    No accuracy and no aptitude fields. If a quality number belongs here one
    day it must come from measurement — ``/v1/console/v1/engine/analytics``
    already computes a real per-model mean_quality WITH a sample count — not
    from a constant typed into a table.
    """
    model_id: str
    provider: str
    tier: ModelTier

    # Published rate card, per 1k tokens, kept SEPARATE. None = not on the
    # card; callers must treat that as unknown, never as free. Local models
    # are the one legitimate 0.0.
    input_usd_per_1k: Optional[float]
    output_usd_per_1k: Optional[float]

    max_context_tokens: int = 0
    max_output_tokens: int = 0

    @property
    def priced(self) -> bool:
        return self.input_usd_per_1k is not None and self.output_usd_per_1k is not None

    @property
    def is_local(self) -> bool:
        return self.tier is ModelTier.LOCAL_FREE


@dataclass
class ModelRanking:
    """A ranking plus the reason it came out that way."""
    task_tier: ModelTier
    ranked_models: List[Tuple[str, float, str]] = field(default_factory=list)
    recommended_model: Optional[str] = None
    recommended_reason: str = ""
    fallback_chain: List[str] = field(default_factory=list)
    #: Models left out because the rate card does not price them — reported so
    #: an absent candidate is visible rather than silently dropped.
    unpriced: List[str] = field(default_factory=list)


# Context/output limits per family, from the published model table.
_LIMITS: Dict[str, Tuple[int, int]] = {
    "claude-fable-5-1": (1_000_000, 128_000),
    "claude-fable-5": (1_000_000, 128_000),
    "claude-opus-5": (1_000_000, 128_000),
    "claude-sonnet-5": (1_000_000, 128_000),
    "claude-haiku-4-5": (200_000, 64_000),
}

# Tier by family prefix; longest match wins, the same rule the rate card uses.
_TIERS: Dict[str, ModelTier] = {
    "claude-fable": ModelTier.FRONTIER,
    "claude-mythos": ModelTier.FRONTIER,
    "claude-opus": ModelTier.BEST_QUALITY,
    "claude-sonnet": ModelTier.BALANCED,
    "claude-haiku": ModelTier.FAST_CHEAP,
}


def _strip_namespace(model_id: str) -> str:
    """``anthropic/claude-opus-5`` -> ``claude-opus-5``.

    The registry declares OpenCode models with a provider namespace. Without
    this, tier and limit lookups miss and every such model looks unknown.
    """
    return model_id.split("/", 1)[1] if "/" in model_id else model_id


def _lookup(table: Dict[str, Any], model_id: str) -> Any:
    """Longest-prefix match against a family table."""
    candidate = _strip_namespace(model_id)
    best_key: Optional[str] = None
    for key in table:
        if candidate.startswith(key) and (best_key is None or len(key) > len(best_key)):
            best_key = key
    return table[best_key] if best_key is not None else None


def _tier_for(model_id: str) -> ModelTier:
    if model_id.startswith("ollama/") or model_id.startswith("ollama:"):
        return ModelTier.LOCAL_FREE
    tier = _lookup(_TIERS, model_id)
    return tier if isinstance(tier, ModelTier) else ModelTier.BALANCED


def _provider_for(model_id: str) -> str:
    if "/" in model_id:
        return model_id.split("/", 1)[0]
    return "anthropic" if model_id.startswith("claude-") else "unknown"


def build_profiles(model_ids: List[str]) -> Dict[str, ModelProfile]:
    """Profiles for the given model ids, priced from the one rate card."""
    from core.learning.model_selection_learner import model_price_per_1k

    profiles: Dict[str, ModelProfile] = {}
    for mid in model_ids:
        if not mid:
            continue  # the registry's "engine default" sentinel, not a model
        tier = _tier_for(mid)
        if tier is ModelTier.LOCAL_FREE:
            in_rate: Optional[float] = 0.0
            out_rate: Optional[float] = 0.0
        else:
            price = model_price_per_1k(_strip_namespace(mid))
            in_rate, out_rate = price if price else (None, None)

        limits = _lookup(_LIMITS, mid)
        max_ctx, max_out = limits if isinstance(limits, tuple) else (0, 0)

        profiles[mid] = ModelProfile(
            model_id=mid,
            provider=_provider_for(mid),
            tier=tier,
            input_usd_per_1k=in_rate,
            output_usd_per_1k=out_rate,
            max_context_tokens=max_ctx,
            max_output_tokens=max_out,
        )
    return profiles


def registry_model_ids() -> List[str]:
    """Every model the engine registry declares — what this install can run."""
    import sys
    from pathlib import Path

    shared = str(Path(__file__).resolve().parents[2]
                 / "corvin_operator" / "bridges" / "shared")
    if shared not in sys.path:
        sys.path.insert(0, shared)
    import engine_models  # type: ignore

    ids: List[str] = []
    for engine in (engine_models.registry_as_dict(force_reload=True) or {}).values():
        if not isinstance(engine, dict):
            continue
        for field_name in ("os_models", "worker_models"):
            for entry in engine.get(field_name) or []:
                mid = (entry.get("id") or "").strip() if isinstance(entry, dict) else ""
                if mid and mid not in ids:
                    ids.append(mid)
    return ids


class MultiModelRouter:
    """Ranks runnable models by tier and price. Estimates, never routes."""

    def __init__(self, profiles: Optional[Dict[str, ModelProfile]] = None):
        if profiles is None:
            try:
                profiles = build_profiles(registry_model_ids())
            except Exception as exc:  # noqa: BLE001
                # An empty router is the honest answer; a hardcoded line-up is
                # the bug this module was rewritten to remove.
                logger.warning("engine registry unavailable, no profiles: %s", exc)
                profiles = {}
        self.profiles = profiles

    def rank_models(
        self,
        min_tier: ModelTier = ModelTier.FAST_CHEAP,
        max_output_usd_per_1k: Optional[float] = None,
        allow_local: bool = True,
    ) -> ModelRanking:
        """Rank models at or above ``min_tier``, cheapest capable first.

        Takes a TIER, not a ``task_type``. The previous signature took one and
        looked up ``f"{task_type}_aptitude"`` on the profile — four
        hand-written constants per model that no measurement supported. A
        caller that knows a task needs Opus-class reasoning can say so; this
        module does not pretend to know that mapping itself.

        Among models that clear the bar, OUTPUT rate decides, because output
        is the larger rate on every current model.
        """
        candidates: List[ModelProfile] = []
        unpriced: List[str] = []

        for profile in self.profiles.values():
            if profile.is_local and not allow_local:
                continue
            if profile.tier < min_tier:
                continue
            if not profile.priced:
                unpriced.append(profile.model_id)
                continue
            if (max_output_usd_per_1k is not None
                    and (profile.output_usd_per_1k or 0.0) > max_output_usd_per_1k):
                continue
            candidates.append(profile)

        # Cheapest first; ties broken by the HIGHER tier.
        candidates.sort(key=lambda p: ((p.output_usd_per_1k or 0.0), -int(p.tier)))

        ranked = [
            (p.model_id, p.output_usd_per_1k or 0.0, self._reason(p))
            for p in candidates
        ]

        if not ranked:
            return ModelRanking(
                task_tier=min_tier,
                ranked_models=[],
                recommended_model=None,
                recommended_reason=(
                    "No runnable model meets this tier and price bound. "
                    "Nothing is recommended, rather than falling back to a "
                    "model the registry may not declare."
                ),
                fallback_chain=[],
                unpriced=sorted(unpriced),
            )

        return ModelRanking(
            task_tier=min_tier,
            ranked_models=ranked,
            recommended_model=ranked[0][0],
            recommended_reason=ranked[0][2],
            fallback_chain=[m for m, _, _ in ranked[1:4]],
            unpriced=sorted(unpriced),
        )

    def _reason(self, profile: ModelProfile) -> str:
        if profile.is_local:
            return f"{profile.tier.name.lower()} - local compute, no per-token cost"
        return (
            f"{profile.tier.name.lower()} - "
            f"${(profile.input_usd_per_1k or 0) * 1000:.2f} in / "
            f"${(profile.output_usd_per_1k or 0) * 1000:.2f} out per 1M tokens"
        )

    def estimate_task_cost(
        self,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
    ) -> Optional[float]:
        """USD for a task at the published rates, or None if unpriced.

        Takes the token split and applies each rate to its own side. The old
        signature multiplied ``(input + output)`` by one averaged rate, which
        is wrong for every mix except the one the average came from, and
        returned 0.0 for an unknown model — indistinguishable from a genuinely
        free local one.
        """
        profile = self.profiles.get(model_id)
        if profile is None or not profile.priced:
            return None
        return ((input_tokens / 1000.0) * (profile.input_usd_per_1k or 0.0)
                + (output_tokens / 1000.0) * (profile.output_usd_per_1k or 0.0))


def default_router() -> MultiModelRouter:
    """Router over whatever the engine registry currently declares."""
    return MultiModelRouter()
