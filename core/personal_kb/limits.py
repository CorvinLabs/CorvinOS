"""Attempt limits that cannot be turned against the owner (ADR-2326 item 4).

* No lockout. A failed unlock delays the next attempt for that handle (1, 2, 4 ... capped at 30 s,
  state on disk under ``HMAC(pepper, handle)``) and backs off per session (up to 5 minutes, in memory).
* Scrypt runs in a dedicated pool of 2 threads with at most 8 waiting; beyond that the request is
  refused at once with :class:`Busy`, before any scrypt starts. Never on the shared executor.
* Owner creation: at most 5 per minute per process.
"""
from __future__ import annotations

import collections
import contextlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

from . import locks, paths

T = TypeVar("T")

POOL_WORKERS = 2
QUEUE_DEPTH = 8
HANDLE_DELAY_CAP_S = 30.0
SESSION_BACKOFF_CAP_S = 300.0
CREATIONS_PER_MINUTE = 5

_pool = ThreadPoolExecutor(max_workers=POOL_WORKERS, thread_name_prefix="personal-kb-kdf")
_inflight = 0
_inflight_lock = threading.Lock()
_session_fail: dict[str, tuple[int, float]] = {}
_creations: collections.deque[float] = collections.deque()
_state_lock = threading.Lock()


class Busy(RuntimeError):
    """Too many key derivations in flight; retry shortly (HTTP 429)."""


class TooSoon(RuntimeError):
    def __init__(self, wait_s: float) -> None:
        super().__init__("try again later")
        self.wait_s = max(0.0, wait_s)


def run_kdf(fn: Callable[[], T]) -> T:
    """Run a key derivation in the dedicated pool, refusing at once when the queue is full."""
    global _inflight
    with _inflight_lock:
        if _inflight >= POOL_WORKERS + QUEUE_DEPTH:
            raise Busy("key derivation queue is full")
        _inflight += 1
    try:
        return _pool.submit(fn).result()
    finally:
        with _inflight_lock:
            _inflight -= 1


def _load(tenant_id: str) -> dict[str, Any]:
    try:
        return json.loads(paths.lockouts_file(tenant_id).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _save(tenant_id: str, data: dict[str, Any]) -> None:
    locks.atomic_write(paths.lockouts_file(tenant_id), json.dumps(data).encode("utf-8"))


_handle_inflight: set[tuple[str, str]] = set()


@contextlib.contextmanager
def handle_attempt(tenant_id: str, handle_key: str):
    """One passphrase check per handle at a time in this process: parallel requests cannot run several
    guesses inside one delay window (the console is the only process that checks passphrases)."""
    k = (tenant_id, handle_key)
    with _state_lock:
        if k in _handle_inflight:
            raise TooSoon(1.0)
        _handle_inflight.add(k)
    try:
        yield
    finally:
        with _state_lock:
            _handle_inflight.discard(k)


def check_handle(tenant_id: str, handle_key: str, now: float | None = None) -> None:
    ts = time.time() if now is None else now
    rec = _load(tenant_id).get(handle_key)
    if rec and float(rec.get("next_ok", 0)) > ts:
        raise TooSoon(float(rec["next_ok"]) - ts)


def record_handle_failure(tenant_id: str, handle_key: str, now: float | None = None) -> int:
    ts = time.time() if now is None else now
    if not paths.root(tenant_id).is_dir():
        return 0
    with locks.exclusive(paths.tenant_lock_file(tenant_id)):
        data = _load(tenant_id)
        rec = data.get(handle_key) or {"fails": 0}
        fails = int(rec.get("fails", 0)) + 1
        delay = min(HANDLE_DELAY_CAP_S, float(2 ** min(fails - 1, 10)))
        data[handle_key] = {"fails": fails, "next_ok": ts + delay}
        _save(tenant_id, data)
        return fails


def clear_handle(tenant_id: str, handle_key: str) -> None:
    if not paths.root(tenant_id).is_dir():
        return
    with locks.exclusive(paths.tenant_lock_file(tenant_id)):
        data = _load(tenant_id)
        if data.pop(handle_key, None) is not None:
            _save(tenant_id, data)


def check_session(session_key: str, now: float | None = None) -> None:
    ts = time.time() if now is None else now
    with _state_lock:
        rec = _session_fail.get(session_key)
    if rec and rec[1] > ts:
        raise TooSoon(rec[1] - ts)


def record_session_failure(session_key: str, now: float | None = None) -> None:
    ts = time.time() if now is None else now
    with _state_lock:
        fails = _session_fail.get(session_key, (0, 0.0))[0] + 1
        _session_fail[session_key] = (fails, ts + min(SESSION_BACKOFF_CAP_S, float(2 ** min(fails - 1, 12))))


def clear_session(session_key: str) -> None:
    with _state_lock:
        _session_fail.pop(session_key, None)


def check_creation(now: float | None = None) -> None:
    ts = time.time() if now is None else now
    with _state_lock:
        while _creations and _creations[0] < ts - 60:
            _creations.popleft()
        if len(_creations) >= CREATIONS_PER_MINUTE:
            raise TooSoon(60 - (ts - _creations[0]))
        _creations.append(ts)


def _reset_for_tests() -> None:
    with _state_lock:
        _session_fail.clear()
        _creations.clear()
        _handle_inflight.clear()
