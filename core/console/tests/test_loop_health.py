"""Console loop health (2026-11-06 thread-start stall): never-retired pre-warmed workers,
ABOVE_NORMAL priority on Windows, the loop-lag watchdog and the saturation signal that
optional background work asks. Real threads, a real event loop, a real stall.
Timing bounds are generous: the target host itself has slow thread starts."""
from __future__ import annotations

import asyncio
import sys
import threading
import time

import pytest

from corvin_console import loop_health as lh


@pytest.fixture(autouse=True)
def _clean():
    lh.reset_for_tests()
    yield
    lh.stop_watchdog(join_s=5.0)
    lh.reset_for_tests()


def _new_loop_run(coro_fn):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro_fn())
    finally:
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()


def test_tune_worker_threads_raises_the_anyio_idle_time(monkeypatch):
    from anyio._backends import _asyncio as a

    monkeypatch.setattr(a.WorkerThread, "MAX_IDLE_TIME", 10)
    assert lh.tune_worker_threads() is True
    assert a.WorkerThread.MAX_IDLE_TIME >= 24 * 3600     # a quiet night never empties the pool
    big = a.WorkerThread.MAX_IDLE_TIME
    assert lh.tune_worker_threads(60.0) is True           # never lowers
    assert a.WorkerThread.MAX_IDLE_TIME == big


def test_tune_worker_threads_tolerates_a_missing_attribute(monkeypatch, caplog):
    from anyio._backends import _asyncio as a

    monkeypatch.delattr(a, "WorkerThread")
    assert lh.tune_worker_threads() is False
    assert any("MAX_IDLE_TIME not found" in r.getMessage() for r in caplog.records)


def test_prewarm_runs_on_n_distinct_threads_and_later_bursts_reuse_them(monkeypatch):
    from anyio._backends import _asyncio as a
    import anyio.to_thread

    monkeypatch.setattr(a.WorkerThread, "MAX_IDLE_TIME", 10**9)

    async def main():
        out = await lh.prewarm(4, gap_s=0.0)
        assert out == {"anyio": 4, "executor": 4}
        existing = {t.ident for t in threading.enumerate()}
        seen: dict[str, set] = {"anyio": set(), "executor": set()}
        barrier = threading.Barrier(8, timeout=60)

        def work(kind):
            seen[kind].add(threading.get_ident())
            barrier.wait()

        calls = [anyio.to_thread.run_sync(work, "anyio") for _ in range(4)]
        calls += [asyncio.to_thread(work, "executor") for _ in range(4)]
        await asyncio.gather(*calls)
        # the burst of 4 concurrent calls of each kind ran on already-started threads
        assert seen["anyio"] <= existing and seen["executor"] <= existing
        assert len(seen["anyio"]) == 4 and len(seen["executor"]) == 4
        return True

    assert _new_loop_run(main)


def test_prewarm_does_not_replace_a_live_default_executor():
    async def main():
        loop = asyncio.get_running_loop()
        await asyncio.to_thread(lambda: None)        # asyncio creates its own executor
        first = loop._default_executor
        await lh.prewarm(2, gap_s=0.0)
        assert loop._default_executor is first
        return True

    assert _new_loop_run(main)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows priority class")
def test_raise_process_priority_sets_above_normal():
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32")
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.GetPriorityClass.argtypes = [wintypes.HANDLE]
    k32.GetPriorityClass.restype = wintypes.DWORD
    k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    h = k32.GetCurrentProcess()
    old = k32.GetPriorityClass(h)
    try:
        assert lh.raise_process_priority() is True
        assert k32.GetPriorityClass(h) == 0x00008000
    finally:
        k32.SetPriorityClass(h, old)


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows no-op")
def test_raise_process_priority_is_a_noop_elsewhere():
    assert lh.raise_process_priority() is False


def test_one_slow_thread_start_is_not_saturation_a_sustained_run_is():
    slow, fast = lh.SATURATED_THREAD_START_S + 0.5, 0.01
    lh._note(thread_start_s=slow, now=100.0)
    lh._note(thread_start_s=fast, now=101.0)
    lh._note(thread_start_s=slow, now=102.0)
    assert lh.host_saturated(now=102.0) is False          # 2 of 4 slow: not sustained
    lh._note(thread_start_s=slow, now=103.0)
    assert lh.host_saturated(now=103.0) is True           # 3 of 4 slow: sustained
    assert not lh.host_saturated(now=103.0 + lh.SATURATION_HOLD_S + 1)


def test_a_long_loop_lag_alone_is_saturation():
    lh._note(loop_lag_s=lh.SATURATED_LOOP_LAG_S - 0.1, now=10.0)
    assert lh.host_saturated(now=10.0) is False
    lh._note(loop_lag_s=lh.SATURATED_LOOP_LAG_S + 0.5, now=10.0)
    assert lh.host_saturated(now=11.0) is True


def test_watchdog_records_a_real_stall_with_the_loop_threads_stack(tmp_path):
    log_path = tmp_path / "logs" / "loop-stall.log"

    def blocking_handler_on_the_loop():
        time.sleep(3.0)

    async def main():
        lh.start_watchdog(asyncio.get_running_loop(), period_s=0.1, probe_s=3600,
                          stall_log_s=1.0, log_path=log_path)
        await asyncio.sleep(0.5)
        blocking_handler_on_the_loop()
        await asyncio.sleep(0.5)
        return True

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(main())
    finally:
        lh.stop_watchdog(join_s=5.0)
        loop.close()
    text = log_path.read_text(encoding="utf-8")
    assert "event loop lag" in text
    assert "blocking_handler_on_the_loop" in text      # the loop thread's frame, code only
    assert lh.status()["loop_lag_s"] is not None


def test_watchdog_stop_is_prompt_and_a_restart_never_revives_the_old_thread():
    async def main():
        loop = asyncio.get_running_loop()
        first = lh.start_watchdog(loop, period_s=0.05, probe_s=3600)
        assert lh.start_watchdog(loop) is first              # idempotent while running
        lh.stop_watchdog()                                   # no join on the loop
        second = lh.start_watchdog(loop, period_s=0.05, probe_s=3600)
        assert second is not first
        first.join(5.0)
        assert not first.is_alive()                          # the old one really exited
        assert second.is_alive()
        lh.stop_watchdog()
        second.join(5.0)
        assert not second.is_alive()
        return True

    assert _new_loop_run(main)


def test_install_is_inert_under_pytest():
    async def main():
        lh.install_early()
        await lh.install_serving(None)

    asyncio.run(main())
    assert lh._watchdog is None


# ── both hosts wire it; the thread dump; the external watchdog's policy ──────

_REPO = __import__("pathlib").Path(__file__).resolve().parents[3]


def _lifespan_source(rel: str) -> str:
    return (_REPO / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("host", [
    "core/gateway/corvin_gateway/app.py",        # corvin-webui.service (the live Linux host)
    "core/console/corvin_console/standalone.py",  # corvinos-serve
])
def test_both_shipped_hosts_wire_loop_health_in_their_lifespan(host):
    """Until 2026-11-07 only standalone did; the live gateway host ran with anyio's 10 s
    worker lifetime, no pre-warm and no stall log while its watchdog restarted it."""
    src = _lifespan_source(host)
    early = src.index("_loop_health.install_early()")
    serving = src.index("await _loop_health.install_serving(")
    assert early < serving
    # install_serving sits right before the lifespan yields, so boot + pre-warm are
    # never reported as a stall
    body = src[serving:]
    assert body.index("yield") < body.index("stop_watchdog()")


def test_thread_dump_signal_registers_sigusr2_for_all_threads(monkeypatch):
    if sys.platform == "win32":
        pytest.skip("POSIX only")
    import faulthandler
    import signal
    calls: list = []
    monkeypatch.setattr(faulthandler, "register",
                        lambda sig, **kw: calls.append((sig, kw)))
    monkeypatch.setattr(lh, "_thread_dump_installed", False)
    assert lh.install_thread_dump_signal() is True
    # chain=False is load-bearing: chain=True re-raises into SIG_DFL = terminate after the dump
    assert calls == [(signal.SIGUSR2, {"file": sys.stderr, "all_threads": True, "chain": False})]
    # idempotent: a second install registers nothing
    assert lh.install_thread_dump_signal() is True
    assert len(calls) == 1


def test_thread_dump_signal_is_a_noop_on_windows(monkeypatch):
    import faulthandler
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(lh, "_thread_dump_installed", False)
    monkeypatch.setattr(faulthandler, "register",
                        lambda *a, **kw: pytest.fail("must not register on Windows"),
                        raising=False)
    assert lh.install_thread_dump_signal() is False


def test_systemd_watchdog_gives_a_listening_console_30s_to_answer():
    """scripts/setup.sh writes corvin-watchdog.service. With `-m 5` a console whose loop
    stalled 5-7 s failed three probes while still serving every request, and was
    restarted ten times in an hour (2026-11-07). Not listening is still caught in 5 s."""
    src = _lifespan_source("scripts/setup.sh")
    exec_line = next(l for l in src.splitlines() if l.startswith("ExecStart=") and "healthz" in l)
    assert "curl -fs --connect-timeout 5 -m 30 http://127.0.0.1:8765/v1/console/healthz" in exec_line
    assert "curl -fs -m 5 " not in src
