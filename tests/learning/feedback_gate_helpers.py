"""Test helper: run code that sits DOWNSTREAM of the ADR-0534 feedback gate.

Suites that exercise feedback writers, aggregations or optimizers (not the gate)
write bulk feedback about skills their sandbox never executed. ``open_gate``
makes every subject verifiable and lifts the throttle for that test only —
throttle, reality check and trust weighting themselves are proven by
``tests/learning/test_feedback_trust_gate_e2e.py``. Never use it there.
"""
from __future__ import annotations


def open_gate(monkeypatch) -> None:
    from core.learning import feedback_throttle, reality_check

    monkeypatch.setattr(feedback_throttle, "DEFAULT_RATE_LIMIT",
                        feedback_throttle.RateLimitConfig(window_sec=60, max_per_window=10**9))
    monkeypatch.setattr(reality_check, "validate",
                        lambda feedback, store, **_: reality_check.RealityResult(True, "test_fixture", 1))
