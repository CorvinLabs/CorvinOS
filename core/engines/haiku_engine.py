"""Haiku Engine Implementation (Phase 2, Week 7).

Fast, cheap Haiku 4.5 backend for EngineInterface.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional, Any

from core.engines.engine_interface import (
    EngineType,
    EngineStatus,
    EngineCapability,
    EngineRequest,
    EngineResponse,
    EngineInterface,
)


class HaikuEngine(EngineInterface):
    """Haiku 4.5 implementation.

    Capabilities:
    - Good quality (0.92/1.0)
    - Fast (p99 ~1.2s)
    - Cheapest ($0.80/$4 per 1M tokens)
    - Best for quick tasks, summarization, simple classification
    """

    def __init__(self):
        super().__init__(EngineType.HAIKU)
        self.model_id = "claude-haiku-4-5-20251001"

    async def execute(self, request: EngineRequest) -> EngineResponse:
        """Execute task using Haiku.

        Simulates Haiku behavior:
        - Takes 0.5-1.5s depending on task complexity
        - Quality score 0.88-0.95
        - Costs $0.80 per 1M input, $4 per 1M output tokens
        """
        start_time = time.time()

        try:
            # Simulate Haiku API call
            await asyncio.sleep(0.2 + (len(request.prompt) / 20000.0))

            # Simulate output (shorter than Claude)
            output = f"Haiku response: {request.prompt[:30]}..."
            tokens_input = len(request.prompt.split())
            tokens_output = len(output.split())

            # Calculate cost (Haiku pricing)
            input_cost = (tokens_input / 1_000_000) * 0.80  # $0.80 per 1M
            output_cost = (tokens_output / 1_000_000) * 4.0  # $4 per 1M
            cost_cents = int((input_cost + output_cost) * 100)

            latency_ms = int((time.time() - start_time) * 1000)

            response = EngineResponse(
                task_id=request.task_id,
                engine_type=EngineType.HAIKU,
                success=True,
                output=output,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                latency_ms=latency_ms,
                cost_cents=cost_cents,
                quality_score=0.92,
            )

            self.record_execution(response)
            return response

        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)
            response = EngineResponse(
                task_id=request.task_id,
                engine_type=EngineType.HAIKU,
                success=False,
                latency_ms=latency_ms,
                cost_cents=0,
                error=str(e),
            )
            self.record_execution(response)
            return response

    async def execute_streaming(self, request: EngineRequest) -> Any:
        """Stream Haiku response."""
        raise NotImplementedError("Streaming not implemented yet")

    def get_capability(self) -> EngineCapability:
        """Get Haiku capability profile."""
        return EngineCapability(
            engine_type=EngineType.HAIKU,
            max_latency_ms=1500,  # p99 ~1200ms
            max_tokens=100000,
            cost_per_1m_input_tokens=80,  # $0.80
            cost_per_1m_output_tokens=400,  # $4
            supports_streaming=True,
            supports_vision=False,
            quality_tier="standard",
        )

    async def health_check(self) -> EngineStatus:
        """Quick health check for Haiku API."""
        try:
            await asyncio.sleep(0.05)
            return EngineStatus.HEALTHY
        except Exception:
            return EngineStatus.UNAVAILABLE
