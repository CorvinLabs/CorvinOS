"""E2E tests for ADR-0703 §1.5 — License refresh daemon.

Tests:
  - Daemon initialization and thread lifecycle
  - Three refresh cycles (permit, CRL, ASRL) with independent timers
  - Cross-platform lock contention (POSIX + Windows)
  - Counter protocol (monotonic increment with fsync + rename)
  - State persistence and recovery
  - Lock unavailable handling + hourly de-duplication
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

import pytest


def test_refresh_daemon_state_serialization():
    """Test RefreshDaemonState serialization."""
    from corvin_operator.license.refresh_daemon import RefreshDaemonState

    state = RefreshDaemonState(cycle_count=5, last_crl_merge=1000, last_asrl_fetch=2000)
    data = state.to_dict()
    assert data["cycle_count"] == 5
    assert data["last_crl_merge"] == 1000
    assert data["last_asrl_fetch"] == 2000

    restored = RefreshDaemonState.from_dict(data)
    assert restored.cycle_count == 5
    assert restored.last_crl_merge == 1000


def test_refresh_daemon_initialization(tmp_path):
    """Test daemon initialization with custom corvin_home."""
    from corvin_operator.license.refresh_daemon import RefreshWorkerThread

    corvin_home = tmp_path / "corvin"
    daemon = RefreshWorkerThread(corvin_home)

    assert daemon.corvin_home == corvin_home
    assert daemon.license_dir == corvin_home / "global" / "license"
    assert daemon.state.cycle_count == 0


def test_refresh_daemon_state_persistence(tmp_path):
    """Test daemon state is persisted to disk."""
    from corvin_operator.license.refresh_daemon import RefreshWorkerThread

    corvin_home = tmp_path / "corvin"
    daemon = RefreshWorkerThread(corvin_home)

    # Modify state and save
    daemon.state.cycle_count = 42
    daemon.state.last_crl_merge = 1111
    daemon._save_state()

    # Verify file exists and has correct content
    state_file = corvin_home / "global" / "license" / "state.json"
    assert state_file.exists()
    data = json.loads(state_file.read_text())
    assert data["cycle_count"] == 42
    assert data["last_crl_merge"] == 1111

    # Create new daemon instance and verify it loads the state
    daemon2 = RefreshWorkerThread(corvin_home)
    assert daemon2.state.cycle_count == 42
    assert daemon2.state.last_crl_merge == 1111


def test_refresh_daemon_counter_increment(tmp_path):
    """Test counter protocol (monotonic increment with fsync + rename)."""
    from corvin_operator.license.refresh_daemon import RefreshWorkerThread

    corvin_home = tmp_path / "corvin"
    daemon = RefreshWorkerThread(corvin_home)

    # Increment counter multiple times
    c1 = daemon._increment_counter()
    c2 = daemon._increment_counter()
    c3 = daemon._increment_counter()

    assert c1 == 1
    assert c2 == 2
    assert c3 == 3

    # Verify counter file exists and is readable
    counter_file = corvin_home / "global" / "license" / "refresh_counter.json"
    assert counter_file.exists()
    data = json.loads(counter_file.read_text())
    assert data["n"] == 3


def test_refresh_daemon_lock_posix(tmp_path):
    """Test POSIX lock (fcntl) on systems that support it."""
    if sys.platform.startswith("win"):
        pytest.skip("POSIX lock test on Windows")

    from corvin_operator.license.refresh_daemon import RefreshWorkerThread

    corvin_home = tmp_path / "corvin"
    daemon = RefreshWorkerThread(corvin_home)

    # Acquire lock
    fd = daemon._acquire_lock()
    assert fd is not None

    # Try to acquire again from same thread (should fail on non-blocking lock)
    fd2 = daemon._acquire_lock()
    assert fd2 is None

    # Release
    daemon._release_lock(fd)

    # Now should be able to acquire again
    fd3 = daemon._acquire_lock()
    assert fd3 is not None
    daemon._release_lock(fd3)


def test_refresh_daemon_lock_windows(tmp_path):
    """Test Windows lock (msvcrt) on Windows."""
    if not sys.platform.startswith("win"):
        pytest.skip("Windows lock test on POSIX")

    from corvin_operator.license.refresh_daemon import RefreshWorkerThread

    corvin_home = tmp_path / "corvin"
    daemon = RefreshWorkerThread(corvin_home)

    # Acquire lock
    fd = daemon._acquire_lock()
    assert fd is not None

    # Try to acquire again (should fail)
    fd2 = daemon._acquire_lock()
    assert fd2 is None

    # Release
    daemon._release_lock(fd)


@pytest.fixture
def daemon_mod(monkeypatch):
    """refresh_daemon with its process-global singleton reset, and thread START
    recorded instead of spawning a real background thread (which would outlive
    the test and keep writing into a deleted tmp dir)."""
    from corvin_operator.license import refresh_daemon

    started: list = []
    monkeypatch.setattr(refresh_daemon, "_daemon_thread", None)
    monkeypatch.setattr(refresh_daemon.RefreshWorkerThread, "start",
                        lambda self: started.append(self))
    refresh_daemon._test_started = started  # type: ignore[attr-defined]
    return refresh_daemon


def _with_credential(tmp_path) -> Path:
    corvin_home = tmp_path / "corvin"
    (corvin_home / "global" / "license").mkdir(parents=True)
    (corvin_home / "global" / "license.key").write_text("test-jwt-credential")
    return corvin_home


def test_refresh_daemon_no_credential(tmp_path, daemon_mod):
    """No licence key → no daemon (free installs never refresh)."""
    corvin_home = tmp_path / "corvin"
    corvin_home.mkdir(parents=True)
    daemon_mod.start_background_daemon(str(corvin_home))
    assert daemon_mod._daemon_thread is None
    assert daemon_mod._test_started == []


def test_refresh_daemon_with_credential(tmp_path, daemon_mod):
    corvin_home = _with_credential(tmp_path)
    daemon_mod.start_background_daemon(str(corvin_home))
    thread = daemon_mod._daemon_thread
    assert isinstance(thread, daemon_mod.RefreshWorkerThread)
    assert thread.corvin_home == corvin_home and thread.daemon is True
    assert daemon_mod._test_started == [thread]


def test_refresh_daemon_idempotent_startup(tmp_path, daemon_mod):
    corvin_home = _with_credential(tmp_path)
    for _ in range(3):
        daemon_mod.start_background_daemon(str(corvin_home))
    assert len(daemon_mod._test_started) == 1
    assert daemon_mod._test_started[0] is daemon_mod._daemon_thread


def test_refresh_daemon_honours_corvin_home_env(tmp_path, daemon_mod, monkeypatch):
    corvin_home = _with_credential(tmp_path)
    monkeypatch.setenv("CORVIN_HOME", str(corvin_home))
    daemon_mod.start_background_daemon()
    assert daemon_mod._daemon_thread.corvin_home == corvin_home


def test_refresh_daemon_lock_unavailable_deduplication(tmp_path):
    """Hourly de-duplication of ``license.refresh_lock_unavailable``.

    ``_emit_lock_unavailable`` does ``from audit import audit_event`` after putting
    ``corvin_operator/bridges/shared`` on sys.path. The old patch target
    ``"operator.bridges.shared.audit.audit_event"`` resolved to the STDLIB
    ``operator`` module. Patch the module the daemon actually imports.
    """
    from corvin_operator.license.refresh_daemon import RefreshWorkerThread

    bridges_shared = str(Path(__file__).resolve().parents[2] / "corvin_operator" / "bridges" / "shared")
    if bridges_shared not in sys.path:
        sys.path.insert(0, bridges_shared)
    import audit as bridge_audit  # the same module object the daemon will import

    assert Path(bridge_audit.__file__).parent == Path(bridges_shared)

    daemon = RefreshWorkerThread(tmp_path / "corvin")
    emitted: list[str] = []

    def record(event_type: str, **kwargs):
        if event_type == "license.refresh_lock_unavailable":
            emitted.append(kwargs.get("reason", ""))

    with mock.patch.object(bridge_audit, "audit_event", side_effect=record):
        daemon._emit_lock_unavailable("test_reason")
        assert emitted == ["test_reason"]

        daemon._emit_lock_unavailable("test_reason")          # immediate repeat
        assert emitted == ["test_reason"]

        daemon._lock_failures["test_reason"] = int(time.time()) - 1800   # 30 min ago
        daemon._emit_lock_unavailable("test_reason")
        assert emitted == ["test_reason"]

        daemon._lock_failures["test_reason"] = int(time.time()) - 3600   # 1 h ago
        daemon._emit_lock_unavailable("test_reason")
        assert emitted == ["test_reason", "test_reason"]

        daemon._emit_lock_unavailable("other_reason")         # different reason
        assert emitted == ["test_reason", "test_reason", "other_reason"]


def _due(daemon, *, permit: bool, crl: bool, asrl: bool) -> int:
    from corvin_operator.license import refresh_daemon as rd

    now = int(time.time())
    daemon.state.last_permit_refresh = now - rd.PERMIT_REFRESH_INTERVAL - 1 if permit else now - 60
    daemon.state.last_crl_merge = now - rd.CRL_MERGE_INTERVAL - 1 if crl else now - 60
    daemon.state.last_asrl_fetch = now - rd.ASRL_FETCH_INTERVAL - 1 if asrl else now - 60
    return now


def test_refresh_daemon_three_cycles(tmp_path, monkeypatch):
    """All three cycles due → each runs and advances its own timer.

    The real ``_do_*`` methods run; only the network call behind the permit
    refresh (``session_refresh.refresh_once``) is replaced."""
    from corvin_operator.license import session_refresh
    from corvin_operator.license.refresh_daemon import RefreshWorkerThread

    permit_calls: list[int] = []
    monkeypatch.setattr(session_refresh, "refresh_once",
                        lambda timeout=10: permit_calls.append(timeout) or True)
    daemon = RefreshWorkerThread(tmp_path / "corvin")
    before = _due(daemon, permit=True, crl=True, asrl=True)

    daemon._do_permit_refresh()
    daemon._do_crl_merge()
    daemon._do_asrl_fetch()

    assert permit_calls == [10]
    assert daemon.state.last_permit_refresh >= before
    assert daemon.state.last_crl_merge >= before
    assert daemon.state.last_asrl_fetch >= before


def test_refresh_daemon_cycle_timers_independent(tmp_path, monkeypatch):
    """Only the permit cycle is due → CRL/ASRL timers stay put.

    The old version replaced the ``_do_*`` methods with mocks that ignore the
    timers and then asserted the mocks weren't called — after calling them."""
    from corvin_operator.license import session_refresh
    from corvin_operator.license.refresh_daemon import RefreshWorkerThread

    permit_calls: list[int] = []
    monkeypatch.setattr(session_refresh, "refresh_once",
                        lambda timeout=10: permit_calls.append(timeout) or True)
    daemon = RefreshWorkerThread(tmp_path / "corvin")
    now = _due(daemon, permit=True, crl=False, asrl=False)
    crl_before, asrl_before = daemon.state.last_crl_merge, daemon.state.last_asrl_fetch

    daemon._do_permit_refresh()
    daemon._do_crl_merge()
    daemon._do_asrl_fetch()

    assert permit_calls == [10]
    assert daemon.state.last_permit_refresh >= now
    assert daemon.state.last_crl_merge == crl_before
    assert daemon.state.last_asrl_fetch == asrl_before


def test_failed_permit_refresh_keeps_the_timer_due(tmp_path, monkeypatch):
    from corvin_operator.license import session_refresh
    from corvin_operator.license.refresh_daemon import RefreshWorkerThread

    monkeypatch.setattr(session_refresh, "refresh_once", lambda timeout=10: False)
    daemon = RefreshWorkerThread(tmp_path / "corvin")
    _due(daemon, permit=True, crl=False, asrl=False)
    stale = daemon.state.last_permit_refresh
    daemon._do_permit_refresh()
    assert daemon.state.last_permit_refresh == stale   # retried next cycle


def test_refresh_daemon_state_increments_cycle_count(tmp_path):
    """Counter + state file round-trip as one cycle of ``run`` performs them."""
    from corvin_operator.license.refresh_daemon import RefreshWorkerThread

    corvin_home = tmp_path / "corvin"
    daemon = RefreshWorkerThread(corvin_home)
    fd = daemon._acquire_lock()
    assert fd is not None
    try:
        assert daemon._increment_counter() == 1
        daemon.state.cycle_count += 1
        daemon._save_state()
    finally:
        daemon._release_lock(fd)

    state_file = corvin_home / "global" / "license" / "state.json"
    assert json.loads(state_file.read_text())["cycle_count"] == 1
    assert (state_file.stat().st_mode & 0o777) == 0o600
    assert RefreshWorkerThread(corvin_home).state.cycle_count == 1


# Integration: boot-time wiring
def test_boot_refresh_calls_daemon_start(tmp_path, monkeypatch):
    """``session_refresh.boot_refresh()`` starts the refresh daemon.

    The old patch target ``"operator.license.refresh_daemon.start_background_daemon"``
    resolved to the stdlib ``operator`` module. ``boot_refresh`` does
    ``from . import refresh_daemon as _rd; _rd.start_background_daemon()`` —
    patch that module attribute. The network refresh is stubbed out."""
    from corvin_operator.license import refresh_daemon, session_refresh

    corvin_home = _with_credential(tmp_path)
    monkeypatch.setenv("CORVIN_HOME", str(corvin_home))
    monkeypatch.setattr(session_refresh, "should_refresh", lambda: False)
    monkeypatch.setattr(session_refresh, "refresh_once",
                        lambda **k: pytest.fail("boot must not refresh when not due"))

    calls: list[tuple] = []
    monkeypatch.setattr(refresh_daemon, "start_background_daemon",
                        lambda *a, **k: calls.append((a, k)))
    session_refresh.boot_refresh()
    assert calls == [((), {})]


def test_boot_refresh_survives_a_daemon_start_failure(tmp_path, monkeypatch):
    """boot_refresh never raises — a broken daemon start must not fail boot."""
    from corvin_operator.license import refresh_daemon, session_refresh

    monkeypatch.setattr(session_refresh, "should_refresh", lambda: False)

    def boom(*a, **k):
        raise RuntimeError("no threads for you")

    monkeypatch.setattr(refresh_daemon, "start_background_daemon", boom)
    session_refresh.boot_refresh()
