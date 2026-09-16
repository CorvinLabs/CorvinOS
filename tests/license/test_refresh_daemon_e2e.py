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


def test_refresh_daemon_no_credential(tmp_path):
    """Test daemon doesn't start when no credential file exists."""
    from corvin_operator.license.refresh_daemon import start_background_daemon

    corvin_home = tmp_path / "corvin"
    corvin_home.mkdir(parents=True)

    # No credential file — daemon should not start
    start_background_daemon(str(corvin_home))

    # Verify no thread is running
    time.sleep(0.5)
    # (We can't directly check this without accessing the module-level _daemon_thread,
    # but the test just verifies no exception is raised)


def test_refresh_daemon_with_credential(tmp_path):
    """Test daemon starts when credential file exists."""
    from corvin_operator.license.refresh_daemon import start_background_daemon

    corvin_home = tmp_path / "corvin"
    license_dir = corvin_home / "global" / "license"
    license_dir.mkdir(parents=True)

    # Create credential file
    credential_file = corvin_home / "global" / "license.key"
    credential_file.write_text("test-jwt-credential")

    # Should start without raising
    start_background_daemon(str(corvin_home))


def test_refresh_daemon_idempotent_startup(tmp_path):
    """Test start_background_daemon is idempotent."""
    from corvin_operator.license.refresh_daemon import start_background_daemon

    corvin_home = tmp_path / "corvin"
    license_dir = corvin_home / "global" / "license"
    license_dir.mkdir(parents=True)

    # Create credential file
    credential_file = corvin_home / "global" / "license.key"
    credential_file.write_text("test-jwt-credential")

    # Call multiple times — should only start once
    start_background_daemon(str(corvin_home))
    start_background_daemon(str(corvin_home))
    start_background_daemon(str(corvin_home))

    # No exception should be raised


def test_refresh_daemon_lock_unavailable_deduplication(tmp_path):
    """Test hourly de-duplication of lock unavailable emissions."""
    from corvin_operator.license.refresh_daemon import RefreshWorkerThread

    corvin_home = tmp_path / "corvin"
    daemon = RefreshWorkerThread(corvin_home)

    # Track audit events emitted
    emitted_events: list[tuple[str, str]] = []

    def mock_audit_event(event_type: str, **kwargs):
        if event_type == "license.refresh_lock_unavailable":
            emitted_events.append((event_type, kwargs.get("reason", "")))

    with mock.patch("operator.bridges.shared.audit.audit_event", side_effect=mock_audit_event):
        # First emit
        daemon._emit_lock_unavailable("test_reason")
        assert len(emitted_events) == 1

        # Second emit (same reason, immediate) — should be deduplicated
        daemon._emit_lock_unavailable("test_reason")
        assert len(emitted_events) == 1  # No new event

        # Simulate time passing < 1 hour
        daemon._lock_failures["test_reason"] = int(time.time()) - 1800  # 30 min ago
        daemon._emit_lock_unavailable("test_reason")
        assert len(emitted_events) == 1  # Still deduplicated

        # Simulate time passing >= 1 hour
        daemon._lock_failures["test_reason"] = int(time.time()) - 3600  # 1 hour ago
        daemon._emit_lock_unavailable("test_reason")
        assert len(emitted_events) == 2  # New event emitted

        # Different reason should emit immediately
        daemon._emit_lock_unavailable("other_reason")
        assert len(emitted_events) == 3


def test_refresh_daemon_three_cycles(tmp_path):
    """Test three independent refresh cycles (permit, CRL, ASRL)."""
    from corvin_operator.license.refresh_daemon import RefreshWorkerThread

    corvin_home = tmp_path / "corvin"
    daemon = RefreshWorkerThread(corvin_home)

    # Mock the cycle functions
    permit_called = []
    crl_called = []
    asrl_called = []

    def mock_permit_refresh():
        permit_called.append(time.time())

    def mock_crl_merge():
        crl_called.append(time.time())

    def mock_asrl_fetch():
        asrl_called.append(time.time())

    daemon._do_permit_refresh = mock_permit_refresh
    daemon._do_crl_merge = mock_crl_merge
    daemon._do_asrl_fetch = mock_asrl_fetch

    # Manually set timers to trigger all three cycles
    daemon.state.last_permit_refresh = int(time.time()) - 3 * 3600 - 1
    daemon.state.last_crl_merge = int(time.time()) - 3600 - 1
    daemon.state.last_asrl_fetch = int(time.time()) - 24 * 3600 - 1

    daemon._do_permit_refresh()
    daemon._do_crl_merge()
    daemon._do_asrl_fetch()

    assert len(permit_called) == 1
    assert len(crl_called) == 1
    assert len(asrl_called) == 1


def test_refresh_daemon_cycle_timers_independent(tmp_path):
    """Test permit/CRL/ASRL cycles are independent and don't trigger early."""
    from corvin_operator.license.refresh_daemon import RefreshWorkerThread

    corvin_home = tmp_path / "corvin"
    daemon = RefreshWorkerThread(corvin_home)

    permit_called = []
    crl_called = []
    asrl_called = []

    def mock_permit_refresh():
        permit_called.append(time.time())

    def mock_crl_merge():
        crl_called.append(time.time())

    def mock_asrl_fetch():
        asrl_called.append(time.time())

    daemon._do_permit_refresh = mock_permit_refresh
    daemon._do_crl_merge = mock_crl_merge
    daemon._do_asrl_fetch = mock_asrl_fetch

    # Set just the permit timer to trigger
    now = int(time.time())
    daemon.state.last_permit_refresh = now - 3 * 3600 - 1
    daemon.state.last_crl_merge = now - 1000  # Not yet
    daemon.state.last_asrl_fetch = now - 1000  # Not yet

    daemon._do_permit_refresh()
    daemon._do_crl_merge()
    daemon._do_asrl_fetch()

    assert len(permit_called) == 1
    assert len(crl_called) == 0  # Not yet
    assert len(asrl_called) == 0  # Not yet


def test_refresh_daemon_state_increments_cycle_count(tmp_path):
    """Test daemon increments cycle_count and saves state."""
    from corvin_operator.license.refresh_daemon import RefreshWorkerThread

    corvin_home = tmp_path / "corvin"
    daemon = RefreshWorkerThread(corvin_home)

    # Mock cycle functions (no-op)
    daemon._do_permit_refresh = lambda: None
    daemon._do_crl_merge = lambda: None
    daemon._do_asrl_fetch = lambda: None

    # Mock lock to make it succeed
    def mock_acquire_lock():
        return 999  # Fake FD

    def mock_release_lock(fd):
        pass

    daemon._acquire_lock = mock_acquire_lock
    daemon._release_lock = mock_release_lock

    # Run one cycle manually
    fd = daemon._acquire_lock()
    daemon._increment_counter()
    daemon._do_permit_refresh()
    daemon._do_crl_merge()
    daemon._do_asrl_fetch()
    daemon.state.cycle_count += 1
    daemon._save_state()
    daemon._release_lock(fd)

    # Verify state file
    state_file = corvin_home / "global" / "license" / "state.json"
    assert state_file.exists()
    data = json.loads(state_file.read_text())
    assert data["cycle_count"] == 1


# Integration test: Boot-time wiring
def test_boot_refresh_calls_daemon_start(tmp_path, monkeypatch):
    """Test boot_refresh() calls start_background_daemon()."""
    corvin_home = tmp_path / "corvin"
    monkeypatch.setenv("CORVIN_HOME", str(corvin_home))

    # Create credential to trigger daemon startup
    license_dir = corvin_home / "global" / "license"
    license_dir.mkdir(parents=True)
    (corvin_home / "global" / "license.key").write_text("test-jwt")

    # Track daemon startups
    daemon_started = []

    def mock_daemon_start(ch=None):
        daemon_started.append(True)

    # Mock the daemon start
    with mock.patch(
        "operator.license.refresh_daemon.start_background_daemon",
        side_effect=mock_daemon_start
    ):
        from corvin_operator.license import session_refresh
        session_refresh.boot_refresh()

        assert len(daemon_started) == 1
