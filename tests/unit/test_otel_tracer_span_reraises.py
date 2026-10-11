"""``OtelTracer.span`` must not swallow the traced body's exception.

With tracing enabled, an exception thrown into the span generator was caught and a second
``yield None`` followed, so ``contextlib`` raised ``RuntimeError("generator didn't stop after
throw()")`` and the caller saw that instead of its own error (a failing Skill reported a
RuntimeError rather than the ValueError it raised). A failure to START the tracer is still
absorbed: tracing must never take a call down.
"""
from __future__ import annotations

import pytest

from core.telemetry import otel_config
from core.telemetry.otel_config import OtelTracer


def _tracer():
    t = otel_config.get_tracer()
    if not isinstance(t, OtelTracer) or not t.config.enabled or not t.tracer:
        pytest.skip("OpenTelemetry tracing is not active in this environment")
    return t


def test_the_bodys_own_exception_reaches_the_caller():
    t = _tracer()
    with pytest.raises(ValueError, match="boom"):
        with t.span("skill.x.execute", attributes={"skill_id": "x"}):
            raise ValueError("boom")


def test_a_normal_body_still_yields_a_span_and_exits_cleanly():
    t = _tracer()
    with t.span("skill.x.execute") as span:
        assert span is not None
    # reusable afterwards
    with t.span("skill.y.execute") as span:
        assert span is not None


def test_a_tracer_that_cannot_start_a_span_is_absorbed(monkeypatch):
    t = _tracer()

    class _Broken:
        def start_as_current_span(self, name):
            raise RuntimeError("exporter down")

    monkeypatch.setattr(t, "tracer", _Broken())
    ran = []
    with t.span("skill.x.execute") as span:
        ran.append(span)
    assert ran == [None], "tracing failed to start: the body still runs, with no span"
