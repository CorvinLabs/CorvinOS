"""Unified EngineInterface for all compute engines (Phase 0, updated v2.0).

Abstract base class defining the contract for all engines:
- Claude, Haiku, Opus, Sonnet
(Hermes and Local removed in v2.0 — Claude Code only)
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional
from datetime import datetime


class EngineType(str, Enum):
    """Supported engine types (Claude Code only in v2.0)."""

    CLAUDE = "claude"
    OPUS = "claude-opus-5"
    SONNET = "claude-sonnet-4"
    HAIKU = "claude-haiku"


class EngineStatus(str, Enum):
    """Engine operational status."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    TIMEOUT = "timeout"
    ERROR = "error"
    UNAVAILABLE = "unavailable"


@dataclass
class EngineCapability:
    """Engine capability profile."""

    engine_type: EngineType
    max_latency_ms: int
    max_tokens: int
    cost_per_1m_input_tokens: int  # in cents
    cost_per_1m_output_tokens: int  # in cents
    supports_streaming: bool = False
    supports_vision: bool = False
    supports_tool_use: bool = True
    quality_tier: str = "standard"  # "standard", "optimized", "premium"


@dataclass
class EngineRequest:
    """Request to execute a task on an engine."""

    task_id: str
    task_type: str
    prompt: str
    system_prompt: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 2048
    timeout_ms: int = 5000
    metadata: dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class EngineResponse:
    """Response from engine execution."""

    task_id: str
    engine_type: EngineType
    success: bool
    output: Optional[str] = None
    tokens_input: int = 0
    tokens_output: int = 0
    latency_ms: int = 0
    cost_cents: int = 0
    error: Optional[str] = None
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()


class EngineInterface(ABC):
    """Abstract base class for all compute engines.

    Implementations must provide:
    1. Health checking (is_healthy, get_status)
    2. Task execution (execute, execute_streaming)
    3. Capability reporting (get_capability, get_max_latency)
    4. Graceful degradation (fallback chains, quality modes)
    """

    def __init__(self, engine_type: EngineType):
        self.engine_type = engine_type
        self._status = EngineStatus.HEALTHY
        self._last_error = None
        self._call_count = 0
        self._error_count = 0
        self._total_latency_ms = 0

    @abstractmethod
    async def execute(self, request: EngineRequest) -> EngineResponse:
        """Execute task synchronously.

        Args:
            request: EngineRequest with task details

        Returns:
            EngineResponse with result or error
        """
        pass

    @abstractmethod
    async def execute_streaming(self, request: EngineRequest) -> Any:
        """Execute task with streaming response.

        Yields tokens as they arrive for real-time feedback.
        """
        pass

    @abstractmethod
    def get_capability(self) -> EngineCapability:
        """Get engine capability profile."""
        pass

    @abstractmethod
    async def health_check(self) -> EngineStatus:
        """Quick health check (ping, version query, etc.)."""
        pass

    async def is_healthy(self) -> bool:
        """Check if engine is currently healthy."""
        status = await self.health_check()
        return status == EngineStatus.HEALTHY

    def get_status(self) -> EngineStatus:
        """Get last known status (may be cached)."""
        return self._status

    def get_max_latency_ms(self) -> int:
        """Get maximum acceptable latency for this engine."""
        return self.get_capability().max_latency_ms

    def get_cost_estimate(self, input_tokens: int, output_tokens: int) -> int:
        """Estimate cost in cents for a task."""
        cap = self.get_capability()
        input_cost = (input_tokens / 1_000_000) * cap.cost_per_1m_input_tokens
        output_cost = (output_tokens / 1_000_000) * cap.cost_per_1m_output_tokens
        return int(input_cost + output_cost)

    def record_execution(self, response: EngineResponse) -> None:
        """Record execution metrics for monitoring."""
        self._call_count += 1
        self._total_latency_ms += response.latency_ms

        if not response.success:
            self._error_count += 1
            self._last_error = response.error

    def get_stats(self) -> dict[str, Any]:
        """Get execution statistics."""
        error_rate = (
            (self._error_count / self._call_count)
            if self._call_count > 0
            else 0
        )
        avg_latency = (
            (self._total_latency_ms / self._call_count)
            if self._call_count > 0
            else 0
        )

        return {
            "engine_type": self.engine_type.value,
            "call_count": self._call_count,
            "error_count": self._error_count,
            "error_rate": error_rate,
            "average_latency_ms": int(avg_latency),
            "last_error": self._last_error,
        }

    def reset_stats(self) -> None:
        """Reset execution statistics."""
        self._call_count = 0
        self._error_count = 0
        self._total_latency_ms = 0
        self._last_error = None


class EnginePool:
    """Pool of engines for load-balancing and fallback."""

    def __init__(self):
        self.engines: dict[EngineType, EngineInterface] = {}
        self.fallback_chain: list[EngineType] = [
            EngineType.HAIKU,
            EngineType.SONNET,
            EngineType.OPUS,
        ]

    def register_engine(self, engine: EngineInterface) -> None:
        """Register an engine in the pool."""
        self.engines[engine.engine_type] = engine

    async def execute_with_fallback(
        self, request: EngineRequest, initial_engine: EngineType = EngineType.HAIKU
    ) -> Optional[EngineResponse]:
        """Execute task with fallback chain.

        Tries engines in fallback order until success.
        """
        # Reorder fallback chain to start with initial_engine
        chain = [initial_engine] + [
            e for e in self.fallback_chain if e != initial_engine
        ]

        for engine_type in chain:
            if engine_type not in self.engines:
                continue

            engine = self.engines[engine_type]

            try:
                response = await asyncio.wait_for(
                    engine.execute(request),
                    timeout=request.timeout_ms / 1000.0,
                )

                if response.success:
                    engine.record_execution(response)
                    return response

            except asyncio.TimeoutError:
                # Engine timed out, try next
                continue
            except Exception as e:
                # Engine error, try next
                continue

        # All engines failed
        return None

    def get_engine(self, engine_type: EngineType) -> Optional[EngineInterface]:
        """Get a specific engine."""
        return self.engines.get(engine_type)

    def get_all_engines(self) -> list[EngineInterface]:
        """Get all registered engines."""
        return list(self.engines.values())

    async def health_check_all(self) -> dict[EngineType, EngineStatus]:
        """Check health of all engines in parallel."""
        tasks = {
            engine_type: asyncio.create_task(engine.health_check())
            for engine_type, engine in self.engines.items()
        }

        results = {}
        for engine_type, task in tasks.items():
            try:
                results[engine_type] = await task
            except Exception:
                results[engine_type] = EngineStatus.UNAVAILABLE

        return results

    def get_stats_all(self) -> dict[EngineType, dict[str, Any]]:
        """Get stats for all engines."""
        return {
            engine_type: engine.get_stats()
            for engine_type, engine in self.engines.items()
        }
