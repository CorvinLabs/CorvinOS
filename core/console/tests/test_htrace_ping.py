"""Tests for the anonymous instance-count ping (ADR-0180 §3) —
adversarial review findings: TOCTOU race in ping_if_due, and the
one-shot-instead-of-recurring ping at corvin-serve startup.

Verifies:
  - ping_if_due() locks the check-then-send-then-stamp sequence, so a
    concurrent caller (lock already held) never sends a duplicate ping.
  - ping_loop() re-invokes ping_if_due() repeatedly (not just once).
  - start_ping_thread() is idempotent — only ever starts one thread per
    process, even if called multiple times.
"""
from __future__ import annotations

import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from corvin_console.aco import htrace_uploader as hu


@pytest.fixture(autouse=True)
def _reset_ping_thread_state():
    """start_ping_thread's idempotency guard is module-global — reset it
    around every test so tests don't leak state into each other."""
    orig = hu._ping_thread_started
    hu._ping_thread_started = False
    yield
    hu._ping_thread_started = orig


def _make_home(tmp_path: Path) -> Path:
    home = tmp_path / ".corvin"
    (home / "aco" / "telemetry").mkdir(parents=True, exist_ok=True)
    return home


def test_ping_if_due_skips_network_call_when_lock_already_held(tmp_path):
    """Simulates the exact race: another process (or thread) already holds
    the ping lock when this call arrives — it must return True (not an
    error) and must NOT send a second ping for the same instance-day."""
    if not hu._HAS_FLOCK:
        pytest.skip("flock not available on this platform")
    import fcntl as _fcntl

    home = _make_home(tmp_path)
    with (
        patch.object(hu, "ping_enabled", return_value=True),
        patch.object(hu, "ensure_ping_tokens", return_value=True),
    ):
        lock_path = hu.htrace_dir(home) / hu._PING_LOCK_FILENAME
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        holder = lock_path.open("w")
        _fcntl.flock(holder, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        try:
            with patch("urllib.request.urlopen") as mock_urlopen:
                result = hu.ping_if_due(home)
        finally:
            _fcntl.flock(holder, _fcntl.LOCK_UN)
            holder.close()

    assert result is True
    mock_urlopen.assert_not_called()


def test_ping_if_due_sends_when_lock_is_free(tmp_path):
    """Sanity counterpart: with no contention, a due ping actually sends."""
    home = _make_home(tmp_path)
    mock_resp = type("R", (), {"getcode": lambda self: 200})()
    mock_ctx = type("Ctx", (), {
        "__enter__": lambda self: mock_resp,
        "__exit__": lambda self, *a: False,
    })()

    with (
        patch.object(hu, "ping_enabled", return_value=True),
        patch.object(hu, "ensure_ping_tokens", return_value=True),
        patch.object(hu, "_last_ping_path", return_value=tmp_path / "last_ping"),
        patch.object(hu, "_load_telemetry_token", return_value="tok"),
        patch.object(hu, "_load_instance_token", return_value="itok"),
        patch.object(hu, "load_or_create_instance_id", return_value="iid"),
        patch.object(hu, "_detect_active_engine", return_value="claude_code"),
        patch("urllib.request.urlopen", return_value=mock_ctx) as mock_urlopen,
    ):
        result = hu.ping_if_due(home)

    assert result is True
    mock_urlopen.assert_called_once()


def test_ping_loop_reinvokes_ping_if_due_repeatedly():
    """The recurring loop must call ping_if_due() more than once — locks in
    the fix for the "sent once at boot, never again" undercounting bug."""
    calls = []

    def _fake_ping_if_due(home):
        calls.append(home)
        if len(calls) >= 3:
            raise SystemExit  # break out of the infinite loop for the test
        return True

    with (
        patch.object(hu, "ping_if_due", _fake_ping_if_due),
        patch.object(hu.time, "sleep", lambda _s: None),  # no real waiting
    ):
        with pytest.raises(SystemExit):
            hu.ping_loop(Path("/fake/home"))

    assert len(calls) == 3


def test_ping_if_due_still_sends_after_a_backward_clock_jump(tmp_path):
    """A negative `age` (backward clock jump — NTP correction, VM/container
    clock skew on boot) must NOT be treated as "already sent today". Before
    the fix, `age < _PING_INTERVAL_S` was true for ANY negative age, so a
    clock that jumps backward on every boot suppressed the ping forever."""
    home = _make_home(tmp_path)
    stamp = tmp_path / "last_ping"
    stamp.write_text("x", encoding="utf-8")
    # Backdate the stamp's mtime into the FUTURE relative to "now" so
    # time.time() - mtime is negative, simulating a backward clock jump.
    future = hu.time.time() + 10_000
    import os as _os
    _os.utime(stamp, (future, future))

    mock_resp = type("R", (), {"getcode": lambda self: 200})()
    mock_ctx = type("Ctx", (), {
        "__enter__": lambda self: mock_resp,
        "__exit__": lambda self, *a: False,
    })()

    with (
        patch.object(hu, "ping_enabled", return_value=True),
        patch.object(hu, "ensure_ping_tokens", return_value=True),
        patch.object(hu, "_last_ping_path", return_value=stamp),
        patch.object(hu, "_load_telemetry_token", return_value="tok"),
        patch.object(hu, "_load_instance_token", return_value="itok"),
        patch.object(hu, "load_or_create_instance_id", return_value="iid"),
        patch.object(hu, "_detect_active_engine", return_value="claude_code"),
        patch("urllib.request.urlopen", return_value=mock_ctx) as mock_urlopen,
    ):
        result = hu.ping_if_due(home)

    assert result is True
    mock_urlopen.assert_called_once(), (
        "a backward clock jump must not suppress the ping indefinitely"
    )


def test_ping_if_due_provisions_tokens_inside_the_lock_not_before(tmp_path):
    """ensure_ping_tokens() must run AFTER the flock is acquired — calling it
    before the lock (the pre-fix ordering) let two racing processes both
    provision tokens concurrently, risking a mismatched instance/telemetry
    token pair. Verified by asserting ensure_ping_tokens is only invoked
    while the lock file is actually held."""
    if not hu._HAS_FLOCK:
        pytest.skip("flock not available on this platform")
    import fcntl as _fcntl

    home = _make_home(tmp_path)
    observed_locked_during_provision = []

    def _fake_ensure_tokens(_home):
        lock_path = hu.htrace_dir(home) / hu._PING_LOCK_FILENAME
        probe = lock_path.open("w")
        try:
            _fcntl.flock(probe, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            observed_locked_during_provision.append(False)  # we got it — not locked
            _fcntl.flock(probe, _fcntl.LOCK_UN)
        except (OSError, BlockingIOError):
            observed_locked_during_provision.append(True)  # someone else holds it
        finally:
            probe.close()
        return False  # stop here — no need to actually send a ping

    with (
        patch.object(hu, "ping_enabled", return_value=True),
        patch.object(hu, "ensure_ping_tokens", side_effect=_fake_ensure_tokens),
    ):
        hu.ping_if_due(home)

    assert observed_locked_during_provision == [True], (
        "ensure_ping_tokens() must run while ping_if_due's own lock is held"
    )


def test_start_ping_thread_is_idempotent():
    """Calling start_ping_thread() twice must only ever start ONE thread —
    matches the pattern already used by start_heartbeat_thread()."""
    started_threads = []
    orig_thread = threading.Thread

    def _tracking_thread(*a, **k):
        t = orig_thread(*a, **k)
        started_threads.append(t)
        return t

    with (
        patch.object(hu, "ping_loop", lambda home: None),
        patch("threading.Thread", side_effect=_tracking_thread),
    ):
        hu.start_ping_thread(Path("/fake/home"))
        hu.start_ping_thread(Path("/fake/home"))

    assert len(started_threads) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
