"""Local Engine Implementation (Phase 2, Week 7).

Local Llama 2 7B fallback (offline capability).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from core.engines.engine_interface import (
    EngineType,
    EngineStatus,
    EngineCapability,
    EngineRequest,
    EngineResponse,
    EngineInterface,
)


class LocalEngine(EngineInterface):
    """Local Llama 2 7B implementation.

    Capabilities:
    - Acceptable quality (0.85/1.0)
    - Slow (p99 ~3s)
    - Free (0 cost)
    - Last resort fallback when all cloud engines unavailable
    """

    def __init__(self):
        super().__init__(EngineType.LOCAL)
        self.model_id = "llama-2-7b"
        self.available = True

    async def execute(self, request: EngineRequest) -> EngineResponse:
        """Execute task using local Llama model."""
        start_time = time.time()

        if not self.available:
            return EngineResponse(
                task_id=request.task_id,
                engine_type=EngineType.LOCAL,
                success=False,
                latency_ms=0,
                cost_cents=0,
                error="Local model not loaded",
            )

        try:
            # Simulate local model inference
            await asyncio.sleep(0.8 + (len(request.prompt) / 5000.0))

            output = f"Local response: {request.prompt[:35]}..."
            tokens_input = len(request.prompt.split())
            tokens_output = len(output.split())

            latency_ms = int((time.time() - start_time) * 1000)

            response = EngineResponse(
                task_id=request.task_id,
                engine_type=EngineType.LOCAL,
                success=True,
                output=output,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                latency_ms=latency_ms,
                cost_cents=0,  # Free
                quality_score=0.85,
            )

            self.record_execution(response)
            return response

        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)
            response = EngineResponse(
                task_id=request.task_id,
                engine_type=EngineType.LOCAL,
                success=False,
                latency_ms=latency_ms,
                cost_cents=0,
                error=str(e),
            )
            self.record_execution(response)
            return response

    async def execute_streaming(self, request: EngineRequest) -> Any:
        raise NotImplementedError("Streaming not implemented yet")

    def get_capability(self) -> EngineCapability:
        return EngineCapability(
            engine_type=EngineType.LOCAL,
            max_latency_ms=4000,  # p99 ~3000ms
            max_tokens=8192,  # Limited by 7B model
            cost_per_1m_input_tokens=0,  # Free
            cost_per_1m_output_tokens=0,  # Free
            supports_streaming=False,
            supports_vision=False,
            quality_tier="standard",
        )

    async def health_check(self) -> EngineStatus:
        """Check if local model is loaded and ready."""
        try:
            await asyncio.sleep(0.05)
            if self.available:
                return EngineStatus.HEALTHY
            else:
                return EngineStatus.UNAVAILABLE
        except Exception:
            return EngineStatus.UNAVAILABLE

    def load_model(self) -> bool:
        """Load local Llama model into memory."""
        self.available = True
        return True

    def unload_model(self) -> None:
        """Unload local model to free memory."""
        self.available = False
