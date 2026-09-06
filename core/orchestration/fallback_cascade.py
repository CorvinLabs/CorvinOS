"""Fallback Cascade Logic (Phase 2, Week 9).

Implements multi-level fallback with timeout thresholds and retry logic.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Optional, List

from core.engines.engine_interface import EngineType, EngineRequest, EngineResponse
from core.engines.engine_registry import EngineRegistry


@dataclass
class CascadeLevel:
    """Single level in cascade hierarchy."""

    engine_type: EngineType
    timeout_ms: int
    max_retries: int = 1


@dataclass
class CascadeResult:
    """Result of cascade attempt."""

    success: bool
    engine_used: Optional[EngineType] = None
    response: Optional[EngineResponse] = None
    cascade_level: int = 0  # Which level in cascade succeeded
    total_attempts: int = 0
    total_latency_ms: int = 0


class FallbackCascade:
    """Cascading fallback through multiple engines.

    Default chain: Haiku (5s) → Hermes (10s) → Claude (20s) → Local (no timeout)
    """

    def __init__(self, registry: EngineRegistry):
        self.registry = registry
        # NOTE: Hermes and Local removed in v2.0 (Claude Code only).
        # Legacy cascade chain simplified to Claude only.
        self.cascade_chain: List[CascadeLevel] = [
            CascadeLevel(EngineType.CLAUDE, timeout_ms=20000, max_retries=1),
        ]
        self.cascade_stats = {
            "attempts": 0,
            "successes": 0,
            "timeouts": 0,
            "errors": 0,
            "cascade_count": 0,  # Times we had to fallback
        }

    async def execute_with_cascade(self, request: EngineRequest) -> CascadeResult:
        """Execute task with fallback cascade.

        Tries each level in sequence until success or exhaustion.
        """
        start_time = time.time()
        total_attempts = 0
        last_error = None

        for level_idx, level in enumerate(self.cascade_chain):
            engine = self.registry.get_engine(level.engine_type)
            if not engine:
                continue

            # Retry loop at this level
            for attempt in range(level.max_retries + 1):
                total_attempts += 1
                self.cascade_stats["attempts"] += 1

                try:
                    # Execute with timeout
                    response = await asyncio.wait_for(
                        engine.execute(request),
                        timeout=level.timeout_ms / 1000.0
                    )

                    if response.success:
                        # Success!
                        self.cascade_stats["successes"] += 1
                        if level_idx > 0:
                            self.cascade_stats["cascade_count"] += 1

                        total_latency = int((time.time() - start_time) * 1000)
                        return CascadeResult(
                            success=True,
                            engine_used=level.engine_type,
                            response=response,
                            cascade_level=level_idx,
                            total_attempts=total_attempts,
                            total_latency_ms=total_latency,
                        )

                    # Non-success response, try next level
                    last_error = response.error
                    break

                except asyncio.TimeoutError:
                    # Timeout at this level
                    self.cascade_stats["timeouts"] += 1
                    last_error = f"Timeout at {level.engine_type.value} (>{level.timeout_ms}ms)"

                    if attempt < level.max_retries:
                        # Retry at same level
                        continue
                    else:
                        # Move to next level
                        break

                except Exception as e:
                    # Error at this level
                    self.cascade_stats["errors"] += 1
                    last_error = str(e)
                    break

        # All levels exhausted
        total_latency = int((time.time() - start_time) * 1000)
        return CascadeResult(
            success=False,
            engine_used=None,
            response=None,
            cascade_level=len(self.cascade_chain),
            total_attempts=total_attempts,
            total_latency_ms=total_latency,
        )

    def set_cascade_level(self, level_idx: int, timeout_ms: int, max_retries: int) -> None:
        """Customize cascade level parameters."""
        if 0 <= level_idx < len(self.cascade_chain):
            self.cascade_chain[level_idx].timeout_ms = timeout_ms
            self.cascade_chain[level_idx].max_retries = max_retries

    def get_stats(self) -> dict:
        """Get cascade statistics."""
        success_rate = (
            self.cascade_stats["successes"] / max(self.cascade_stats["attempts"], 1)
        ) * 100

        return {
            **self.cascade_stats,
            "success_rate_percent": success_rate,
        }
