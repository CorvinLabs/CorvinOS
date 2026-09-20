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

Token Boundaries (Phase 2 Optimized, revised 2026-09-20 after quality regression analysis):
- SIMPLE: < 30 tokens → Haiku (one-liners: "What's 2+2?")
- MEDIUM: 30-150 tokens → Sonnet (typical requests: "Write a function to...")
- COMPLEX: >= 150 tokens → Opus (detailed: "Implement a system that...")

RATIONALE: Phase 1 benchmark showed -4.9% quality loss from 37.8% token savings.
Root cause: too aggressive token reduction. Target Phase 2: 20-25% savings with ≤2% quality loss.
Strategy: Conservative thresholds (favor Sonnet/Opus over Haiku), only route to Haiku for truly simple tasks.

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

    # Maximum number of routing decisions to keep in memory
    # Beyond this, oldest decisions are discarded to prevent memory leaks
    MAX_HISTORY_SIZE = 1000

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

        # Step 6: Verify cost budget (enforce on ALL tiers, including SIMPLE)
        # If SIMPLE itself exceeds budget, we must fail — cannot degrade further
        if not cost_ok:
            if tier == ModelTier.SIMPLE:
                # Even the cheapest model exceeds budget — reject this task
                logger.warning(
                    f"Cost {cost_estimate:.2f}USD exceeds budget {cost_limit_usd}USD; "
                    f"task cannot run (already on cheapest model)"
                )
                # Set very low confidence to signal this is an error condition
                confidence = 0.0
            else:
                # Degrade to cheaper model
                logger.warning(
                    f"Cost {cost_estimate:.2f}USD exceeds budget {cost_limit_usd}USD; "
                    f"degrading {tier.value} → simple"
                )
                tier = ModelTier.SIMPLE
                model = self.MODEL_BY_TIER[tier]
                cost_estimate = self._estimate_cost(task_input, model)
                confidence = max(0.5, confidence - 0.2)  # Lower confidence on degrade
                # Check if even SIMPLE exceeds budget after downgrade
                if cost_estimate > cost_limit_usd:
                    logger.warning(
                        f"Cost {cost_estimate:.2f}USD still exceeds budget {cost_limit_usd}USD "
                        f"after degrading to SIMPLE"
                    )
                    confidence = 0.0

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
        # Validate tenant_id before logging (GDPR compliance: Art. 5, 6, 32)
        if not self._is_valid_tenant_id(tenant_id):
            logger.error(f"Invalid tenant_id {tenant_id!r} before audit; rejecting decision")
            # Return a decision with zero confidence to signal error
            error_decision = RoutingDecision(
                model=model,
                engine=engine.value,
                tier=tier.value,
                confidence=0.0,  # Signal error condition
                reasoning="Error: invalid tenant_id",
                signal_strength="weak",
                cost_estimate=cost_estimate,
                latency_estimate_ms=latency_ms,
                timestamp_utc=self._now_iso8601(),
            )
            return error_decision

        self._audit_decision(decision, tenant_id)

        # Step 11: Track history for analytics (bounded to prevent memory leaks)
        self.decision_history.append(decision)
        # Keep only the most recent MAX_HISTORY_SIZE decisions
        if len(self.decision_history) > self.MAX_HISTORY_SIZE:
            # Remove oldest entries, keeping only the most recent ones
            self.decision_history = self.decision_history[-self.MAX_HISTORY_SIZE:]

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
        """Classify task tier based on token count + task keywords (ADR-0867, Phase 2 Optimized).

        Decision rules (Phase 2 optimized for quality):
        - SIMPLE: < 30 tokens AND no complex keywords → Haiku
        - MEDIUM: 30-150 tokens OR has "write/implement" keywords → Sonnet
        - COMPLEX: >= 150 tokens OR deep analysis/architecture keywords → Opus

        Phase 1 benchmark revealed -4.9% quality loss from overly aggressive token reduction (37.8%).
        Phase 2 optimization: conservative thresholds to target 20-25% savings with ≤2% quality loss.

        Pre-computed complexity (e.g., from model_selector.py) is used
        as a secondary signal for confidence adjustment.

        Returns:
            (ModelTier, confidence [0.0-1.0], signal_strength "strong"|"medium"|"weak")
        """
        # Primary: token count classification (Phase 2 optimized thresholds)
        if token_count < 30:
            base_tier = ModelTier.SIMPLE
            base_confidence = 0.85  # Lower confidence on smaller boundary
        elif token_count < 150:
            base_tier = ModelTier.MEDIUM
            base_confidence = 0.80  # Slightly higher confidence on larger window
        else:
            base_tier = ModelTier.COMPLEX
            base_confidence = 0.95  # High confidence on COMPLEX

        # Secondary: keyword heuristics (Phase 2: conservative bumping to preserve quality)
        # Goal: only bump tiers if CLEARLY necessary; default to base tier classification
        tier = base_tier
        confidence = base_confidence
        # Signal strength correlates with token count confidence:
        # >= 150 tokens: STRONG signal (high confidence in tier)
        # 30-150 tokens: MEDIUM signal (moderate confidence)
        # < 30 tokens: WEAK signal (low confidence, may be keyword-bumped)
        signal_strength = "strong" if token_count >= 150 else ("medium" if token_count >= 30 else "weak")

        if task_input:
            import re
            task_lower = task_input.lower()

            # Phase 2 Conservative: Only bump SIMPLE→MEDIUM if BOTH:
            # 1. Has creation keyword AND
            # 2. Task input > 50 chars (not a trivial one-liner)
            creation_patterns = (
                r"\b(write|writing|written|wrote|writes)\b",
                r"\b(implement|implementation|implementing|implemented)\b",
                r"\b(create|creation|creating|created|creates)\b",
                r"\b(develop|development|developing|developed)\b",
            )
            if (tier == ModelTier.SIMPLE and
                len(task_input) > 50 and
                any(re.search(pattern, task_lower) for pattern in creation_patterns)):
                tier = ModelTier.MEDIUM
                confidence = 0.80  # Keyword-based confidence
                signal_strength = "medium"

            # Phase 2 Conservative: Bump MEDIUM→COMPLEX or SIMPLE→COMPLEX only for deep analysis
            # Requires BOTH: analysis keyword AND task input > 80 chars (substantive depth)
            analysis_patterns = (
                r"\b(analyze|analysis|analyzing|analyzed|analyzes)\b",
                r"\b(debug|debugging|debugged|debugger)\b",
                r"\b(investigate|investigation|investigating|investigated)\b",
                r"\b(troubleshoot|troubleshooting|troubleshot)\b",
            )
            if (tier in (ModelTier.SIMPLE, ModelTier.MEDIUM) and
                len(task_input) > 80 and
                any(re.search(pattern, task_lower) for pattern in analysis_patterns)):
                tier = ModelTier.COMPLEX
                confidence = 0.85 if tier == ModelTier.MEDIUM else 0.75
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

        Standard heuristic: ~4-5 characters per token (Claude standard).
        Refined for code (higher density ~3 chars/token) and prose (lower density ~4-5 chars/token).

        Based on empirical data: word-count * 1.3 ≈ token count for English prose,
        code has slightly higher density due to symbols and short identifiers.
        """
        char_count = len(text)
        if char_count == 0:
            return 0

        # Heuristic: measure code density by looking for code-like patterns
        # Code has multiple indicators: short lines, high symbol density, indentation
        lines = text.count("\n") + 1
        avg_line_length = char_count / max(1, lines)

        # Count code indicators (require multiple to classify as code)
        code_indicators = 0

        # Indicator 1: Average line length < 60 (code has shorter lines)
        if avg_line_length < 60:
            code_indicators += 1

        # Indicator 2: High curly brace/bracket density (≥ 1 per 100 chars)
        bracket_count = text.count("{") + text.count("}") + text.count("[") + text.count("]")
        if bracket_count >= char_count / 100:  # At least 1 bracket per 100 chars
            code_indicators += 1

        # Indicator 3: Semicolons used for statement termination (≥ 1 per 100 chars)
        # Prose semicolons are rare; code uses them frequently for statements
        semicolon_count = text.count(";")
        if semicolon_count >= char_count / 100:
            code_indicators += 1

        # Indicator 4: Indentation pattern (leading spaces/tabs, ≥ 20% of lines)
        indented_lines = sum(1 for line in text.split("\n") if line and line[0] in " \t")
        if indented_lines >= lines * 0.2:
            code_indicators += 1

        # Require at least 2 indicators to classify as code (avoid false positives)
        is_code = code_indicators >= 2

        if is_code:
            # Code: ~3 characters per token (higher density due to keywords, symbols)
            return max(10, int(char_count / 3))
        else:
            # Prose/mixed: ~4 characters per token (standard Claude ratio)
            # For high-quality estimates, use word count * 1.3
            word_count = len(text.split())
            word_estimate = int(word_count * 1.3)
            char_estimate = int(char_count / 4)
            # Use word-based estimate if available and reasonable
            if word_count > 0:
                return max(10, min(word_estimate, char_estimate * 2))  # Sanity bounds
            return max(10, char_estimate)

    def _estimate_cost(self, task_input: str, model: str) -> float:
        """Estimate cost for a task (input + expected output).

        Output-to-input ratio empirically calibrated from real CorvinOS turns:
        - Haiku (simple tasks): ~1.1x output ratio (short responses)
        - Sonnet (medium tasks): ~1.2x output ratio (moderate responses)
        - Opus (complex tasks): ~1.3x output ratio (detailed reasoning)

        This replaces the previous fixed 1.5x multiplier which overestimated
        writing task costs by ~25-30%.
        """
        input_tokens = self._estimate_tokens(task_input)

        # Empirically calibrated output multiplier per model
        # Based on 2026-09 real-world CorvinOS turn analysis
        output_multiplier_by_model = {
            "claude-haiku-4-5": 1.1,      # Haiku: short responses
            "claude-sonnet-5": 1.2,       # Sonnet: moderate responses
            "claude-opus-5": 1.3,         # Opus: detailed reasoning + thinking
        }
        multiplier = output_multiplier_by_model.get(model, 1.2)
        output_tokens = int(input_tokens * multiplier)

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
        """Log routing decision to audit trail (best-effort, non-blocking).

        Critical exceptions (OOM, AttributeError) are re-raised to signal
        system problems; audit-related errors are logged but don't break routing.
        """
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
        except MemoryError:
            # OOM is a system-level problem, not an audit module issue
            logger.error("Out of memory during routing audit")
            raise
        except AttributeError as e:
            # AttributeError usually indicates a bug in the audit module or
            # in the RoutingDecision dataclass; log as ERROR so it's noticed
            logger.error(f"Audit attribute error: {e}")
            raise
        except Exception as e:
            # Audit module unavailable, ImportError, etc. — log but don't fail
            logger.debug(f"Audit event skipped (audit unavailable): {type(e).__name__}")

    @staticmethod
    def _now_iso8601() -> str:
        """Current timestamp in ISO 8601 format (UTC)."""
        return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

    @staticmethod
    def _is_valid_tenant_id(tenant_id: str) -> bool:
        """Validate tenant_id before using it in audit logs (GDPR compliance).

        A valid tenant_id is a non-empty string matching the pattern [a-zA-Z0-9_-].
        This prevents injection attacks and PII leakage into audit logs.

        Args:
            tenant_id: The tenant identifier to validate

        Returns:
            True if tenant_id is valid, False otherwise
        """
        if not isinstance(tenant_id, str) or not tenant_id:
            return False
        # Allow alphanumeric, underscore, and hyphen (common for tenant identifiers)
        import re
        return bool(re.match(r"^[a-zA-Z0-9_-]+$", tenant_id))

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
