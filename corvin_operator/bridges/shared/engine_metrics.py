"""engine_metrics.py — Prometheus metrics for OpenCodeEngine OS-turns.

ADR-2087 removed HermesEngine and its ``record_hermes_turn`` metric.

ADR-0067 M2.5. Lazy-loads prometheus_client so the adapter stays importable
on hosts without a monitoring stack. All functions are best-effort and never
raise (adapter startup is never blocked by metrics).

MUST NOT import anthropic (CI AST lint enforces — matches L22/L29/L34/L35 rule).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Lazy Prometheus import — silently disabled when client not installed
# ---------------------------------------------------------------------------

try:
    from prometheus_client import Counter, Histogram  # type: ignore[import-not-found]

    _OPENCODE_TURNS = Counter(
        "corvin_bridge_opencode_turns_total",
        "OpenCodeEngine OS-turn dispatch count by outcome and persona",
        labelnames=["outcome", "persona"],
    )
    _OPENCODE_DURATION = Histogram(
        "corvin_bridge_opencode_turn_duration_seconds",
        "OpenCodeEngine OS-turn wall-clock duration",
        labelnames=["outcome"],
        buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0],
    )
    _METRICS_AVAILABLE = True
except ImportError:
    _OPENCODE_TURNS = _OPENCODE_DURATION = None  # type: ignore[assignment]
    _METRICS_AVAILABLE = False


# ---------------------------------------------------------------------------
# Public helpers — called from adapter._call_opencode_streaming_via_engine
# ---------------------------------------------------------------------------


def record_opencode_turn(
    *,
    outcome: str,
    persona: str,
    duration_s: float,
) -> None:
    """Emit OpenCode OS-turn Prometheus metrics. Best-effort; never raises."""
    try:
        if _OPENCODE_TURNS is not None:
            _OPENCODE_TURNS.labels(outcome=outcome, persona=persona or "").inc()
        if _OPENCODE_DURATION is not None:
            _OPENCODE_DURATION.labels(outcome=outcome).observe(duration_s)
    except Exception:  # noqa: BLE001
        pass


__all__ = ["record_opencode_turn"]
