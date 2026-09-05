"""Learning Integration: Plugin Performance Model (ADR-0314 + ADR-0612)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger(__name__)


@dataclass
class PluginPerformanceStats:
    """Stats for a plugin implementing a capability."""

    plugin_id: str
    capability_id: str
    invocations: int = 0
    successes: int = 0
    failures: int = 0
    total_latency_ms: int = 0
    slo_met_count: int = 0

    @property
    def success_rate(self) -> float:
        """Success rate (0.0–1.0)."""
        if self.invocations == 0:
            return 0.0
        return self.successes / self.invocations

    @property
    def p50_latency_ms(self) -> float:
        """Average latency."""
        if self.invocations == 0:
            return 0.0
        return self.total_latency_ms / self.invocations

    @property
    def slo_met_rate(self) -> float:
        """SLO compliance rate."""
        if self.invocations == 0:
            return 0.0
        return self.slo_met_count / self.invocations

    def observe_invocation(self, latency_ms: int, success: bool, slo_met: bool) -> None:
        """Record an invocation."""
        self.invocations += 1
        self.total_latency_ms += latency_ms
        if success:
            self.successes += 1
        else:
            self.failures += 1
        if slo_met:
            self.slo_met_count += 1


@dataclass
class PluginPerformanceModel:
    """Learned model for plugin performance."""

    skill_id: str
    tenant_id: str = "default"
    stats: dict[str, PluginPerformanceStats] = field(default_factory=dict)  # plugin_id → stats
    confidence: float = 0.0  # 0.0–1.0

    def record_outcome(
        self,
        plugin_id: str,
        capability_id: str,
        latency_ms: int,
        success: bool,
        slo_met: bool,
    ) -> None:
        """Record plugin invocation outcome."""
        key = f"{plugin_id}:{capability_id}"
        if key not in self.stats:
            self.stats[key] = PluginPerformanceStats(plugin_id, capability_id)
        self.stats[key].observe_invocation(latency_ms, success, slo_met)

        # Update confidence (simple: scale by invocation count)
        total_invocations = sum(s.invocations for s in self.stats.values())
        self.confidence = min(1.0, total_invocations / 100.0)  # Confidence at 100 invocations

    def recommend_plugin(
        self,
        allowed_plugins: list[str],
        capability_id: str,
    ) -> Optional[tuple[str, float]]:
        """
        Recommend best plugin based on learned performance.

        Returns: (plugin_id, confidence) or None if no data
        """
        best_plugin = None
        best_score = -1.0

        for plugin_id in allowed_plugins:
            key = f"{plugin_id}:{capability_id}"
            if key not in self.stats:
                continue

            stats = self.stats[key]
            # Score: weighted combination of success_rate + slo_met_rate
            score = (stats.success_rate * 0.6) + (stats.slo_met_rate * 0.4)

            if score > best_score:
                best_score = score
                best_plugin = plugin_id

        if best_plugin is None:
            return None

        return best_plugin, self.confidence

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "skill_id": self.skill_id,
            "tenant_id": self.tenant_id,
            "confidence": self.confidence,
            "stats": {
                k: {
                    "plugin_id": v.plugin_id,
                    "capability_id": v.capability_id,
                    "invocations": v.invocations,
                    "successes": v.successes,
                    "success_rate": v.success_rate,
                    "p50_latency_ms": v.p50_latency_ms,
                    "slo_met_rate": v.slo_met_rate,
                }
                for k, v in self.stats.items()
            },
        }


class OrchestrationLearner:
    """Connect orchestration outcomes to learning loop."""

    def __init__(self):
        """Initialize learner."""
        self.models: dict[str, PluginPerformanceModel] = {}

    def process_outcome(
        self,
        skill_id: str,
        plugin_id: str,
        capability_id: str,
        latency_ms: int,
        success: bool,
        slo_met: bool,
        tenant_id: str = "default",
    ) -> None:
        """Process invocation outcome."""
        key = f"{tenant_id}:{skill_id}"
        if key not in self.models:
            self.models[key] = PluginPerformanceModel(skill_id, tenant_id)

        self.models[key].record_outcome(
            plugin_id=plugin_id,
            capability_id=capability_id,
            latency_ms=latency_ms,
            success=success,
            slo_met=slo_met,
        )

    def recommend(
        self,
        skill_id: str,
        capability_id: str,
        allowed_plugins: list[str],
        tenant_id: str = "default",
    ) -> Optional[tuple[str, float]]:
        """Get plugin recommendation."""
        key = f"{tenant_id}:{skill_id}"
        model = self.models.get(key)
        if model is None:
            return None
        return model.recommend_plugin(allowed_plugins, capability_id)

    def get_model(self, skill_id: str, tenant_id: str = "default") -> Optional[PluginPerformanceModel]:
        """Get model for skill."""
        key = f"{tenant_id}:{skill_id}"
        return self.models.get(key)


# Global learner instance
_learner: Optional[OrchestrationLearner] = None


def get_learner() -> OrchestrationLearner:
    """Get or create global learner."""
    global _learner
    if _learner is None:
        _learner = OrchestrationLearner()
    return _learner
