"""
Dual Plugin Architecture: Deterministic + LLM-Driven

ADR-0513: Plugin Taxonomy (Deterministic vs. LLM-Driven)

Two plugin types co-exist:
1. Deterministic Plugins — Fast (<1ms), predictable, critical path
2. LLM-Driven Plugins — Smart (100-500ms), adaptive, decision-making
"""

from abc import ABC, abstractmethod
from enum import Enum
from dataclasses import dataclass
from typing import Any, Optional, Dict


class PluginType(str, Enum):
    """Plugin execution model."""
    DETERMINISTIC = "deterministic"  # Pure Python, <1ms, no LLM
    LLM_DRIVEN = "llm_driven"         # LLM-based reasoning, 100-500ms, adaptive


class PluginTier(str, Enum):
    """Plugin capability tier."""
    CRITICAL = "critical"        # Real-time critical (audit, security, core)
    HIGH = "high"               # Important but tolerant of latency
    GENERAL = "general"         # Informational, observability
    EXPERIMENTAL = "experimental"  # Research, optional


@dataclass
class PluginCapabilities:
    """Declare plugin capabilities & constraints."""
    plugin_type: PluginType
    tier: PluginTier
    max_latency_ms: int  # SLA: max execution time
    requires_llm: bool = False
    can_call_other_plugins: bool = False
    learnable: bool = False  # Can improve via skill grading

    def __post_init__(self):
        """Validate constraints."""
        if self.plugin_type == PluginType.DETERMINISTIC:
            assert self.max_latency_ms < 10, "Deterministic must be <10ms"
            assert not self.requires_llm, "Deterministic cannot require LLM"

        if self.plugin_type == PluginType.LLM_DRIVEN:
            assert self.max_latency_ms >= 100, "LLM must allow 100+ms"
            assert self.requires_llm, "LLM-driven must declare LLM requirement"


class CorvinPluginBase(ABC):
    """Base class for both plugin types."""

    @property
    @abstractmethod
    def capabilities(self) -> PluginCapabilities:
        """Declare plugin type & constraints."""
        pass

    @property
    def plugin_type(self) -> PluginType:
        """Shorthand for capabilities.plugin_type."""
        return self.capabilities.plugin_type

    @property
    def is_deterministic(self) -> bool:
        """Is this a fast deterministic plugin?"""
        return self.plugin_type == PluginType.DETERMINISTIC

    @property
    def is_llm_driven(self) -> bool:
        """Is this an LLM-based adaptive plugin?"""
        return self.plugin_type == PluginType.LLM_DRIVEN

    async def initialize(self, context):
        """Initialize plugin."""
        pass

    async def on_health_check(self):
        """All plugins must implement health check."""
        from .protocol import HealthStatus
        return HealthStatus(ok=True, message="operational")

    async def shutdown(self):
        """Graceful shutdown."""
        pass


class DeterministicPlugin(CorvinPluginBase):
    """
    Fast, predictable, no LLM.

    Use for:
    - Real-time critical functions
    - Security gates
    - Audit chains
    - Performance-sensitive monitoring
    """

    @property
    def capabilities(self) -> PluginCapabilities:
        return PluginCapabilities(
            plugin_type=PluginType.DETERMINISTIC,
            tier=self.get_tier(),
            max_latency_ms=self.get_max_latency_ms(),
        )

    def get_tier(self) -> PluginTier:
        """Override in subclass."""
        return PluginTier.GENERAL

    def get_max_latency_ms(self) -> int:
        """Override in subclass."""
        return 1  # Default: sub-millisecond


class LLMDrivenPlugin(CorvinPluginBase):
    """
    Intelligent, adaptive, LLM-based.

    Use for:
    - Intelligent decision making
    - Error analysis & healing
    - Optimization
    - Self-learning components

    Requires:
    - LLM backend (Claude API)
    - Skill 2.0 grading loop
    - Latency tolerance (100-500ms)
    """

    def __init__(self, llm_backend=None, skill_engine=None):
        """Initialize LLM-driven plugin."""
        self.llm_backend = llm_backend  # LLM agent executor
        self.skill_engine = skill_engine  # Skill grading & learning
        self.reasoning_history = []  # Track decisions for learning

    @property
    def capabilities(self) -> PluginCapabilities:
        return PluginCapabilities(
            plugin_type=PluginType.LLM_DRIVEN,
            tier=self.get_tier(),
            max_latency_ms=self.get_max_latency_ms(),
            requires_llm=True,
            can_call_other_plugins=True,
            learnable=True,
        )

    def get_tier(self) -> PluginTier:
        """Override in subclass."""
        return PluginTier.HIGH

    def get_max_latency_ms(self) -> int:
        """Override in subclass."""
        return 300  # Default: 300ms (LLM round-trip)

    async def reason(self, prompt: str, tools: Optional[Dict[str, Any]] = None):
        """
        Use LLM to reason about a problem.

        Args:
            prompt: What to reason about
            tools: Available tools/plugins to call

        Returns:
            LLM reasoning result with decision & confidence
        """
        if not self.llm_backend:
            raise RuntimeError("LLM backend not configured")

        decision = await self.llm_backend.invoke(
            model="claude-opus-5",
            prompt=prompt,
            tools=tools or {},
        )

        # Track for learning
        self.reasoning_history.append({
            "prompt": prompt,
            "decision": decision,
            "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
        })

        return decision

    async def learn_from_outcome(self, decision_id: str, outcome_score: float, feedback: str):
        """
        Record learning signal (from Skill 2.0 grading).

        Args:
            decision_id: Which decision this grades
            outcome_score: 0.0-1.0 score
            feedback: Human/system feedback
        """
        if self.skill_engine:
            await self.skill_engine.grade(
                skill_id=self.plugin_id,
                decision_id=decision_id,
                score=outcome_score,
                feedback=feedback,
            )


# ── Orchestration Rules ────────────────────────────────────────────────────

class PluginOrchestrationRules:
    """
    Rules for how plugins are orchestrated based on type.

    DETERMINISTIC:
      - Always on critical path
      - <1ms SLA enforced
      - Circuit breaker strict (fail = drop plugin)
      - No retries (fail fast)

    LLM_DRIVEN:
      - Off critical path
      - 100-500ms SLA enforced
      - Circuit breaker lenient (fail = log & continue)
      - Retries allowed (timeout + fallback)
      - Async execution (don't block)
    """

    @staticmethod
    def should_enforce_latency(plugin: CorvinPluginBase) -> bool:
        """Enforce latency SLA strictly?"""
        if plugin.is_deterministic:
            return True  # Hard deadline
        if plugin.is_llm_driven:
            return False  # Soft deadline (no hard block)
        return False

    @staticmethod
    def on_plugin_timeout(plugin: CorvinPluginBase, elapsed_ms: int):
        """What to do when plugin times out."""
        if plugin.is_deterministic:
            # Critical: circuit breaker trip immediately
            plugin.circuit_breaker.trip()
            raise TimeoutError(f"{plugin.id} exceeded {plugin.capabilities.max_latency_ms}ms")

        if plugin.is_llm_driven:
            # Non-critical: log & continue
            import logging
            logging.warning(
                f"LLM plugin {plugin.id} slow: {elapsed_ms}ms "
                f"(SLA: {plugin.capabilities.max_latency_ms}ms)"
            )
            # Don't trip breaker, let it retry

    @staticmethod
    def should_retry(plugin: CorvinPluginBase, attempt: int) -> bool:
        """Should we retry a failed plugin?"""
        if plugin.is_deterministic:
            return False  # Fail fast
        if plugin.is_llm_driven and attempt < 2:
            return True  # Retry LLM calls (transient failures common)
        return False


"""
USAGE:

# Deterministic plugin (fast, critical)
class VibHealthMonitor(DeterministicPlugin):
    def get_tier(self):
        return PluginTier.HIGH

    def get_max_latency_ms(self):
        return 1  # Sub-ms

    async def on_brain_metric(self, metric):
        # Pure Python, must be <1ms
        if metric.latency > THRESHOLD:
            await self.alert()

# LLM-Driven plugin (smart, adaptive)
class IntelligentErrorHealer(LLMDrivenPlugin):
    def get_tier(self):
        return PluginTier.HIGH

    def get_max_latency_ms(self):
        return 500  # Allow 500ms for LLM

    async def on_error(self, error):
        # Use LLM to reason about error
        decision = await self.reason(
            f"How should we handle this error? {error}"
        )
        await self.execute_healing(decision)

        # Learn from outcome
        outcome = await self.monitor_outcome()
        await self.learn_from_outcome(
            decision_id=decision.id,
            outcome_score=outcome.score,
            feedback=outcome.feedback
        )
"""
