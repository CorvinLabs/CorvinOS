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

import logging
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
        """Record turn completion and store metrics.

        Rewritten 2026-08-18 — as written, this method could not complete even
        once. It called `counter.total_tokens()` and `counter.baseline_tokens()`
        (both plain int attributes on TokenCounter, so: "'int' object is not
        callable"), read `counter.subsystem_breakdown` (the field is
        `subsystem_tokens`), invoked `store.insert_token_metrics(...)` with
        keyword arguments the store does not accept (its API is
        `write_token_metrics(counter, tenant_id=..., ...)`), and finally called
        `emitter.emit(name, dict)` where EventEmitter takes a LearningEvent.
        Every real turn therefore raised on the first line of measurement and
        was swallowed by the `except Exception: pass` around the call site in
        chat_runtime.py — which is why the dashboard only ever showed rows that
        had been inserted by hand.
        """
        if not self._turn_context:
            return

        ctx = self._turn_context
        counter = ctx.counter

        # Stamps end_time + latency_ms on the counter (to_event reads both).
        counter.finalize()

        # The baseline is an ESTIMATE, never a measurement — see
        # token_baseline.py. Left as None it would make savings_* None and the
        # dashboard blank, so fill it from the same heuristic the rest of the
        # pipeline uses, and keep saying out loud that it is not measured.
        if counter.baseline_tokens is None:
            try:
                from core.learning.token_baseline import BaselineMetrics  # noqa: PLC0415
                counter.baseline_tokens = BaselineMetrics(
                    turn_id=ctx.turn_id,
                    task_complexity=counter.task_complexity or "moderate",
                ).baseline_tokens
            except Exception:  # noqa: BLE001
                counter.baseline_tokens = counter.total_tokens

        # write_token_metrics() converts the counter to a hash-chained
        # LearningEvent, emits it, and mirrors it into the SQLite backend — so
        # it already performs the emit this method used to attempt separately.
        self.store.write_token_metrics(
            counter,
            tenant_id=ctx.tenant_id,
            instance_id=_instance_id(),
            session_id=ctx.session_id,
        )

        # Clear context for next turn
        self._turn_context = None


logger = logging.getLogger(__name__)


def _instance_id() -> str:
    """Stable id for this install; "local" when the registry is unavailable."""
    try:
        from core.learning.instance_registry import get_instance_registry  # noqa: PLC0415
        reg = get_instance_registry()
        current = getattr(reg, "instance_id", None) or getattr(reg, "current_instance_id", None)
        if isinstance(current, str) and current:
            return current
    except Exception:  # noqa: BLE001
        pass
    return "local"

# Global hook instance (singleton pattern)
_hook: Optional[TokenMeasurementHook] = None
# One-shot latch: a failed auto-init must not be retried on every single turn.
_autoinit_attempted: bool = False


def initialize_token_hook(store: TokenMetricsStore, emitter: EventEmitter) -> TokenMeasurementHook:
    """Initialize the global token measurement hook."""
    global _hook
    _hook = TokenMeasurementHook(store, emitter)
    return _hook


def _autoinit_token_hook() -> Optional[TokenMeasurementHook]:
    """Best-effort default wiring, used when no host called initialize_token_hook.

    Why this exists: the explicit initialisation lives in
    `corvin_console.standalone`, but CorvinOS ships TWO hosts — the other one,
    `corvin_gateway.app`, is what `corvin-service` actually runs. On that host
    the hook stayed None, `record_turn_metrics()` returned at its first line,
    and not a single turn was ever recorded, while the dashboard sat there
    looking merely empty. Same one-host-only trap CLAUDE.md records for the
    boot tripwire. Initialising on first use covers every host, including
    future ones, instead of adding a second call site that can drift again.

    Never raises: token measurement is telemetry, and telemetry must not be
    able to break a chat turn.
    """
    global _hook
    try:
        from pathlib import Path  # noqa: PLC0415

        from core.learning.event_emitter import EventEmitter  # noqa: PLC0415
        from core.learning.token_metrics_db import TokenMetricsDB  # noqa: PLC0415
        from core.learning.token_metrics_store import TokenMetricsStore  # noqa: PLC0415

        tenant_id = "_default"
        try:
            from forge.paths import tenant_home  # noqa: PLC0415
            tenant_dir = tenant_home(tenant_id)
        except Exception:  # noqa: BLE001
            tenant_dir = Path.home() / ".corvin" / "tenants" / tenant_id

        emitter = EventEmitter(Path(tenant_dir), tenant_id)
        _hook = TokenMeasurementHook(TokenMetricsStore(emitter, db=TokenMetricsDB()), emitter)
        logger.info("Token measurement hook auto-initialized")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Token measurement hook auto-init skipped: %s", exc)
        _hook = None
    return _hook


def get_token_hook() -> Optional[TokenMeasurementHook]:
    """Get the global token measurement hook, auto-initialising on first use."""
    global _autoinit_attempted
    if _hook is None and not _autoinit_attempted:
        _autoinit_attempted = True
        return _autoinit_token_hook()
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
