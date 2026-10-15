"""PLAN-0959 P1: the stream-idle watchdog must not read a wall-clock step as silence.

Measured 2026-10-13: a laptop lid-close gave the claude turn a 33 298 s "silence" on the
wall clock, and the watchdog killed it on wake-up. CLOCK_MONOTONIC does not advance during a
suspend, so the same step must not kill a turn here.

The test drives a real turn through ``adapter.process_one`` with the fake CLI. The WALL clock
steps forward by 3 h right after the adapter has processed the final ``result`` (the step is
armed from ``_unregister_engine``, which both the old and the new loop call at that point).
The monotonic clock keeps running normally. The stream then ends about 1.4 s later, while the
idle limit is 1.5 s. The turn must finish; before P1 the wall-clock gap killed it at the
first idle check after the step.
"""
from __future__ import annotations

import time

from test_bg_scope_hardening import _derive, _final, _turn  # noqa: F401 — shared harness
from test_bg_scope_completion import box  # noqa: F401 — fixture


def test_a_wall_clock_step_after_the_last_event_does_not_kill_a_healthy_turn(box, monkeypatch, caplog):
    caplog.set_level("INFO")
    def end_shortly_after_the_result(ev):
        # Fixture: result at ~1.54 s. End the stream at 2.94 s, i.e. ~1.4 s after the result,
        # under the 1.5 s idle limit, so the only idle check that could fire is the 1 s tick.
        next(e for e in ev if e.get("type") == "_eof")["_t"] = 2.94

    fx = _derive(box, "idle_clock_step", "plain_no_children", end_shortly_after_the_result)
    monkeypatch.setenv("ADAPTER_STREAM_IDLE_TIMEOUT", "1.5")

    armed = {"on": False}
    real_time = time.time

    def stepped_time() -> float:
        return real_time() + (3 * 3600.0 if armed["on"] else 0.0)

    # Only the wall clock steps; time.monotonic() is untouched, like a host suspend.
    monkeypatch.setattr(time, "time", stepped_time)

    def arm_after_the_result(adapter):
        real_unregister = adapter._unregister_engine

        def unregister_and_arm(*args, **kwargs):
            armed["on"] = True          # the final result has been processed
            return real_unregister(*args, **kwargs)

        monkeypatch.setattr(adapter, "_unregister_engine", unregister_and_arm)

    msgs, _ = _turn(monkeypatch, box, fx, speedup=1, adapter_hook=arm_after_the_result)

    assert armed["on"], "the step was never armed: the turn did not reach its result"
    # The answer "ok" was already delivered before any kill, so the final text alone cannot
    # tell the two behaviours apart. The adapter log can: the old watchdog logs
    # "engine stream idle" when it kills; the new one logs the clock step instead.
    log_text = caplog.text
    assert "engine stream idle" not in log_text, "the idle watchdog killed a healthy turn"
    assert "watchdog.clock_jump" in log_text, "the wall-clock step was not observed"
    assert "ok" in _final(msgs)
