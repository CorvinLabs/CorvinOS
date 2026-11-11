"""Keep the console's event loop answering on a saturated host.

Measured 2026-11-06 on a 4-vCPU Windows VM with EDR/management agents (run queue 62,
65 % kernel time): a plain ``threading.Thread(...).start(); join()`` took median
0.16-0.3 s, p95 15 s, max 24 s. The console created threads ON the event-loop thread for
almost every request: anyio's worker threads exit after 10 s idle
(``WorkerThread.MAX_IDLE_TIME``), so under bursty traffic nearly every
``run_in_threadpool`` (FastAPI ``def`` handlers/dependencies, response serialisation)
started a new thread from the loop thread. While it started, the loop could not accept,
serve or close connections: the backlog filled, the browser saw "connection refused".

This module, wired from the console lifespan:

* ``tune_worker_threads`` — idle anyio workers are never retired (anyio prunes every
  worker idle longer than ``MAX_IDLE_TIME`` on the next call, so any finite value lets a
  quiet night empty the warm pool); the pool stays bounded by anyio's thread limiter;
* ``prewarm`` — starts ``PREWARM_WORKERS`` anyio workers and default-executor threads in
  the background, spaced so the loop serves requests in between;
* ``raise_process_priority`` — ABOVE_NORMAL on Windows (no admin needed; never HIGH;
  child processes do not inherit it — Windows only propagates IDLE/BELOW_NORMAL);
* ``start_watchdog`` — ONE daemon thread, started after the pre-warm, that measures
  event-loop lag every ``WATCHDOG_PERIOD_S`` and thread-start latency every
  ``THREAD_PROBE_S``; a lag over ``STALL_LOG_S`` writes the loop thread's stack (code
  locations only, no locals, no request data) to ``<CORVIN_HOME>/logs/loop-stall.log``,
  rate-limited and size-capped;
* ``host_saturated`` — asked ONLY by optional background work (the KB projector's
  cadence, starting a new SkillForge fork). Audit, tripwires, the integrity scan, the
  learning brake (anomaly monitor) and canary rollback never ask it;
* ``share_pool`` / ``prewarm_share_pool`` — the 2-thread pool of background project exports (ADR-2323)
* ``browse_pool`` / ``prewarm_browse_pool`` — the folder browser's dedicated 4-thread pool
  (ADR-2322 Decision 8), pre-warmed after the default pool; listings never use the default
  executor;
* ``install_thread_dump_signal`` — ``kill -USR2 <pid>`` writes EVERY thread's stack (code
  locations only) to stderr, i.e. the journal. ``py-spy dump`` needs ptrace, which
  ``kernel.yama.ptrace_scope=1`` denies a non-ancestor process without sudo — measured
  2026-11-07 on the Linux install while the console stalled 2–7 s every ~20 s and the
  systemd watchdog restarted it ten times in an hour. POSIX only; a no-op on Windows.

Both shipped hosts wire this module: ``corvin_console.standalone`` (``corvinos-serve``) and
``corvin_gateway.app`` (``corvin-webui.service``). Until 2026-11-07 only the standalone
host did, so the live Linux install ran with anyio's 10 s worker lifetime, no pre-warm and
no stall log.

Performance only: nothing here changes audit ordering or any fail-closed decision, and
every step is best-effort (a failure is logged and boot continues).
"""
from __future__ import annotations

import asyncio
import collections
import faulthandler
import gc
import logging
import os
import sys
import signal
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

#: idle anyio worker lifetime: effectively never retired (anyio's default is 10 s)
WORKER_IDLE_S = 365 * 24 * 3600.0
#: anyio workers and asyncio executor threads started ahead of demand
PREWARM_WORKERS = 8
#: explicit size of the asyncio default executor when the console creates it
DEFAULT_EXECUTOR_WORKERS = 32
#: spacing between two warm-up thread starts: the loop serves requests in between
PREWARM_GAP_S = 0.05
#: a warm-up holder gives up after this long (a host where 8 starts take longer is
#: reported with the real thread count, never hangs the pre-warm)
PREWARM_HOLD_MAX_S = 120.0

WATCHDOG_PERIOD_S = 5.0
THREAD_PROBE_S = 30.0
STALL_LOG_S = 2.0
STALL_LOG_MIN_INTERVAL_S = 60.0
STALL_LOG_MAX_BYTES = 2 * 1024 * 1024
#: thread-start probes considered, and how many of them must be slow
THREAD_PROBE_WINDOW = 4
THREAD_PROBE_SLOW_NEEDED = 3
#: a thread start+join slower than this is "slow" (the incident host's MEDIAN was
#: 0.16-0.3 s, so the bar sits well above it: only a sustained, severe state counts)
SATURATED_THREAD_START_S = 1.0
#: an event-loop lag above this marks the host as saturated by itself
SATURATED_LOOP_LAG_S = 2.0
#: how long one saturated observation keeps ``host_saturated()`` true
SATURATION_HOLD_S = 120.0

_ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000

_state_lock = threading.Lock()
_last_saturated_at = 0.0
_last_thread_start_s: Optional[float] = None
_last_loop_lag_s: Optional[float] = None
_probes: "collections.deque[float]" = collections.deque(maxlen=THREAD_PROBE_WINDOW)

_watchdog: Optional[threading.Thread] = None
_watchdog_stop: Optional[threading.Event] = None
_watchdog_lock = threading.Lock()


# ── worker threads ──────────────────────────────────────────────────────────

def tune_worker_threads(idle_s: float = WORKER_IDLE_S) -> bool:
    """Raise anyio's idle-worker lifetime to ``idle_s`` (private anyio attribute: guarded,
    so an anyio upgrade that renames it degrades to a log line, never a failed boot)."""
    try:
        from anyio._backends import _asyncio as _anyio_asyncio  # noqa: PLC0415
        worker = getattr(_anyio_asyncio, "WorkerThread", None)
        if worker is None or not hasattr(worker, "MAX_IDLE_TIME"):
            log.warning("loop health: anyio WorkerThread.MAX_IDLE_TIME not found — idle "
                        "worker threads keep anyio's default lifetime")
            return False
        if worker.MAX_IDLE_TIME < idle_s:
            worker.MAX_IDLE_TIME = idle_s
        return True
    except Exception:  # noqa: BLE001
        log.warning("loop health: could not tune anyio worker threads", exc_info=True)
        return False


async def prewarm(n: int = PREWARM_WORKERS, *, gap_s: float = PREWARM_GAP_S,
                  executor_workers: int = DEFAULT_EXECUTOR_WORKERS,
                  hold_max_s: float = PREWARM_HOLD_MAX_S) -> dict:
    """Start ``n`` anyio workers and ``n`` default-executor threads ahead of demand.

    Every warm-up call holds its thread until ALL submitted calls are running (or
    ``hold_max_s``), so each lands on its own thread and the pools really grow to ``n``.
    Returns the number of DISTINCT threads each pool ran on. Run as a background task."""
    loop = asyncio.get_running_loop()
    # Size the default executor only while none exists: replacing a live one would leave
    # its threads behind (asyncio does not shut the old executor down).
    if getattr(loop, "_default_executor", None) is None:
        try:
            loop.set_default_executor(ThreadPoolExecutor(
                max_workers=executor_workers, thread_name_prefix="corvin-exec"))
        except Exception:  # noqa: BLE001
            log.warning("loop health: could not size the default executor", exc_info=True)
    to_thread = None
    try:
        import anyio.to_thread as to_thread  # noqa: PLC0415
        limiter = to_thread.current_default_thread_limiter()
        if limiter.total_tokens < n:
            limiter.total_tokens = n
    except Exception:  # noqa: BLE001
        to_thread = None
        log.warning("loop health: anyio thread limiter unavailable", exc_info=True)

    kinds = (["anyio"] if to_thread is not None else []) + ["executor"]
    expected = n * len(kinds)
    cond = threading.Condition()
    running = {"count": 0}
    release = {"go": False}
    idents: dict[str, set[int]] = {k: set() for k in kinds}

    def _hold(kind: str) -> None:
        with cond:
            idents[kind].add(threading.get_ident())
            running["count"] += 1
            cond.notify_all()
            cond.wait_for(lambda: release["go"] or running["count"] >= expected,
                          timeout=hold_max_s)

    tasks: list[asyncio.Future] = []
    try:
        for _ in range(n):
            for kind in kinds:
                if kind == "anyio":
                    tasks.append(asyncio.ensure_future(to_thread.run_sync(_hold, kind)))
                else:
                    tasks.append(loop.run_in_executor(None, _hold, kind))
                await asyncio.sleep(gap_s)
    finally:
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        with cond:
            release["go"] = True
            cond.notify_all()
    return {k: len(v) for k, v in idents.items()}


# ── the folder browser's own pool (ADR-2322 Decision 8, REVIEW-0061 M1) ────────

#: threads of the dedicated folder-browser pool: the global cap of concurrent listings
BROWSE_POOL_WORKERS = 4

_browse_pool: Optional[ThreadPoolExecutor] = None
_browse_pool_lock = threading.Lock()


def browse_pool() -> ThreadPoolExecutor:
    """The ONE pool every folder listing runs on - never the default executor that ``asyncio.to_thread`` and the audit-chain
    writes share, so a listing hung on a dead mount can never hold an audit write. Long-lived (never shut down while the process
    runs); created on first use and pre-warmed by :func:`prewarm_browse_pool` from both hosts' lifespan."""
    global _browse_pool
    with _browse_pool_lock:
        if _browse_pool is None:
            _browse_pool = ThreadPoolExecutor(max_workers=BROWSE_POOL_WORKERS, thread_name_prefix="corvin-browse")
        return _browse_pool


async def prewarm_browse_pool(*, gap_s: float = PREWARM_GAP_S, hold_max_s: float = PREWARM_HOLD_MAX_S) -> int:
    """Start the browse pool's threads ahead of demand (each warm-up call holds its thread until all are running, so the pool
    really grows to ``BROWSE_POOL_WORKERS``). Returns the number of distinct threads."""
    return await _prewarm_pool(browse_pool(), BROWSE_POOL_WORKERS, gap_s=gap_s, hold_max_s=hold_max_s)


# ── the project-share pool (ADR-2323 Decision 12): background export jobs ──────

#: threads of the export-job pool: equal to the two export slots, so an admitted job never waits for a thread
SHARE_POOL_WORKERS = 2

_share_pool: Optional[ThreadPoolExecutor] = None
_share_pool_lock = threading.Lock()


def share_pool() -> ThreadPoolExecutor:
    """The long-lived pool background project exports run on (never a thread per request); pre-warmed from both hosts'
    lifespan by :func:`prewarm_share_pool`."""
    global _share_pool
    with _share_pool_lock:
        if _share_pool is None:
            _share_pool = ThreadPoolExecutor(max_workers=SHARE_POOL_WORKERS, thread_name_prefix="corvin-share")
        return _share_pool


async def prewarm_share_pool(*, gap_s: float = PREWARM_GAP_S, hold_max_s: float = PREWARM_HOLD_MAX_S) -> int:
    return await _prewarm_pool(share_pool(), SHARE_POOL_WORKERS, gap_s=gap_s, hold_max_s=hold_max_s)


async def _prewarm_pool(pool: ThreadPoolExecutor, n: int, *, gap_s: float, hold_max_s: float) -> int:
    cond = threading.Condition()
    seen: set[int] = set()

    def _hold() -> None:
        with cond:
            seen.add(threading.get_ident())
            cond.notify_all()
            cond.wait_for(lambda: len(seen) >= n, timeout=hold_max_s)

    futs = []
    for _ in range(n):
        futs.append(asyncio.wrap_future(pool.submit(_hold)))
        await asyncio.sleep(gap_s)
    await asyncio.gather(*futs, return_exceptions=True)
    return len(seen)


# ── process priority ────────────────────────────────────────────────────────

def raise_process_priority() -> bool:
    """ABOVE_NORMAL priority class on Windows (best-effort; no-op elsewhere)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415
        # a private WinDLL with typed signatures: untyped, the 64-bit pseudo-handle
        # (HANDLE)-1 is passed as a 32-bit int and the call fails
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.SetPriorityClass.restype = wintypes.BOOL
        ok = bool(k32.SetPriorityClass(k32.GetCurrentProcess(), _ABOVE_NORMAL_PRIORITY_CLASS))
        if not ok:
            log.warning("loop health: SetPriorityClass(ABOVE_NORMAL) failed (error %d)",
                        ctypes.get_last_error())  # type: ignore[attr-defined]
        return ok
    except Exception:  # noqa: BLE001
        log.warning("loop health: could not raise the process priority", exc_info=True)
        return False


# ── saturation signal ───────────────────────────────────────────────────────

def _note(*, thread_start_s: Optional[float] = None, loop_lag_s: Optional[float] = None,
          now: Optional[float] = None) -> None:
    global _last_saturated_at, _last_thread_start_s, _last_loop_lag_s
    now = time.monotonic() if now is None else now
    with _state_lock:
        if thread_start_s is not None:
            _last_thread_start_s = thread_start_s
            _probes.append(thread_start_s)
            slow = sum(1 for p in _probes if p > SATURATED_THREAD_START_S)
            if slow >= THREAD_PROBE_SLOW_NEEDED:
                _last_saturated_at = now
        if loop_lag_s is not None:
            _last_loop_lag_s = loop_lag_s
            if loop_lag_s > SATURATED_LOOP_LAG_S:
                _last_saturated_at = now


def host_saturated(now: Optional[float] = None) -> bool:
    """True while recent probes saw a severely lagging loop or mostly-slow thread starts.

    Only optional background work asks this. Without a running watchdog (tests, CLI
    tools) it is always False, i.e. nothing is skipped."""
    now = time.monotonic() if now is None else now
    with _state_lock:
        return _last_saturated_at > 0 and (now - _last_saturated_at) < SATURATION_HOLD_S


def status() -> dict:
    with _state_lock:
        return {"thread_start_s": _last_thread_start_s, "loop_lag_s": _last_loop_lag_s,
                "saturated": _last_saturated_at > 0
                and (time.monotonic() - _last_saturated_at) < SATURATION_HOLD_S}


def reset_for_tests() -> None:
    global _last_saturated_at, _last_thread_start_s, _last_loop_lag_s
    with _state_lock:
        _last_saturated_at = 0.0
        _last_thread_start_s = _last_loop_lag_s = None
        _probes.clear()


# ── watchdog ────────────────────────────────────────────────────────────────

def measure_thread_start() -> float:
    t0 = time.perf_counter()
    t = threading.Thread(target=lambda: None, name="corvin-thread-probe", daemon=True)
    t.start()
    t.join()
    return time.perf_counter() - t0


def _stall_log_path() -> Path:
    home = os.environ.get("CORVIN_HOME") or str(Path.home() / ".corvin")
    return Path(home) / "logs" / "loop-stall.log"


def _thread_stack(ident: int) -> str:
    """The loop thread's frames (file:line function) — code locations only, no locals."""
    frame = sys._current_frames().get(ident)
    if frame is None:
        return "  <loop thread frame unavailable>\n"
    return "".join(traceback.format_stack(frame))


def _write_stall(lag_s: float, path: Path, loop_ident: int) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if path.stat().st_size > STALL_LOG_MAX_BYTES:
                os.replace(path, path.with_name(path.name + ".1"))   # keep one older file
        except OSError:
            pass
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"=== {time.strftime('%Y-%m-%dT%H:%M:%S')} event loop lag {lag_s:.2f}s "
                     f"(last thread start {(_last_thread_start_s or 0.0):.3f}s)\n")
            fh.write(_thread_stack(loop_ident))
    except OSError:
        pass


def _watchdog_run(loop: asyncio.AbstractEventLoop, stop: threading.Event, loop_ident: int,
                  period_s: float, probe_s: float, stall_log_s: float,
                  log_path: Path) -> None:
    last_probe = 0.0
    last_stall_log = 0.0
    while not stop.wait(period_s):
        if loop.is_closed():
            return
        now = time.monotonic()
        if now - last_probe >= probe_s:
            last_probe = now
            try:
                _note(thread_start_s=measure_thread_start())
            except Exception:  # noqa: BLE001
                pass
            if stop.is_set():
                return
        ran = threading.Event()
        t0 = time.monotonic()
        try:
            loop.call_soon_threadsafe(ran.set)
        except RuntimeError:  # loop closed
            return
        logged = False
        # poll in short slices so stop() is honoured within ~0.2 s, never a full wait
        while not ran.wait(0.2):
            if stop.is_set():
                return
            lag = time.monotonic() - t0
            if lag < stall_log_s:
                continue
            _note(loop_lag_s=lag)
            if not logged and time.monotonic() - last_stall_log >= STALL_LOG_MIN_INTERVAL_S:
                last_stall_log = time.monotonic()
                logged = True
                log.warning("event loop has not run a callback for %.1fs — its stack is "
                            "in %s", lag, log_path)
                _write_stall(lag, log_path, loop_ident)
        _note(loop_lag_s=time.monotonic() - t0)


def start_watchdog(loop: asyncio.AbstractEventLoop, *, period_s: float = WATCHDOG_PERIOD_S,
                   probe_s: float = THREAD_PROBE_S, stall_log_s: float = STALL_LOG_S,
                   log_path: Optional[Path] = None) -> threading.Thread:
    """Start the one watchdog thread. Call it FROM the loop thread (its ident is the one
    whose stack a stall records). Each start owns its own stop event, so a previous
    watchdog that has not exited yet can never be revived."""
    global _watchdog, _watchdog_stop
    with _watchdog_lock:
        if _watchdog is not None and _watchdog.is_alive() and _watchdog_stop is not None \
                and not _watchdog_stop.is_set():
            return _watchdog
        stop = threading.Event()
        t = threading.Thread(
            target=_watchdog_run, name="corvin-loop-watchdog", daemon=True,
            args=(loop, stop, threading.get_ident(), period_s, probe_s, stall_log_s,
                  log_path or _stall_log_path()))
        _watchdog, _watchdog_stop = t, stop
    t.start()
    return t


def stop_watchdog(join_s: float = 0.0) -> None:
    """Signal the watchdog to exit. Default: no join — this runs on the event loop at
    shutdown, and the thread is a daemon that exits within ~0.2 s by itself."""
    global _watchdog, _watchdog_stop
    with _watchdog_lock:
        t, stop = _watchdog, _watchdog_stop
        _watchdog = _watchdog_stop = None
    if stop is not None:
        stop.set()
    if t is not None and join_s > 0:
        t.join(join_s)


# ── GC pause log ────────────────────────────────────────────────────────────

#: a cyclic-GC pass longer than this is logged (every thread stops for its duration)
GC_PAUSE_LOG_S = 0.5
_gc_started_at: dict[int, float] = {}
_gc_log_installed = False


def _gc_callback(phase: str, info: dict) -> None:
    gen = info.get("generation", -1)
    if phase == "start":
        _gc_started_at[gen] = time.perf_counter()
        return
    t0 = _gc_started_at.pop(gen, None)
    if t0 is None:
        return
    took = time.perf_counter() - t0
    if took >= GC_PAUSE_LOG_S:
        log.warning("gc: generation %d collection paused the process for %.2fs "
                    "(collected %s, uncollectable %s, tracked objects now %s)",
                    gen, took, info.get("collected"), info.get("uncollectable"),
                    sum(gc.get_count()))


def install_gc_pause_log() -> bool:
    """Log every cyclic-GC pass over ``GC_PAUSE_LOG_S``.

    A full (generation 2) collection walks every tracked container object and holds
    the GIL throughout: on a multi-GB heap it takes seconds, during which the event
    loop AND every worker thread stand still — a stall no thread stack can name, because
    the loop thread's frame is wherever the allocation that triggered it happened to be.
    The callback costs two ``perf_counter`` calls per pass."""
    global _gc_log_installed
    if _gc_log_installed:
        return True
    try:
        gc.callbacks.append(_gc_callback)
    except Exception:  # noqa: BLE001
        log.warning("loop health: could not install the gc pause log", exc_info=True)
        return False
    _gc_log_installed = True
    return True


# ── thread dump on demand ───────────────────────────────────────────────────

_thread_dump_installed = False


def install_thread_dump_signal() -> bool:
    """``kill -USR2 <pid>`` dumps every thread's stack to stderr (the journal).

    ``faulthandler`` writes from the signal handler in C — it works while the GIL is
    held by a busy thread or the loop is stalled, exactly when ``py-spy`` is wanted and,
    on a host with ``kernel.yama.ptrace_scope=1``, refused without sudo. Code locations
    only, never locals or request data. POSIX only (``faulthandler.register`` and
    ``SIGUSR2`` do not exist on Windows): there it returns False and nothing changes."""
    global _thread_dump_installed
    if _thread_dump_installed:
        return True
    sig = getattr(signal, "SIGUSR2", None)
    if sys.platform == "win32" or sig is None or not hasattr(faulthandler, "register"):
        return False
    # chain=False: with chain=True faulthandler restores the PREVIOUS disposition and
    # re-raises the signal after the dump — and the previous disposition of SIGUSR2 is
    # SIG_DFL, i.e. terminate. Measured 2026-11-07: the first dump was written and the
    # console died with status=12/USR2 right after it.
    try:
        faulthandler.register(sig, file=sys.stderr, all_threads=True, chain=False)
    except (AttributeError, ValueError, OSError, RuntimeError):
        log.warning("loop health: could not install the SIGUSR2 thread dump", exc_info=True)
        return False
    _thread_dump_installed = True
    return True


# ── lifespan wiring ─────────────────────────────────────────────────────────

def _under_pytest() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("CORVIN_PYTEST_SESSION_SANDBOX"))


def install_early() -> None:
    """First thing in the console lifespan: worker lifetime + process priority.

    Not under pytest (the functions are tested directly): never-retired workers and a
    raised priority would outlive each TestClient."""
    if _under_pytest():
        return
    try:
        tune_worker_threads()
        if raise_process_priority():
            log.info("loop health: process priority ABOVE_NORMAL")
        if install_thread_dump_signal():
            log.info("loop health: kill -USR2 %d dumps every thread's stack to the journal",
                     os.getpid())
        install_gc_pause_log()
    except Exception:  # noqa: BLE001
        log.warning("loop health: early install failed (console continues)", exc_info=True)


async def install_serving(app_state: object = None) -> None:
    """Right before the lifespan yields: the background pre-warm, then the watchdog.

    The watchdog starts only after the pre-warm (whose thread starts run on the loop by
    design) so neither the synchronous boot nor the warm-up is reported as a stall or
    marks the host saturated. Not under pytest: a watchdog in the test process would flip
    ``host_saturated()`` on a slow CI box and make optional-work tests skip."""
    if _under_pytest():
        return
    try:
        task = asyncio.ensure_future(_prewarm_then_watch())
        if app_state is not None:
            app_state.loop_health_task = task  # keep a reference (no GC'd task)
    except Exception:  # noqa: BLE001
        log.warning("loop health: install failed (console continues)", exc_info=True)


async def _prewarm_then_watch() -> None:
    t0 = time.monotonic()
    try:
        out = await prewarm()
        log.info("loop health: pre-warmed %s distinct threads in %.1fs",
                 out, time.monotonic() - t0)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        log.warning("loop health: pre-warm failed", exc_info=True)
    try:
        log.info("loop health: folder-browser pool pre-warmed (%d threads)", await prewarm_browse_pool())
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        log.warning("loop health: folder-browser pool pre-warm failed", exc_info=True)
    try:
        log.info("loop health: project-share pool pre-warmed (%d threads)", await prewarm_share_pool())
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        log.warning("loop health: project-share pool pre-warm failed", exc_info=True)
    try:
        start_watchdog(asyncio.get_running_loop())
    except Exception:  # noqa: BLE001
        log.warning("loop health: watchdog failed to start", exc_info=True)
