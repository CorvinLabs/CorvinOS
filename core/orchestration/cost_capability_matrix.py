"""Cost & Capability Matrix (Phase 2, Week 7).

Matrix mapping: engine × task_type → (cost, latency, quality)
Used for routing decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from core.engines.engine_interface import EngineType


@dataclass(frozen=True)
class CostCapability:
    """Cost and capability for engine + task combination."""

    engine: EngineType
    task_type: str
    cost_per_1m_tokens: int  # cents
    latency_p99_ms: int  # milliseconds
    quality_score: float  # 0.0-1.0


class CostCapabilityMatrix:
    """Matrix of engine capabilities across task types.

    Provides:
    - Cost estimation for engine + task
    - Latency expectations
    - Quality scores
    - Lookup by engine or task
    """

    def __init__(self):
        self.matrix: Dict[tuple[EngineType, str], CostCapability] = {}
        self._initialize_matrix()

    def _initialize_matrix(self) -> None:
        """Initialize with realistic cost/capability data."""
        # Claude Code only (v2.0) — Hermes and Local removed
        # Claude (best quality, slowest, most expensive)
        self._add_capability(EngineType.CLAUDE, "code_gen", 3000, 2500, 0.98)
        self._add_capability(EngineType.CLAUDE, "analysis", 3000, 2200, 0.99)
        self._add_capability(EngineType.CLAUDE, "chat", 3000, 1500, 0.96)
        self._add_capability(EngineType.CLAUDE, "research", 3000, 2800, 0.99)

        # Haiku (cheap, fast, decent quality)
        self._add_capability(EngineType.HAIKU, "code_gen", 80, 1200, 0.90)
        self._add_capability(EngineType.HAIKU, "analysis", 80, 1000, 0.88)
        self._add_capability(EngineType.HAIKU, "chat", 80, 600, 0.92)
        self._add_capability(EngineType.HAIKU, "research", 80, 1500, 0.85)

    def _add_capability(
        self,
        engine: EngineType,
        task_type: str,
        cost: int,
        latency: int,
        quality: float,
    ) -> None:
        """Add capability entry."""
        capability = CostCapability(
            engine=engine,
            task_type=task_type,
            cost_per_1m_tokens=cost,
            latency_p99_ms=latency,
            quality_score=quality,
        )
        self.matrix[(engine, task_type)] = capability

    def get_capability(
        self,
        engine: EngineType,
        task_type: str,
    ) -> Optional[CostCapability]:
        """Get cost/capability for specific engine + task."""
        return self.matrix.get((engine, task_type))

    def get_engines_for_task(self, task_type: str) -> list[EngineType]:
        """Get all engines that support a task type."""
        engines = set()
        for (engine, task), _ in self.matrix.items():
            if task == task_type:
                engines.add(engine)
        return list(engines)

    def get_best_quality_engine(self, task_type: str) -> Optional[EngineType]:
        """Get engine with highest quality for task."""
        best_engine = None
        best_quality = -1

        for engine in self.get_engines_for_task(task_type):
            capability = self.get_capability(engine, task_type)
            if capability and capability.quality_score > best_quality:
                best_engine = engine
                best_quality = capability.quality_score

        return best_engine

    def get_cheapest_engine(self, task_type: str) -> Optional[EngineType]:
        """Get cheapest engine for task."""
        cheapest_engine = None
        cheapest_cost = float('inf')

        for engine in self.get_engines_for_task(task_type):
            capability = self.get_capability(engine, task_type)
            if capability and capability.cost_per_1m_tokens < cheapest_cost:
                cheapest_engine = engine
                cheapest_cost = capability.cost_per_1m_tokens

        return cheapest_engine

    def get_fastest_engine(self, task_type: str) -> Optional[EngineType]:
        """Get fastest engine for task."""
        fastest_engine = None
        fastest_latency = float('inf')

        for engine in self.get_engines_for_task(task_type):
            capability = self.get_capability(engine, task_type)
            if capability and capability.latency_p99_ms < fastest_latency:
                fastest_engine = engine
                fastest_latency = capability.latency_p99_ms

        return fastest_engine

    def estimate_quality(
        self,
        engine: EngineType,
        task_type: str,
    ) -> float:
        """Estimate quality score for engine + task."""
        capability = self.get_capability(engine, task_type)
        return capability.quality_score if capability else 0.0

    def estimate_cost(
        self,
        engine: EngineType,
        task_type: str,
        token_count: int,
    ) -> int:
        """Estimate cost in cents for a task."""
        capability = self.get_capability(engine, task_type)
        if not capability:
            return 0
        return int((token_count / 1_000_000) * capability.cost_per_1m_tokens)

    def estimate_latency(
        self,
        engine: EngineType,
        task_type: str,
    ) -> int:
        """Estimate p99 latency in ms."""
        capability = self.get_capability(engine, task_type)
        return capability.latency_p99_ms if capability else 0

    def get_matrix_as_dict(self) -> Dict[str, Dict[str, Dict]]:
        """Export matrix as nested dict for inspection."""
        result = {}
        for engine in [EngineType.CLAUDE, EngineType.HAIKU, EngineType.HERMES, EngineType.LOCAL]:
            engine_key = engine.value
            result[engine_key] = {}
            for task_type in ["code_gen", "analysis", "chat", "research"]:
                capability = self.get_capability(engine, task_type)
                if capability:
                    result[engine_key][task_type] = {
                        "cost_per_1m": capability.cost_per_1m_tokens,
                        "latency_p99_ms": capability.latency_p99_ms,
                        "quality": capability.quality_score,
                    }
        return result
