#!/usr/bin/env python3
"""say.py total-deadline budget (review finding V2, 2026-07-20).

The per-provider timeouts of the auto-chain SUM to up to 40s (openai 10 +
edge 10 + piper 20) while the console's outer subprocess budget is 25s
(routes/voice.py::_TTS_TIMEOUT_S). When the sum overruns, the console
SIGKILLs say.py, the Piper grandchild keeps running as an orphan, and the
sibling corvin_tts_*.wav stays behind. say.py therefore enforces its OWN
wall-clock deadline (default 22s, env CORVIN_TTS_TOTAL_BUDGET_S): each
provider gets min(provider_timeout, remaining - margin), and a provider
with too little remaining budget is SKIPPED with a stderr note so the
console's failure-reason surface sees why.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import say  # noqa: E402


# ── _clamped_timeout: pure unit surface, no providers involved ──────────────

def test_no_deadline_passes_provider_timeout_through():
    assert say._clamped_timeout(10.0, None) == 10.0


def test_ample_budget_keeps_provider_timeout():
    assert say._clamped_timeout(10.0, 21.0) == 10.0


def test_low_budget_clamps_to_remaining_minus_margin():
    t = say._clamped_timeout(20.0, 5.0)
    assert t == pytest.approx(5.0 - say._DEADLINE_MARGIN_S)


def test_exhausted_budget_returns_none():
    almost_nothing = say._DEADLINE_MARGIN_S + say._MIN_ATTEMPT_S - 0.1
    assert say._clamped_timeout(10.0, almost_nothing) is None
    assert say._clamped_timeout(10.0, 0.0) is None
    assert say._clamped_timeout(10.0, -3.0) is None


def test_default_total_budget_stays_under_console_outer_timeout():
    # routes/voice.py::_TTS_TIMEOUT_S defaults to 25s; say.py must finish
    # (or degrade) strictly before the caller SIGKILLs it.
    assert say._TOTAL_BUDGET_S < 25.0


def test_deadline_anchored_at_module_import_not_main():
    # V2-RESIDUAL (2026-07-20): the total-deadline clock is captured at module
    # import so a slow interpreter start counts against the budget rather than
    # pushing the internal deadline past the console's outer SIGKILL. The anchor
    # must exist and already be in the past (set during import).
    assert hasattr(say, "_PROCESS_START_MONOTONIC")
    import time as _t
    assert say._PROCESS_START_MONOTONIC <= _t.monotonic()


# ── E2E: exhausted budget skips every provider, no orphan work started ──────

def test_exhausted_budget_skips_all_providers_e2e(tmp_path):
    out = tmp_path / "clip.opus"
    env = dict(os.environ)
    env["CORVIN_TTS_TOTAL_BUDGET_S"] = "0"
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "say.py"), str(out), "hello", "en"],
        capture_output=True, text=True, env=env, timeout=15,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "", "silent skip — no provider may run"
    assert "skipping provider" in proc.stderr
    assert not out.exists()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
