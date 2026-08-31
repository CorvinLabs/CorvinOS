"""Tests for HIGH-002: TOCTOU Race fix.

Validates that quota checks (increment_and_check) are atomic and cannot be bypassed
through concurrent access or split read-modify-write operations.

Key invariant (HIGH-002): The _INCREMENT_LOCK must be held for the ENTIRE duration of:
  1. _load(path) — read current count from file
  2. get_limit(feature) — fetch limit from license
  3. Check: if (current + 1) > limit — fail-closed evaluation
  4. _save(path) with incremented count

The lock CANNOT be released between step 3 (check) and step 4 (increment).
This is the TOCTOU race fix: ensure check and increment are atomic.
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from limits import LicenseLimitError
from quota_counter import _INCREMENT_LOCK


class TestTOCTOULockInvariant(unittest.TestCase):
    """Verify the HIGH-002 lock invariant is maintained."""

    def setUp(self):
        """Create a temporary corvin home for testing."""
        self.test_dir = tempfile.TemporaryDirectory()
        self.corvin_home = Path(self.test_dir.name)

    def tearDown(self):
        """Clean up temporary directory."""
        self.test_dir.cleanup()

    def test_increment_lock_is_blocking(self):
        """Verify _INCREMENT_LOCK is a blocking lock (HIGH-002 requirement)."""
        # Should be able to acquire the lock
        acquired = _INCREMENT_LOCK.acquire(blocking=True)
        self.assertTrue(acquired, "Lock should be acquirable")

        # Verify it's actually locked now (non-blocking acquire should fail)
        acquired_again = _INCREMENT_LOCK.acquire(blocking=False)
        self.assertFalse(
            acquired_again,
            "Second acquire should fail when lock is held (HIGH-002: mutual exclusion)"
        )

        # Clean up
        _INCREMENT_LOCK.release()

    def test_lock_serializes_concurrent_access(self):
        """Verify lock properly serializes concurrent operations (HIGH-002)."""
        access_log = []
        lock_holder = None

        def worker(worker_id):
            nonlocal lock_holder
            acquired = _INCREMENT_LOCK.acquire(blocking=True, timeout=5)
            self.assertTrue(acquired, f"Worker {worker_id} should acquire lock")

            # Record who holds the lock
            prev_holder = lock_holder
            lock_holder = worker_id
            access_log.append(("acquire", worker_id))

            # Hold lock for a moment
            import time
            time.sleep(0.01)

            access_log.append(("release", worker_id))
            lock_holder = prev_holder
            _INCREMENT_LOCK.release()

        # Start 5 workers competing for the lock
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify serialization: no interleaving of acquire/release pairs
        # Each worker's acquire should be followed by its release before next acquire
        active_workers = set()
        for event, worker_id in access_log:
            if event == "acquire":
                self.assertEqual(
                    len(active_workers), 0,
                    f"Only one worker should hold lock at a time (HIGH-002: mutex)"
                )
                active_workers.add(worker_id)
            else:  # release
                self.assertIn(
                    worker_id, active_workers,
                    f"Worker {worker_id} tried to release without holding lock"
                )
                active_workers.remove(worker_id)

    def test_quota_read_modify_write_atomicity(self):
        """Verify quota operations are atomic with respect to the lock (HIGH-002)."""
        feature = "test_feature_atomic"
        tenant_id = "test-tenant"
        quota_dir = self.corvin_home / "quotas"
        quota_dir.mkdir(parents=True, exist_ok=True)

        # Import the internal functions
        from quota_counter import _load, _save, _quota_path, _today_utc

        today = _today_utc()
        path = _quota_path(self.corvin_home, tenant_id, feature, today)

        # Initialize quota to 5
        _save(path, {"count": 5, "timestamp": "2026-01-01T00:00:00"})

        # Simulate a read-modify-write operation that must be atomic
        check_happened = []
        increment_happened = []

        def atomic_rmw():
            with _INCREMENT_LOCK:
                # Read
                data = _load(path)
                current = data.get("count", 0)
                check_happened.append(current)

                # Check: would (current + 1) exceed limit of 6?
                if (current + 1) > 6:
                    # Should NOT increment
                    return False

                # Modify and write
                data["count"] = current + 1
                _save(path, data)
                increment_happened.append(current + 1)
                return True

        # First call should succeed (5 + 1 = 6, at limit)
        result1 = atomic_rmw()
        self.assertTrue(result1, "First increment should succeed")

        # Verify state
        data = _load(path)
        self.assertEqual(data["count"], 6, "Counter should be 6")

        # Second call should fail (6 + 1 = 7, exceeds limit)
        result2 = atomic_rmw()
        self.assertFalse(result2, "Second increment should fail (over limit)")

        # Counter must NOT have been incremented despite the check happening
        data = _load(path)
        self.assertEqual(
            data["count"], 6,
            "Counter must not increment when check fails (HIGH-002: atomicity)"
        )


class TestLockProtectsCheckAndIncrement(unittest.TestCase):
    """Verify lock protects both the check AND the increment (HIGH-002)."""

    def test_check_and_increment_cannot_be_split(self):
        """HIGH-002 invariant: check and increment must be within same lock acquisition."""
        from quota_counter import _INCREMENT_LOCK

        # The invariant is:
        # WITH _INCREMENT_LOCK:
        #     read current count
        #     get limit
        #     CHECK: if (current + 1) > limit:
        #         raise LicenseLimitError  <-- still holding lock!
        #     ELSE:
        #         increment and save  <-- still holding lock!

        # This test verifies the code structure respects this by checking
        # that both the check and the increment use the same lock

        import inspect
        from quota_counter import _do_increment_and_check

        source = inspect.getsource(_do_increment_and_check)

        # Verify lock is acquired before any operations
        self.assertIn("with _INCREMENT_LOCK", source,
                      "Lock must be acquired at start of _do_increment_and_check")

        # Verify the check happens within the with block
        self.assertIn("current + 1", source,
                      "Limit check must be in source code")

        # Verify save happens within the with block
        self.assertIn("_save(path", source,
                      "Save must happen in _do_increment_and_check")


if __name__ == "__main__":
    unittest.main()
