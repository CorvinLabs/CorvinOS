"""
IntelligentRouter — Phase 2–3: Intelligent Model Routing with Cost/Latency Awareness

Implements deterministic, auditable model selection based on task complexity,
token count, latency requirements, and cost optimization.

Decision Tree (ADR-0867):
1. Extract task complexity (SIMPLE/MEDIUM/COMPLEX)
2. Apply token-based heuristics for tier selection
3. Consider latency requirements (native vs. delegated engines)
4. Apply cost constraints
5. Allow operator overrides
6. Log every decision in audit trail

Token Boundaries (definitive, revised 2026-09-20):
- SIMPLE: < 50 tokens → Haiku (one-liners: "What's 2+2?")
- MEDIUM: 50-250 tokens → Sonnet (typical requests: "Write a function to...")
- COMPLEX: >= 250 tokens → Opus (detailed: "Implement a system that...")

Cost Optimization:
- SIMPLE always uses cheapest model (Haiku)
- MEDIUM balances cost/quality (Sonnet)
- COMPLEX prioritizes quality (Opus)

Latency Awareness:
- If p99 latency critical (<100ms): use native Opus
- If latency flexible (>5 min): can use delegated engines (ACS/TDE)
- Medium is balanced: native Sonnet or delegated ACS
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import Dict, Optional, Tuple, Any

logger = logging.getLogger(__name__)


class ModelTier(Enum):
    """Model tier classification."""
    SIMPLE = "simple"      # Haiku (cost-optimized)
    MEDIUM = "medium"      # Sonnet (balanced)
    COMPLEX = "complex"    # Opus (quality-optimized)


class Engine(Enum):
    """Execution engine selection."""
    NATIVE = "native"      # Local Claude Code
    ACS = "acs"            # Cloud compute (manager/worker fan-out)
    TDE = "tde"            # Tiered delegation engine


@dataclass(frozen=True)
class RoutingDecision:
    """Immutable routing decision (audit-safe)."""
    model: str              # "claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5"
    engine: str             # "native", "acs", "tde"
    tier: str               # "simple", "medium", "complex"
    confidence: float       # 0.0-1.0
    reasoning: str          # Human-readable decision path
    signal_strength: str    # "strong", "medium", "weak"
    cost_estimate: float    # Estimated USD
    latency_estimate_ms: int  # Estimated milliseconds
    timestamp_utc: str      # ISO 8601

    def to_dict(self) -> Dict:
        """Audit-safe serialization (frozen, no PII)."""
        return asdict(self)


class IntelligentRouter:
    """Deterministic, auditable model routing with cost/latency awareness."""

    # Model mapping by tier (cost-optimized for SIMPLE, quality-optimized for COMPLEX)
    MODEL_BY_TIER = {
        ModelTier.SIMPLE: "claude-haiku-4-5",       # Fast, cheap, sufficient for simple tasks
        ModelTier.MEDIUM: "claude-sonnet-5",        # Balanced cost/quality
        ModelTier.COMPLEX: "claude-opus-5",         # Best reasoning for complex tasks
    }

    # Cost estimates per 1M input tokens (USD, 2026 pricing)
    COST_PER_1M_INPUT = {
        "claude-haiku-4-5": 0.80,
        "claude-sonnet-5": 3.00,
        "claude-opus-5": 15.00,
    }

    # Cost estimates per 1M output tokens (USD, 2026 pricing)
    COST_PER_1M_OUTPUT = {
        "claude-haiku-4-5": 4.00,
        "claude-sonnet-5": 15.00,
        "claude-opus-5": 75.00,
    }

    # Latency estimates per model + engine (milliseconds)
    LATENCY_BY_MODEL_ENGINE = {
        ("claude-haiku-4-5", "native"): 200,      # Local Haiku: 200ms
        ("claude-sonnet-5", "native"): 400,       # Local Sonnet: 400ms
        ("claude-opus-5", "native"): 800,         # Local Opus: 800ms
        ("claude-haiku-4-5", "acs"): 2000,        # Cloud Haiku: 2s
        ("claude-sonnet-5", "acs"): 3000,         # Cloud Sonnet: 3s
        ("claude-opus-5", "acs"): 5000,           # Cloud Opus: 5s
        ("claude-haiku-4-5", "tde"): 10000,       # Delegated Haiku: 10s
        ("claude-sonnet-5", "tde"): 15000,        # Delegated Sonnet: 15s
        ("claude-opus-5", "tde"): 30000,          # Delegated Opus: 30s
    }

    def __init__(self, overrides: Optional[Dict[str, Dict[str, Optional[str]]]] = None):
        """Initialize router with optional operator overrides.

        Args:
            overrides: Operator-set model overrides by tier
                      Format: {"simple": {"provider": "...", "model": "..."}, ...}
        """
        self.overrides = overrides or {}
        self.decision_history: list[RoutingDecision] = []

    def route_task(
        self,
        task_input: str,
        *,
        complexity: Optional[str] = None,
        token_count: Optional[int] = None,
        engine_mode: str = "native",
        latency_critical: bool = False,
        cost_limit_usd: float = 5.0,
        tenant_id: str = "_default",
    ) -> RoutingDecision:
        """Route a task to the optimal model.

        Deterministic algorithm:

        1. Classify complexity (SIMPLE/MEDIUM/COMPLEX) based on token count
        2. Apply operator overrides if present
        3. Select engine based on latency requirements
        4. Estimate cost and verify against budget
        5. Log decision to audit trail

        Args:
            task_input: The task description/prompt
            complexity: Pre-computed complexity level (optional)
            token_count: Token count (if known; else estimated from task_input)
            engine_mode: Operator's engine selection ("native", "acs", "tde")
            latency_critical: If True, must meet <100ms p99 latency
            cost_limit_usd: Max cost budget for this task
            tenant_id: Tenant scope (GDPR Art. 5)

        Returns:
            RoutingDecision with model, engine, confidence, reasoning
        """
        start_time = time.time()

        # Step 1: Estimate token count if not provided
        if token_count is None:
            token_count = self._estimate_tokens(task_input)

        # Step 2: Classify complexity (tier selection)
        tier, confidence, signal_strength = self._classify_tier(
            token_count, complexity, task_input=task_input
        )

        # Step 3: Select model (with operator override)
        model = self._select_model(tier, tenant_id)

        # Step 4: Select engine based on latency + availability
        engine = self._select_engine(
            model, engine_mode, latency_critical, tier
        )

        # Step 5: Estimate cost
        cost_estimate = self._estimate_cost(task_input, model)
        cost_ok = cost_estimate <= cost_limit_usd

        # Step 6: Verify cost budget (fail-safe: degrade to cheaper model on budget exceed)
        if not cost_ok and tier != ModelTier.SIMPLE:
            logger.warning(
                f"Cost {cost_estimate:.2f}USD exceeds budget {cost_limit_usd}USD; "
                f"degrading {tier.value} → simple"
            )
            tier = ModelTier.SIMPLE
            model = self.MODEL_BY_TIER[tier]
            cost_estimate = self._estimate_cost(task_input, model)
            confidence = max(0.5, confidence - 0.2)  # Lower confidence on degrade

        # Step 7: Estimate latency
        latency_ms = self.LATENCY_BY_MODEL_ENGINE.get(
            (model, engine.value), 5000
        )

        # Step 8: Build reasoning
        reasoning = self._build_reasoning(
            task_input, token_count, tier, model, engine, cost_estimate, latency_ms
        )

        # Step 9: Create immutable decision
        decision = RoutingDecision(
            model=model,
            engine=engine.value,
            tier=tier.value,
            confidence=confidence,
            reasoning=reasoning,
            signal_strength=signal_strength,
            cost_estimate=cost_estimate,
            latency_estimate_ms=latency_ms,
            timestamp_utc=self._now_iso8601(),
        )

        # Step 10: Log to audit trail
        self._audit_decision(decision, tenant_id)

        # Step 11: Track history for analytics
        self.decision_history.append(decision)

        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.info(
            f"Routed task: {tier.value} → {model} ({engine.value}) | "
            f"tokens={token_count} cost=${cost_estimate:.2f} latency={latency_ms}ms | "
            f"confidence={confidence:.2f} | routing_time={elapsed_ms}ms"
        )

        return decision

    def _classify_tier(
        self,
        token_count: int,
        pre_computed_complexity: Optional[str],
        task_input: str = "",
    ) -> Tuple[ModelTier, float, str]:
        """Classify task tier based on token count + task keywords (ADR-0867).

        Decision rules (definitive):
        - SIMPLE: < 50 tokens AND no "creation" keywords → Haiku
        - MEDIUM: 50-250 tokens OR has "write/implement/create" keywords → Sonnet
        - COMPLEX: >= 250 tokens OR complex keywords → Opus

        Pre-computed complexity (e.g., from model_selector.py) is used
        as a secondary signal for confidence adjustment.

        Returns:
            (ModelTier, confidence [0.0-1.0], signal_strength "strong"|"medium"|"weak")
        """
        # Primary: token count classification
        if token_count < 50:
            base_tier = ModelTier.SIMPLE
            base_confidence = 0.90
        elif token_count < 250:
            base_tier = ModelTier.MEDIUM
            base_confidence = 0.75
        else:
            base_tier = ModelTier.COMPLEX
            base_confidence = 0.95

        # Secondary: keyword heuristics (override base classification if needed)
        # Tasks asking to "write", "implement", "create", "develop", "analyze" etc.
        # should be at least MEDIUM even if short
        tier = base_tier
        confidence = base_confidence
        signal_strength = "strong" if token_count >= 250 else ("medium" if token_count >= 50 else "strong")

        if task_input:
            task_lower = task_input.lower()
            # Creation/Implementation keywords → bump to at least MEDIUM
            creation_keywords = ("write ", "implement ", "create ", "develop ", "build ",
                                "design ", "architect ", "refactor ", "optimize ", "improve ")
            if any(kw in task_lower for kw in creation_keywords):
                if tier == ModelTier.SIMPLE:
                    tier = ModelTier.MEDIUM
                    confidence = 0.75  # Keyword-based confidence
                    signal_strength = "medium"

            # Complex analysis keywords → bump to COMPLEX
            analysis_keywords = ("analyze ", "investigate ", "debug ", "troubleshoot ",
                                "evaluate ", "compare ", "research ", "study ", "explore ")
            if any(kw in task_lower for kw in analysis_keywords) and len(task_input) > 20:
                if tier in (ModelTier.SIMPLE, ModelTier.MEDIUM):
                    tier = ModelTier.COMPLEX
                    confidence = 0.80 if token_count < 250 else 0.95
                    signal_strength = "medium"

        # Secondary signal: pre-computed complexity (boost or lower confidence)
        if pre_computed_complexity:
            norm = pre_computed_complexity.lower()
            if norm == "simple" and tier == ModelTier.SIMPLE:
                confidence = min(1.0, confidence + 0.05)  # Agreement → boost
            elif norm == "complex" and tier == ModelTier.COMPLEX:
                confidence = min(1.0, confidence + 0.05)  # Agreement → boost
            elif norm != tier.value:
                confidence = max(0.5, confidence - 0.1)   # Disagreement → lower

        return tier, confidence, signal_strength

    def _select_model(self, tier: ModelTier, tenant_id: str) -> str:
        """Select model for tier, respecting operator overrides.

        Operator overrides (set via console) take absolute precedence:
        - saved preference stored in corvin_config.yaml
        - consulted BEFORE the tier-based default rule
        """
        # Check for operator override on this tier
        tier_key = tier.value  # "simple", "medium", "complex"
        if tier_key in self.overrides:
            override = self.overrides[tier_key]
            if override.get("model"):
                logger.debug(
                    f"Using operator override for {tier_key}: "
                    f"{override['model']}"
                )
                return override["model"]

        # No override → use tier-based default
        return self.MODEL_BY_TIER[tier]

    def _select_engine(
        self,
        model: str,
        engine_mode: str,
        latency_critical: bool,
        tier: ModelTier,
    ) -> Engine:
        """Select execution engine based on latency + availability.

        Rules (deterministic):
        1. If latency_critical (p99 < 100ms):
           → NATIVE (local Opus/Sonnet/Haiku)
        2. Else if engine_mode == "native":
           → NATIVE (operator selected local-only)
        3. Else if engine_mode == "acs":
           → ACS (manager/worker fan-out, good for big-data)
        4. Else if engine_mode == "tde":
           → TDE (tiered delegation, for long-running tasks)
        5. Default → NATIVE (fail-safe)
        """
        if latency_critical:
            return Engine.NATIVE  # Must be under 100ms

        if engine_mode == "acs":
            return Engine.ACS
        elif engine_mode == "tde":
            return Engine.TDE
        else:
            return Engine.NATIVE  # Default safe choice

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count from text length.

        Simple heuristic: ~4 characters per token (Claude standard).
        Refined for code (token density higher) and prose (lower).
        """
        char_count = len(text)

        # Heuristic adjustments
        code_ratio = text.count("\n") / max(1, len(text) / 80)  # lines per 80 chars
        if code_ratio > 0.5:  # Looks like code
            return max(10, int(char_count / 3))  # Higher token density
        else:  # Prose
            return max(10, int(char_count / 4))  # Standard ratio

    def _estimate_cost(self, task_input: str, model: str) -> float:
        """Estimate cost for a task (input + expected output).

        Assumes:
        - Input: full task_input token count
        - Output: ~1.5x the input (typical for reasoning)
        """
        input_tokens = self._estimate_tokens(task_input)
        output_tokens = int(input_tokens * 1.5)  # Typical output ratio

        # Cost = (input_cost + output_cost) / 1M tokens
        cost_per_1m_in = self.COST_PER_1M_INPUT.get(model, 15.0)
        cost_per_1m_out = self.COST_PER_1M_OUTPUT.get(model, 75.0)

        input_cost = (input_tokens / 1_000_000) * cost_per_1m_in
        output_cost = (output_tokens / 1_000_000) * cost_per_1m_out

        return input_cost + output_cost

    def _build_reasoning(
        self,
        task_input: str,
        token_count: int,
        tier: ModelTier,
        model: str,
        engine: Engine,
        cost_estimate: float,
        latency_ms: int,
    ) -> str:
        """Build human-readable reasoning string."""
        reasons = [
            f"Token count: {token_count} ({tier.value.upper()})",
            f"Model: {model}",
            f"Engine: {engine.value}",
            f"Est. cost: ${cost_estimate:.2f}",
            f"Est. latency: {latency_ms}ms",
        ]

        # Add decision heuristics
        if token_count < 500:
            reasons.append("Cost-optimized (SIMPLE task)")
        elif token_count < 3000:
            reasons.append("Balanced cost/quality (MEDIUM task)")
        else:
            reasons.append("Quality-prioritized (COMPLEX task)")

        # Engine rationale
        if engine == Engine.NATIVE:
            reasons.append("Native execution (low latency)")
        elif engine == Engine.ACS:
            reasons.append("Delegated to ACS (parallelizable work)")
        else:
            reasons.append("Tiered delegation (long-running work)")

        return " | ".join(reasons)

    def _audit_decision(self, decision: RoutingDecision, tenant_id: str) -> None:
        """Log routing decision to audit trail (best-effort, non-blocking)."""
        try:
            from audit import audit_event  # noqa: PLC0415
            audit_event(
                "routing_decision",
                details={
                    "model": decision.model,
                    "engine": decision.engine,
                    "tier": decision.tier,
                    "confidence": decision.confidence,
                    "signal_strength": decision.signal_strength,
                    "cost_estimate_usd": decision.cost_estimate,
                    "latency_estimate_ms": decision.latency_estimate_ms,
                    "reasoning": decision.reasoning,
                },
                tenant_id=tenant_id,
            )
        except Exception as e:
            logger.debug(f"Audit event skipped (audit unavailable): {type(e).__name__}")

    @staticmethod
    def _now_iso8601() -> str:
        """Current timestamp in ISO 8601 format (UTC)."""
        return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

    def get_stats(self) -> Dict[str, Any]:
        """Get routing statistics from decision history."""
        if not self.decision_history:
            return {"decisions": 0}

        tiers = [d.tier for d in self.decision_history]
        models = [d.model for d in self.decision_history]
        engines = [d.engine for d in self.decision_history]

        return {
            "total_decisions": len(self.decision_history),
            "tier_distribution": {
                "simple": tiers.count("simple"),
                "medium": tiers.count("medium"),
                "complex": tiers.count("complex"),
            },
            "model_distribution": {
                "claude-haiku-4-5": models.count("claude-haiku-4-5"),
                "claude-sonnet-5": models.count("claude-sonnet-5"),
                "claude-opus-5": models.count("claude-opus-5"),
            },
            "engine_distribution": {
                "native": engines.count("native"),
                "acs": engines.count("acs"),
                "tde": engines.count("tde"),
            },
            "avg_confidence": sum(d.confidence for d in self.decision_history) / len(self.decision_history),
            "avg_cost_usd": sum(d.cost_estimate for d in self.decision_history) / len(self.decision_history),
            "avg_latency_ms": sum(d.latency_estimate_ms for d in self.decision_history) / len(self.decision_history),
        }
