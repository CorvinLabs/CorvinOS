"""Engine Registry (Phase 2, Week 7).

Registers all 4 engines, tracks health, provides discovery.
"""

from __future__ import annotations

import asyncio
from typing import Optional, Dict, List

from core.engines.engine_interface import EngineType, EngineStatus, EngineInterface
from core.engines.claude_engine import ClaudeEngine
from core.engines.haiku_engine import HaikuEngine


class EngineRegistry:
    """Registry of all available engines.

    Tracks:
    - Engine availability (health checks)
    - Engine metrics (cost, latency, quality)
    - Engine selection for tasks
    """

    def __init__(self):
        self.engines: Dict[EngineType, EngineInterface] = {}
        self.health_cache: Dict[EngineType, EngineStatus] = {}
        self.last_health_check: Dict[EngineType, float] = {}

        # Initialize all engines
        self._initialize_engines()

    def _initialize_engines(self) -> None:
        """Initialize Claude Code engines only (v2.0)."""
        self.engines[EngineType.CLAUDE] = ClaudeEngine()
        self.engines[EngineType.HAIKU] = HaikuEngine()

        # Set initial health to healthy
        for engine_type in self.engines:
            self.health_cache[engine_type] = EngineStatus.HEALTHY
            self.last_health_check[engine_type] = 0

    async def health_check_all(self) -> Dict[EngineType, EngineStatus]:
        """Check health of all engines in parallel."""
        tasks = {
            engine_type: asyncio.create_task(engine.health_check())
            for engine_type, engine in self.engines.items()
        }

        results = {}
        for engine_type, task in tasks.items():
            try:
                status = await task
                self.health_cache[engine_type] = status
            except Exception:
                self.health_cache[engine_type] = EngineStatus.UNAVAILABLE
            results[engine_type] = self.health_cache[engine_type]

        return results

    def get_engine(self, engine_type: EngineType) -> Optional[EngineInterface]:
        """Get a specific engine."""
        return self.engines.get(engine_type)

    def get_all_engines(self) -> List[EngineInterface]:
        """Get all registered engines."""
        return list(self.engines.values())

    def get_healthy_engines(self) -> List[EngineType]:
        """Get all healthy engines (from cached status)."""
        return [
            engine_type for engine_type, status in self.health_cache.items()
            if status == EngineStatus.HEALTHY
        ]

    def get_engine_status(self, engine_type: EngineType) -> EngineStatus:
        """Get cached status for an engine."""
        return self.health_cache.get(engine_type, EngineStatus.UNAVAILABLE)

    def get_stats(self) -> Dict[str, Dict]:
        """Get statistics for all engines."""
        stats = {}
        for engine_type, engine in self.engines.items():
            stats[engine_type.value] = {
                **engine.get_stats(),
                "status": self.health_cache[engine_type].value,
                "capability": {
                    "max_latency_ms": engine.get_capability().max_latency_ms,
                    "max_tokens": engine.get_capability().max_tokens,
                    "cost_per_1m_input": engine.get_capability().cost_per_1m_input_tokens,
                    "cost_per_1m_output": engine.get_capability().cost_per_1m_output_tokens,
                },
            }
        return stats

    def estimate_cost(
        self,
        engine_type: EngineType,
        input_tokens: int,
        output_tokens: int,
    ) -> int:
        """Estimate cost for a task in cents."""
        engine = self.get_engine(engine_type)
        if not engine:
            return 0
        return engine.get_cost_estimate(input_tokens, output_tokens)

    def get_engine_quality_tier(self, engine_type: EngineType) -> str:
        """Get quality tier for an engine."""
        engine = self.get_engine(engine_type)
        if not engine:
            return "unknown"
        return engine.get_capability().quality_tier

