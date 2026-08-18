"""Token Measurement Hook — Integrates TokenCounter into the Chat/Turn Pipeline.

This hook measures real token usage during every turn:
1. Records when a turn starts (with session_id, tenant_id)
2. Records LLM calls (input + output tokens)
3. Records subsystem overhead (memory lookup, skill injection, etc.)
4. Stores metrics in TokenMetricsStore with EventEmitter integration
5. Calculates Vibe Engineering savings vs. baseline

Integration points:
- ChatRuntime.on_turn_start() → hook.start_turn()
- LLMWorker.on_completion() → hook.record_llm()
- ContextPipeline.on_stage_complete() → hook.record_subsystem()
- ChatRuntime.on_turn_complete() → hook.end_turn()
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional
from dataclasses import dataclass

from core.learning.token_instrumentation import TokenCounter, TokenInstrumentationHooks
from core.learning.token_metrics_store import TokenMetricsStore
from core.learning.event_emitter import EventEmitter


@dataclass
class TurnContext:
    """Context for a single turn measurement."""
    turn_id: str
    session_id: str
    tenant_id: str
    start_time: float
    counter: TokenCounter


class TokenMeasurementHook:
    """Hook for measuring tokens in real turns."""

    def __init__(self, store: TokenMetricsStore, emitter: EventEmitter):
        """Initialize with token store and event emitter."""
        self.store = store
        self.emitter = emitter
        self._turn_context: Optional[TurnContext] = None

    def start_turn(
        self,
        turn_id: str,
        session_id: str,
        tenant_id: str = "default",
        engine: str = "claude",
        engine_tier: str = "cloud",
    ) -> None:
        """Record turn start."""
        start_time = time.time()

        # Create counter for this turn
        counter = TokenInstrumentationHooks.on_worker_engine_start(
            turn_id=turn_id,
            engine=engine,
            engine_tier=engine_tier,
        )

        # Store context for this turn
        self._turn_context = TurnContext(
            turn_id=turn_id,
            session_id=session_id,
            tenant_id=tenant_id,
            start_time=start_time,
            counter=counter,
        )

    def record_llm_call(self, input_tokens: int, output_tokens: int) -> None:
        """Record an LLM API call (e.g., Claude API)."""
        if not self._turn_context:
            return

        TokenInstrumentationHooks.on_llm_response(
            counter=self._turn_context.counter,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def record_subsystem(self, subsystem: str, overhead_tokens: int = 0) -> None:
        """Record subsystem execution (memory lookup, skill injection, etc.)."""
        if not self._turn_context:
            return

        TokenInstrumentationHooks.on_subsystem_executed(
            counter=self._turn_context.counter,
            subsystem=subsystem,
            overhead_tokens=overhead_tokens,
        )

    def end_turn(self) -> None:
        """Record turn completion and store metrics."""
        if not self._turn_context:
            return

        ctx = self._turn_context

        # Calculate turn duration
        duration_ms = (time.time() - ctx.start_time) * 1000

        # Get final counter data
        counter = ctx.counter
        total_tokens = counter.total_tokens()
        baseline_tokens = counter.baseline_tokens()
        savings_tokens = baseline_tokens - total_tokens
        savings_percent = (savings_tokens / baseline_tokens * 100) if baseline_tokens > 0 else 0

        # Store metrics in database
        self.store.insert_token_metrics(
            session_id=ctx.session_id,
            tenant_id=ctx.tenant_id,
            turn_id=ctx.turn_id,
            input_tokens=counter.input_tokens,
            output_tokens=counter.output_tokens,
            total_tokens=total_tokens,
            baseline_tokens=baseline_tokens,
            savings_tokens=savings_tokens,
            savings_percent=savings_percent,
            latency_ms=duration_ms,
            task_type=counter.task_type,
            outcome_quality="success",  # Will be updated by outcome loop
            subsystem_breakdown=counter.subsystem_breakdown,
        )

        # Emit event for real-time dashboards
        self.emitter.emit("token_metrics_recorded", {
            "timestamp": datetime.utcnow().isoformat(),
            "turn_id": ctx.turn_id,
            "session_id": ctx.session_id,
            "total_tokens": total_tokens,
            "baseline_tokens": baseline_tokens,
            "savings_percent": savings_percent,
        })

        # Clear context for next turn
        self._turn_context = None


# Global hook instance (singleton pattern)
_hook: Optional[TokenMeasurementHook] = None


def initialize_token_hook(store: TokenMetricsStore, emitter: EventEmitter) -> TokenMeasurementHook:
    """Initialize the global token measurement hook."""
    global _hook
    _hook = TokenMeasurementHook(store, emitter)
    return _hook


def get_token_hook() -> Optional[TokenMeasurementHook]:
    """Get the global token measurement hook."""
    return _hook


def record_turn_metrics(
    turn_id: str,
    session_id: str,
    tenant_id: str = "default",
    input_tokens: int = 0,
    output_tokens: int = 0,
    subsystems: Optional[dict] = None,
) -> None:
    """Convenience function to record a complete turn's metrics in one call.

    Usage in ChatRuntime or similar:
        record_turn_metrics(
            turn_id="turn_123",
            session_id="sess_456",
            tenant_id="tenant_1",
            input_tokens=1500,
            output_tokens=300,
            subsystems={
                "memory_lookup": 50,
                "skill_injection": 100,
            }
        )
    """
    hook = get_token_hook()
    if not hook:
        return

    hook.start_turn(turn_id, session_id, tenant_id)

    # Record LLM call
    if input_tokens > 0 or output_tokens > 0:
        hook.record_llm_call(input_tokens, output_tokens)

    # Record subsystems
    if subsystems:
        for subsystem, tokens in subsystems.items():
            hook.record_subsystem(subsystem, tokens)

    hook.end_turn()
