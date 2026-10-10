"""ADR-0534 Layer 1 — feedback-rate throttle.

A burst of feedback on one subject is either broken automation or poisoning;
either way it must not reach an optimizer. The window is counted from the
PERSISTED decision ledger (``feedback_gate.DecisionLedger``), never from an
in-process deque: the console builds a fresh handler per request, so a
process-local history would never see a second signal and the throttle would
never fire.

There is no operator override secret (the ADR draft had one). An override is
a bypass of a fail-closed gate, and CLAUDE.md forbids those; an operator who
needs a higher rate changes the config, which is visible in code review.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitConfig:
    window_sec: int = 60
    max_per_window: int = 5


DEFAULT_RATE_LIMIT = RateLimitConfig()


@dataclass(frozen=True)
class ThrottleResult:
    allowed: bool
    signals_in_window: int
    config: RateLimitConfig


def check_rate(decisions: list[dict], *, subject: str, now: float,
               config: RateLimitConfig = DEFAULT_RATE_LIMIT) -> ThrottleResult:
    """Count every gate decision (accepted or not) on ``subject`` in the window.

    Rejected attempts count too: a burst of invalid signals is exactly the
    pattern this layer exists to stop, and counting only accepted ones would
    let an attacker probe without limit.
    """
    lo = now - config.window_sec
    n = sum(1 for d in decisions
            if d.get("subject") == subject and lo <= float(d.get("ts", 0.0)) <= now)
    return ThrottleResult(allowed=n < config.max_per_window, signals_in_window=n, config=config)
