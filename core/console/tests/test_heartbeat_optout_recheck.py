"""Regression: the 5-minute presence heartbeat must re-check `ping_enabled()`
on every loop iteration, not just once at thread start (adversarial review
finding).

Before this fix, `start_heartbeat_thread()` checked the opt-out flag once
before spawning `_heartbeat_loop`, which then looped forever calling
`send_heartbeat()` unconditionally. A user opting out mid-session on a
long-running server (corvin-serve/gateway, no restart) kept getting
heartbeats sent for the rest of the process lifetime — sometimes weeks —
even though `ping_if_due()` (the daily ping) already re-checks every call.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from corvin_console.aco import heartbeat as hb


def test_loop_skips_send_once_opt_out_flips_mid_session():
    calls = {"ping_enabled": 0, "send": 0}
    enabled_sequence = [True, True, False, False]  # opts out on the 3rd tick

    def _fake_ping_enabled(_home):
        idx = min(calls["ping_enabled"], len(enabled_sequence) - 1)
        calls["ping_enabled"] += 1
        return enabled_sequence[idx]

    def _fake_send_heartbeat(_home):
        calls["send"] += 1
        return True

    def _fake_sleep(_s):
        if calls["ping_enabled"] >= 4:
            raise SystemExit  # break out of the infinite loop for the test

    with (
        patch.object(hb, "ping_enabled", _fake_ping_enabled),
        patch.object(hb, "send_heartbeat", _fake_send_heartbeat),
        patch.object(hb.time, "sleep", _fake_sleep),
        patch.object(hb.random, "randint", lambda a, b: 0),
    ):
        with pytest.raises(SystemExit):
            hb._heartbeat_loop("/fake/home")

    assert calls["ping_enabled"] == 4
    assert calls["send"] == 2, "heartbeat must stop being sent once opted out mid-loop"


def test_loop_keeps_sending_while_still_enabled():
    calls = {"send": 0}

    def _fake_sleep(_s):
        if calls["send"] >= 3:
            raise SystemExit

    def _fake_send_heartbeat(_home):
        calls["send"] += 1
        return True

    with (
        patch.object(hb, "ping_enabled", return_value=True),
        patch.object(hb, "send_heartbeat", _fake_send_heartbeat),
        patch.object(hb.time, "sleep", _fake_sleep),
        patch.object(hb.random, "randint", lambda a, b: 0),
    ):
        with pytest.raises(SystemExit):
            hb._heartbeat_loop("/fake/home")

    assert calls["send"] == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
