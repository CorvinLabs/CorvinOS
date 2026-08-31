"""LIC-1 + LIC-2 regression tests for compute_quota.

LIC-1 — the daily counter is a read-modify-write. On Windows fcntl.flock is a
no-op, so concurrent threadpool POST /compute/runs in ONE process could all read
current=0 and each write 1, blowing past a 1/day cap. A module-level
threading.Lock now serialises the whole read-modify-write.

LIC-2 — a persistent quota-write failure must NOT fail OPEN on a FINITE limit
(read-only license dir / inode exhaustion → unmetered paid compute). It fails
CLOSED (deny) on a finite limit and only fails OPEN on an unlimited tier.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest import mock

import pytest

_OPERATOR_ROOT = str(Path(__file__).resolve().parents[2])
if _OPERATOR_ROOT not in sys.path:
    sys.path.insert(0, _OPERATOR_ROOT)

import license.compute_quota as cq  # noqa: E402
from license.limits import LicenseLimitError  # noqa: E402


# ── LIC-1: concurrent increment respects the cap ──────────────────────────────

def test_concurrent_increment_respects_cap(tmp_path):
    """With a 1/day cap and many concurrent threads, exactly ONE increment may
    succeed; every other must raise LicenseLimitError, and the on-disk counter
    must never exceed the cap."""
    N = 32
    cap = 1

    successes = []
    denials = []
    barrier = threading.Barrier(N)

    def worker():
        # Maximise the race window: all threads line up, then fire together.
        barrier.wait()
        try:
            cq.increment_and_check(tmp_path, channel="test", chat_key="k")
            successes.append(1)
        except LicenseLimitError:
            denials.append(1)

    with mock.patch("license.validator.get_limit", return_value=cap), \
         mock.patch("license.validator.active_tier", return_value="free"):
        threads = [threading.Thread(target=worker) for _ in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert len(successes) == cap, f"expected exactly {cap} success, got {len(successes)}"
    assert len(denials) == N - cap

    # The persisted counter must equal the cap — never more (the race defeat).
    data = cq._load(cq._quota_path(tmp_path))
    assert data.get(cq._today_utc(), 0) == cap


def test_sequential_increment_still_enforces_cap(tmp_path):
    """Baseline: the serialised path still enforces a small cap correctly."""
    with mock.patch("license.validator.get_limit", return_value=3), \
         mock.patch("license.validator.active_tier", return_value="free"):
        cq.increment_and_check(tmp_path, channel="t", chat_key="k")
        cq.increment_and_check(tmp_path, channel="t", chat_key="k")
        cq.increment_and_check(tmp_path, channel="t", chat_key="k")
        with pytest.raises(LicenseLimitError):
            cq.increment_and_check(tmp_path, channel="t", chat_key="k")


# ── LIC-2: persistent I/O error fails CLOSED on a finite limit ────────────────

def test_finite_limit_fails_closed_on_persistent_io_error(tmp_path):
    """A finite (free-tier) limit + a persistent write failure → DENY."""
    with mock.patch.object(cq, "_do_increment_and_check", side_effect=OSError("disk full")), \
         mock.patch("license.validator.get_limit", return_value=5), \
         mock.patch("license.validator.active_tier", return_value="free"):
        with pytest.raises(LicenseLimitError):
            cq.increment_and_check(tmp_path, channel="t", chat_key="k")


def test_unlimited_tier_fails_open_on_persistent_io_error(tmp_path):
    """An unlimited tier (limit is None) + a persistent write failure → ALLOW
    (there is no quota to enforce, so an operational failure must not block)."""
    with mock.patch.object(cq, "_do_increment_and_check", side_effect=OSError("disk full")), \
         mock.patch("license.validator.get_limit", return_value=None), \
         mock.patch("license.validator.active_tier", return_value="member"):
        # Must NOT raise.
        cq.increment_and_check(tmp_path, channel="t", chat_key="k")


def test_transient_io_error_recovers_via_retry(tmp_path):
    """A single transient failure is retried once and then succeeds — no
    nuisance denial for a legitimate free-tier user."""
    calls = {"n": 0}
    real = cq._do_increment_and_check

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("transient")
        return real(*args, **kwargs)

    with mock.patch.object(cq, "_do_increment_and_check", side_effect=flaky), \
         mock.patch("license.validator.get_limit", return_value=5), \
         mock.patch("license.validator.active_tier", return_value="free"):
        # Should recover on the retry and NOT raise.
        cq.increment_and_check(tmp_path, channel="t", chat_key="k")
    assert calls["n"] == 2  # first failed, retry succeeded


def test_cannot_determine_limit_fails_closed(tmp_path):
    """If the limit lookup itself errors, treat as finite → DENY (the default
    free tier is finite; the conservative choice is to deny)."""
    with mock.patch.object(cq, "_do_increment_and_check", side_effect=OSError("disk full")), \
         mock.patch("license.validator.get_limit", side_effect=RuntimeError("boom")), \
         mock.patch("license.validator.active_tier", return_value="free"):
        with pytest.raises(LicenseLimitError):
            cq.increment_and_check(tmp_path, channel="t", chat_key="k")
