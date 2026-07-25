"""ADR-0222 k=5 — the chat_runtime side of the measurement hook.

Pins the properties that made the k=4 hook dead-on-arrival and invisible:
its import path did not exist, it read a non-existent token key, it fed the
UI-badged answer to the judge, it read a possibly-unbound `ok`, and it wrapped
all of that in `except (ImportError, Exception): pass`.

Run: python3 -m pytest tests/test_tde_measurement_k5_hook.py
"""
from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "orchestration"))

from corvin_console import chat_runtime as cr  # noqa: E402

# ---------------------------------------------------------------------------
# Sampling gate
# ---------------------------------------------------------------------------

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TDE_MEASUREMENT_ENABLED", raising=False)
    assert cr._measurement_should_sample() is False


def test_enabled_flag_samples_every_turn(monkeypatch):
    monkeypatch.setenv("TDE_MEASUREMENT_ENABLED", "1")
    monkeypatch.delenv("TDE_MEASUREMENT_SAMPLE_RATE", raising=False)
    assert cr._measurement_should_sample() is True


@pytest.mark.parametrize("flag", ["0", "true", "yes", "", "TRUE"])
def test_only_literal_one_enables(monkeypatch, flag):
    """A measurement week triples per-turn cost — it must take an exact opt-in."""
    monkeypatch.setenv("TDE_MEASUREMENT_ENABLED", flag)
    assert cr._measurement_should_sample() is False


def test_zero_rate_samples_nothing(monkeypatch):
    monkeypatch.setenv("TDE_MEASUREMENT_ENABLED", "1")
    monkeypatch.setenv("TDE_MEASUREMENT_SAMPLE_RATE", "0.0")
    assert cr._measurement_should_sample() is False


@pytest.mark.parametrize("bad", ["abc", "1.5", "-0.2", "1e9", "nan"])
def test_unparseable_or_out_of_range_rate_fails_closed(monkeypatch, bad):
    """A typo'd rate must not silently triple every turn's spend."""
    monkeypatch.setenv("TDE_MEASUREMENT_ENABLED", "1")
    monkeypatch.setenv("TDE_MEASUREMENT_SAMPLE_RATE", bad)
    assert cr._measurement_should_sample() is False


def test_fractional_rate_thins_sampling(monkeypatch):
    monkeypatch.setenv("TDE_MEASUREMENT_ENABLED", "1")
    monkeypatch.setenv("TDE_MEASUREMENT_SAMPLE_RATE", "0.5")
    monkeypatch.setattr("random.random", lambda: 0.4)
    assert cr._measurement_should_sample() is True
    monkeypatch.setattr("random.random", lambda: 0.6)
    assert cr._measurement_should_sample() is False


# ---------------------------------------------------------------------------
# Detached spawn
# ---------------------------------------------------------------------------

def _ctx(**over):
    base = dict(task_id="run-1", task_text="t", tde_tokens=10, tde_output="o",
                task_complexity="moderate", user_model="m",
                workload_type=None, confidence=None)
    base.update(over)
    return base


def test_spawn_runs_detached_and_keeps_a_strong_ref(monkeypatch):
    """asyncio holds only a WEAK ref to a bare create_task result.

    Without the module-level set, a measurement can be garbage-collected
    mid-flight and vanish with no trace.
    """
    started = asyncio.Event()
    ran: list[str] = []

    async def _body(ctx):
        started.set()
        await asyncio.sleep(0)
        ran.append(ctx["task_id"])

    monkeypatch.setattr(cr, "_run_tde_measurement", _body)

    async def _drive():
        cr._MEASUREMENT_TASKS.clear()
        cr._spawn_tde_measurement(_ctx())
        # Returned immediately, without awaiting the measurement.
        assert not started.is_set()
        assert len(cr._MEASUREMENT_TASKS) == 1
        await asyncio.wait_for(started.wait(), timeout=5)
        while cr._MEASUREMENT_TASKS:
            await asyncio.sleep(0)
        return ran

    assert asyncio.run(_drive()) == ["run-1"]
    assert cr._MEASUREMENT_TASKS == set(), "done callback must release the ref"


def test_spawn_respects_concurrency_limit(monkeypatch):
    """Each measurement is 2 baselines + 2 judge calls; they must not pile up
    per concurrent chat turn and contend for the same rate-limit budget."""
    release = asyncio.Event()

    async def _body(ctx):
        await release.wait()

    monkeypatch.setattr(cr, "_run_tde_measurement", _body)

    async def _drive():
        cr._MEASUREMENT_TASKS.clear()
        for i in range(5):
            cr._spawn_tde_measurement(_ctx(task_id=f"run-{i}"))
        in_flight = len(cr._MEASUREMENT_TASKS)
        release.set()
        while cr._MEASUREMENT_TASKS:
            await asyncio.sleep(0)
        return in_flight

    assert asyncio.run(_drive()) == cr._MEASUREMENT_MAX_CONCURRENT


def test_spawn_never_raises_when_measurement_body_fails(monkeypatch):
    """A failing measurement must not surface into the chat turn — but it must be
    LOGGED, not swallowed the way the k=4 hook's bare `except: pass` did."""
    logged: list[tuple] = []

    async def _boom(ctx):
        raise RuntimeError("judge exploded")

    monkeypatch.setattr(cr, "_run_tde_measurement", _boom)
    monkeypatch.setattr(cr._log, "warning",
                        lambda *a, **kw: logged.append((a, kw)))

    async def _drive():
        cr._MEASUREMENT_TASKS.clear()
        cr._spawn_tde_measurement(_ctx())
        while cr._MEASUREMENT_TASKS:
            await asyncio.sleep(0)

    asyncio.run(_drive())  # must not raise
    assert any("judge exploded" in repr(a) for a, _ in logged), \
        "measurement failure must be logged, not silently dropped"


def test_spawn_without_event_loop_does_not_raise(monkeypatch):
    async def _body(ctx):
        return None

    monkeypatch.setattr(cr, "_run_tde_measurement", _body)
    cr._MEASUREMENT_TASKS.clear()
    cr._spawn_tde_measurement(_ctx())  # no running loop — must be a no-op
    assert cr._MEASUREMENT_TASKS == set()


def test_missing_orchestration_tree_is_logged_not_swallowed(monkeypatch):
    """On a wheel install without operator/orchestration the import fails; that
    must produce a log line, not silence."""
    logged: list[tuple] = []
    monkeypatch.setattr(cr._log, "warning",
                        lambda *a, **kw: logged.append((a, kw)))
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) \
        else __builtins__.__import__

    def _blocked(name, *a, **kw):
        if name.startswith("tde.tde_measurement"):
            raise ImportError("no orchestration tree")
        return real_import(name, *a, **kw)

    monkeypatch.setitem(
        __builtins__ if isinstance(__builtins__, dict) else __builtins__.__dict__,
        "__import__", _blocked)
    asyncio.run(cr._run_tde_measurement(_ctx()))
    assert logged, "ImportError must be logged"


# ---------------------------------------------------------------------------
# Structural invariants of the turn wiring
# ---------------------------------------------------------------------------

def _tde_turn_source() -> str:
    return inspect.getsource(cr._stream_tde_turn)


def test_hook_feeds_the_badge_free_output_to_the_judge():
    """The judge must score model content, not UI chrome.

    `final` carries the appended engine badge ("⚙ Engine: … Steps: 3/3 ok …").
    Judging that against an unbadged direct answer reports a quality loss the TDE
    arm never incurred. The measurement context must therefore carry
    `_tde_bare_output`, captured before the badge is appended.
    """
    src = _tde_turn_source()
    assert '"tde_output": _tde_bare_output,' in src, \
        "measurement context must pass the badge-free output"
    bare_at = src.index("_tde_bare_output = final")
    badge_at = src.index("final = (final + badge).strip()")
    assert bare_at < badge_at, \
        "bare output must be captured BEFORE the badge is appended"


def test_hook_refuses_partially_instrumented_runs():
    """summary["total_tokens"] sums ONLY the steps that returned a usage block.

    On a run where 2 of 5 steps reported usage, it is an UNDER-count of what TDE
    actually spent — every derived savings figure is then biased in TDE's own
    favour. Full instrumentation coverage is therefore a precondition for
    sampling, and the skip is logged rather than silent.
    """
    src = _tde_turn_source()
    assert 'summary.get("instrumented_step_count")' in src, \
        "hook must check instrumentation coverage, not just total_tokens"
    assert 'summary.get("step_count")' in src

    gate_at = src.index("_fully_instrumented and _measurement_should_sample()")
    ctx_at = src.index('"tde_tokens": summary.get("total_tokens"),')
    assert gate_at < ctx_at, \
        "the coverage gate must precede building the measurement context"
    assert "not sampling %s — only %d/%d TDE steps" in src, \
        "a skipped sample must be logged, not dropped silently"


def test_hook_uses_the_real_instrumented_token_source():
    """k=4 read result["usage"], a key this result does not have, so every
    sample it could produce carried tde_tokens=0 — read by the gate as 100%
    savings. The real figure is summary["total_tokens"] (ADR-0219 R1)."""
    src = _tde_turn_source()
    assert '"tde_tokens": summary.get("total_tokens"),' in src
    assert 'result.get("usage", {}).get("total_tokens"' not in src, \
        "the non-existent result['usage'] token source must not come back"


def test_turn_initialises_hook_read_variables_before_the_try():
    """`ok` and `_tde_bare_output` are read after the try block. Assigned only on
    the success path, an exception left `ok` unbound and the hook's own guard
    raised UnboundLocalError OUTSIDE the try meant to contain measurement errors."""
    src = _tde_turn_source()
    try_at = src.index("\n    try:")
    for name, init in (("ok", "ok = False"),
                       ("_tde_bare_output", '_tde_bare_output = ""'),
                       ("_measure_ctx", "_measure_ctx: \"dict[str, Any] | None\" = None")):
        assert init in src, f"{name} must have an initialiser"
        assert src.index(init) < try_at, \
            f"{name} must be initialised BEFORE the try block"


def test_measurement_starts_after_the_answer_is_streamed():
    """The k=4 hook sat BEFORE the result yields, so a measured turn appeared to
    hang for minutes with a finished answer in hand.

    Anchored on the LAST result yield and on the cancellation handler, not the
    first `yield {"type": "result"` in the function: this turn has several such
    yields on early-return paths (import failure, quota exhaustion) near the top,
    so comparing against the first one passes for almost any placement — which is
    exactly how an earlier version of this test failed to catch a hot-path
    regression that had been deliberately reintroduced.
    """
    src = _tde_turn_source()
    spawn_at = src.index("_spawn_tde_measurement(_measure_ctx)")

    assert spawn_at > src.rindex('yield {"type": "result"'), \
        "measurement must start after EVERY result yield, not just the first"
    # Outside the try block: the cancellation handler re-raises, so a
    # disconnected client must never pay for two baseline turns.
    assert spawn_at > src.index("except (asyncio.CancelledError, GeneratorExit):"), \
        "measurement must start outside the turn's try/except, after cancellation handling"
    assert spawn_at > src.index("_reply_persisted = True"), \
        "measurement must start only after the reply is persisted"


def test_measurement_starts_after_the_context_sync():
    """The ADR-0213 context-sync is itself an awaited `claude -p --continue` on
    the expensive user model.

    Starting the sampler before it would put two baseline turns in flight
    alongside that call, and the resulting CLI/rate-limit contention would land
    in the very token and latency numbers being measured — the same contention
    the sequential baseline design exists to avoid.
    """
    src = _tde_turn_source()
    spawn_at = src.index("_spawn_tde_measurement(_measure_ctx)")
    assert spawn_at > src.index("_sync_acs_result_to_transcript"), \
        "measurement must start after the context-sync call"
    assert spawn_at > src.index('os_audit("os_turn.context_sync"'), \
        "measurement must start after the context-sync is fully accounted for"
    assert spawn_at < src.rindex('yield {"type": "done"}'), \
        "measurement must still be started before the turn ends"


def test_production_hook_does_not_use_the_mock_orchestrator():
    """Wiring the fixed-number test double into a production path would feed the
    gate invented evidence."""
    src = inspect.getsource(cr._run_tde_measurement)
    assert "RealTdeOrchestrator" in src
    assert "MockTdeOrchestrator" not in src


def test_hook_has_no_blanket_except_pass():
    """`except (ImportError, Exception): pass` is why two hard defects in the k=4
    hook went unnoticed until someone read the code."""
    src = _tde_turn_source() + inspect.getsource(cr._run_tde_measurement) \
        + inspect.getsource(cr._spawn_tde_measurement)
    assert "except (ImportError, Exception)" not in src
