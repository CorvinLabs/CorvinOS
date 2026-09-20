"""
Integration of IntelligentRouter with delegation_policy and model selection.

Provides bridge functions to use IntelligentRouter's routing decisions in the
actual task execution path (delegation_policy.py + model selection).
"""

import logging
from typing import Optional, Dict, Any, Tuple

from .intelligent_router import IntelligentRouter, RoutingDecision

logger = logging.getLogger(__name__)

# ADR-0867 Phase 3 — per-tenant routing strategy (spec.routing.strategy).
# Fail-soft on import: tenant_config lives in corvin_gateway, which is not
# guaranteed to be importable from every host that loads os_skills (e.g. a
# standalone skill-forge worker). When unavailable, every tenant behaves as
# "phase2_conservative" — the pre-ADR-0867-Phase-3 default — never as an
# unconfigured opt-in to the judge path.
try:
    from core.gateway.corvin_gateway.tenant_config import load_or_default as _load_tenant_config
except ImportError:  # pragma: no cover — exercised only on hosts without corvin_gateway
    _load_tenant_config = None
    logger.debug("corvin_gateway.tenant_config unavailable; routing strategy fixed at phase2_conservative")


def _routing_strategy_for_tenant(tenant_id: str) -> tuple[str, bool]:
    """Resolve (strategy, judge_enabled) for *tenant_id* from tenant.corvin.yaml.

    Fails open to the pre-Phase-3 default ("phase2_conservative", judge
    disabled) on any load error — a malformed/missing tenant config must
    degrade routing quality, never break it.
    """
    if _load_tenant_config is None:
        return "phase2_conservative", False
    try:
        cfg = _load_tenant_config(tenant_id)
        routing = cfg.spec.routing
        return routing.strategy, routing.judge_enabled
    except Exception as e:  # noqa: BLE001 — config load must never break routing
        logger.warning(f"tenant routing config load failed for {tenant_id!r}: {e}; using phase2_conservative")
        return "phase2_conservative", False


class IntelligentRouterBridge:
    """Singleton bridge to integrate IntelligentRouter with existing routing."""

    _instance: Optional["IntelligentRouterBridge"] = None
    _router: Optional[IntelligentRouter] = None

    def __new__(cls) -> "IntelligentRouterBridge":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._router = IntelligentRouter()
        return cls._instance

    @classmethod
    def get_router(cls) -> IntelligentRouter:
        """Get the singleton IntelligentRouter instance."""
        bridge = cls()
        return bridge._router

    @classmethod
    def route_with_intelligent_selection(
        cls,
        task_input: str,
        *,
        complexity: Optional[str] = None,
        token_count: Optional[int] = None,
        engine_mode: str = "native",
        latency_critical: bool = False,
        cost_limit_usd: float = 5.0,
        tenant_id: str = "_default",
    ) -> RoutingDecision:
        """Route a task using IntelligentRouter.

        This is the PUBLIC API for model selection integration.

        Args:
            task_input: Task description or prompt
            complexity: Pre-computed complexity (optional)
            token_count: Token count (optional; estimated if not provided)
            engine_mode: Operator's engine selection ("native", "acs", "tde")
            latency_critical: If True, must meet <100ms p99 latency
            cost_limit_usd: Max cost budget
            tenant_id: Tenant scope (GDPR Art. 5)

        Returns:
            RoutingDecision with selected model, engine, reasoning
        """
        router = cls.get_router()
        strategy, judge_enabled = _routing_strategy_for_tenant(tenant_id)

        if strategy == "phase3_judge" and judge_enabled:
            return router.route_with_judge(
                task_input,
                complexity=complexity,
                token_count=token_count,
                engine_mode=engine_mode,
                latency_critical=latency_critical,
                cost_limit_usd=cost_limit_usd,
                tenant_id=tenant_id,
            )

        # "phase2_conservative" (default) and "baseline" both resolve here;
        # "phase3_judge" with judge_enabled=False also falls back here (the
        # operator kill-switch documented on RoutingConfig).
        return router.route_task(
            task_input,
            complexity=complexity,
            token_count=token_count,
            engine_mode=engine_mode,
            latency_critical=latency_critical,
            cost_limit_usd=cost_limit_usd,
            tenant_id=tenant_id,
        )

    @classmethod
    def get_model_for_task(
        cls,
        task_input: str,
        *,
        token_count: Optional[int] = None,
        tenant_id: str = "_default",
    ) -> str:
        """Get recommended model for a task (convenience wrapper).

        Returns the full model ID (e.g., "claude-haiku-4-5").

        Args:
            task_input: Task description
            token_count: Token count (optional)
            tenant_id: Tenant scope

        Returns:
            Model ID string (e.g., "claude-opus-5")
        """
        decision = cls.route_with_intelligent_selection(
            task_input,
            token_count=token_count,
            tenant_id=tenant_id,
        )
        return decision.model

    @classmethod
    def get_stats(cls) -> Dict[str, Any]:
        """Get router statistics."""
        router = cls.get_router()
        return router.get_stats()

    @classmethod
    def reset_router(cls) -> None:
        """Reset the singleton router (for testing only)."""
        cls._instance = None
        cls._router = None


def intelligent_model_selection(
    task_input: str,
    *,
    complexity: Optional[str] = None,
    token_count: Optional[int] = None,
    tenant_id: str = "_default",
) -> Tuple[str, str, float]:
    """Select model using IntelligentRouter (function interface).

    Convenience function for code that prefers a direct function call
    over class-based routing.

    Returns:
        (model_id, reasoning, confidence)
    """
    decision = IntelligentRouterBridge.route_with_intelligent_selection(
        task_input,
        complexity=complexity,
        token_count=token_count,
        tenant_id=tenant_id,
    )

    return decision.model, decision.reasoning, decision.confidence


# ─────────────────────────────────────────────────────────────────────────────
# Integration hooks for delegation_policy.py
# ─────────────────────────────────────────────────────────────────────────────


def inject_intelligent_model_selection(
    task_input: str,
    current_engine: str,
    *,
    is_big_data: bool = False,
    force_delegate: bool = False,
    tenant_id: str = "_default",
) -> str:
    """Determine model to use based on task characteristics.

    This function is meant to be called FROM delegation_policy.py
    to enhance the engine selection with intelligent model routing.

    It answers the question: "Given that we're using ENGINE X,
    which MODEL should we instantiate?"

    Args:
        task_input: The task/prompt
        current_engine: The engine already selected ("native", "acs", "tde")
        is_big_data: Whether this is big-data-shaped work
        force_delegate: Whether user forced delegation
        tenant_id: Tenant scope

    Returns:
        Model ID to use (e.g., "claude-opus-5")
    """
    # Determine cost/latency constraints based on engine
    latency_critical = current_engine == "native"
    cost_limit = 0.50 if current_engine == "native" else 5.0

    # If big-data: cost optimization is secondary to completion
    if is_big_data:
        cost_limit = 10.0

    # If user forced delegation: prioritize quality over cost
    if force_delegate:
        cost_limit = 15.0

    decision = IntelligentRouterBridge.route_with_intelligent_selection(
        task_input,
        engine_mode=current_engine,
        latency_critical=latency_critical,
        cost_limit_usd=cost_limit,
        tenant_id=tenant_id,
    )

    logger.debug(
        f"Intelligent model selection: {decision.model} "
        f"(tier={decision.tier}, confidence={decision.confidence:.2f})"
    )

    return decision.model
