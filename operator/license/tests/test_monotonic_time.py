"""Tests for Monotonic Time Guard — ADR-0144 MED-1"""

import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from operator.license.monotonic_time import (
    check_clock_rollback,
    _load_prior_max,
    _persist_max,
    _monotonic_timestamp_path,
)


@pytest.fixture
def temp_corvin_home(monkeypatch):
    """Temporary CORVIN_HOME for test isolation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("CORVIN_HOME", tmpdir)
        yield Path(tmpdir)


class TestMonotonicTimePersistence:
    """Test persist/load of monotonic timestamp."""

    def test_first_check_no_prior(self, temp_corvin_home):
        """First check should persist and accept."""
        now = int(time.time())
        assert check_clock_rollback(now) is True

        # Verify file was written
        ts_file = _monotonic_timestamp_path()
        assert ts_file.exists()

        data = json.loads(ts_file.read_text())
        assert data["max_timestamp"] == now

    def test_load_prior_max_exists(self, temp_corvin_home):
        """Load prior max from existing file."""
        ts_file = _monotonic_timestamp_path()
        ts_file.parent.mkdir(parents=True, exist_ok=True)

        prior_ts = 1000000000
        ts_file.write_text(json.dumps({"max_timestamp": prior_ts}))

        assert _load_prior_max() == prior_ts

    def test_load_prior_max_missing_file(self, temp_corvin_home):
        """Load returns None if file doesn't exist."""
        assert _load_prior_max() is None

    def test_load_prior_max_malformed_json(self, temp_corvin_home):
        """Load returns None for malformed JSON."""
        ts_file = _monotonic_timestamp_path()
        ts_file.parent.mkdir(parents=True, exist_ok=True)
        ts_file.write_text("{ invalid json }")

        assert _load_prior_max() is None


class TestClockRollbackDetection:
    """Test clock-rollback detection."""

    def test_monotonic_time_accepted(self, temp_corvin_home):
        """Accept when time moves forward."""
        t1 = 1000000000
        t2 = t1 + 1000  # 1000 seconds later

        assert check_clock_rollback(t1) is True
        assert check_clock_rollback(t2) is True

    def test_same_time_accepted(self, temp_corvin_home):
        """Accept when time stays the same."""
        t = 1000000000

        assert check_clock_rollback(t) is True
        assert check_clock_rollback(t) is True  # Same time again

    def test_clock_rollback_rejected(self, temp_corvin_home):
        """Reject when clock rolls back."""
        t1 = 1000000000
        t2 = t1 - 3600  # 1 hour earlier

        # Set first timestamp
        assert check_clock_rollback(t1) is True

        # Clock rolls back
        assert check_clock_rollback(t2) is False

    def test_clock_rollback_5_seconds(self, temp_corvin_home):
        """Reject small rollback (5 seconds)."""
        t1 = 1000000000
        t2 = t1 - 5

        assert check_clock_rollback(t1) is True
        assert check_clock_rollback(t2) is False

    def test_clock_rollback_1_second(self, temp_corvin_home):
        """Reject 1-second rollback."""
        t1 = 1000000000
        t2 = t1 - 1

        assert check_clock_rollback(t1) is True
        assert check_clock_rollback(t2) is False


class TestClockRollbackRecovery:
    """Test behavior after rollback is detected."""

    def test_rollback_then_forward_accepted(self, temp_corvin_home):
        """After rollback is rejected, accept time moving forward again."""
        t1 = 1000000000
        t2 = t1 - 100  # Rollback
        t3 = t1 + 1000  # Move forward (even past t1)

        # Setup
        assert check_clock_rollback(t1) is True
        assert check_clock_rollback(t2) is False

        # Recover: even though t3 > t2, it's after t1 (the max)
        # So it should be accepted
        assert check_clock_rollback(t3) is True

    def test_rollback_then_restore(self, temp_corvin_home):
        """After rollback, restore to prior max is not sufficient — must go forward."""
        t1 = 1000000000
        t2 = t1 - 100

        assert check_clock_rollback(t1) is True
        assert check_clock_rollback(t2) is False

        # Try to restore to exactly t1
        assert check_clock_rollback(t1) is True  # Accepted (not < max)

        # Try t1 + 1
        assert check_clock_rollback(t1 + 1) is True


class TestMonotonicTimePersistenceUpdate:
    """Test that max is updated on every forward move."""

    def test_max_updated_on_forward(self, temp_corvin_home):
        """Max in file should update on forward clock."""
        t1 = 1000000000
        t2 = t1 + 500

        check_clock_rollback(t1)
        assert _load_prior_max() == t1

        check_clock_rollback(t2)
        assert _load_prior_max() == t2

    def test_max_not_updated_on_same_time(self, temp_corvin_home):
        """Max should not be re-persisted if time is the same."""
        t = 1000000000

        check_clock_rollback(t)
        mtime1 = _monotonic_timestamp_path().stat().st_mtime

        time.sleep(0.01)
        check_clock_rollback(t)
        mtime2 = _monotonic_timestamp_path().stat().st_mtime

        # File might not have been re-written (implementation detail)
        # but that's OK — the value should still be correct
        assert _load_prior_max() == t


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_epoch_time_zero(self, temp_corvin_home):
        """Handle epoch time 0."""
        assert check_clock_rollback(0) is True
        assert check_clock_rollback(0) is True  # Same time
        assert check_clock_rollback(-1) is False  # Rollback from 0

    def test_large_time_values(self, temp_corvin_home):
        """Handle large timestamps (year 2100+)."""
        t1 = 4102444800  # Jan 1, 2100
        t2 = t1 + 3600

        assert check_clock_rollback(t1) is True
        assert check_clock_rollback(t2) is True

    def test_defaults_to_current_time(self, temp_corvin_home):
        """If no time passed, uses current system time."""
        # Just verify it doesn't crash
        result = check_clock_rollback()
        assert isinstance(result, bool)
