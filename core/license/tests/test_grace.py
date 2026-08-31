"""Per-subtask E2E for the grace-period state machine."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from corvin_license import grace


def test_no_state_active_when_valid_until_future(sandbox_home):
    s = grace.assess(valid_until=int(time.time()) + 3600)
    assert s.state == "active"
    assert s.in_grace is False
    assert s.grace_started_at is None


def test_no_state_no_license_when_nothing_present(sandbox_home):
    s = grace.assess(valid_until=None)
    assert s.state == "no-license"
    assert s.in_grace is False


def test_in_grace_immediately_after_expiry(sandbox_home):
    now = 2_000_000_000
    expired_at = now - 3600  # 1 hour ago
    s = grace.assess(valid_until=expired_at, now=now)
    assert s.state == "in-grace"
    assert s.in_grace is True
    assert s.grace_started_at == expired_at
    assert s.grace_ends_at == expired_at + grace.GRACE_PERIOD_SECONDS
    assert s.seconds_remaining > 0


def test_past_grace_period(sandbox_home):
    now = 2_000_000_000
    expired_at = now - (grace.GRACE_PERIOD_SECONDS + 1)
    s = grace.assess(valid_until=expired_at, now=now)
    assert s.state == "expired"
    assert s.in_grace is False
    assert s.seconds_remaining == 0


def test_remember_then_no_license_keeps_grace_active(sandbox_home):
    """Operator deletes license file mid-flight: persisted memory keeps
    the grace timer running so they aren't instantly cut off."""
    now = 2_000_000_000
    valid_until = now + 100  # still valid right now
    grace.remember_valid_license(
        valid_until=valid_until,
        customer_fingerprint="abc123",
    )
    # Walk forward 200s — past expiry, but still well within grace
    later = valid_until + 200
    s = grace.assess(valid_until=None, now=later)
    assert s.state == "in-grace"
    assert s.in_grace is True


def test_remember_persists_max_anchor(sandbox_home):
    """Repeated remember() with smaller exp does not overwrite later one."""
    grace.remember_valid_license(valid_until=3000, customer_fingerprint="a")
    grace.remember_valid_license(valid_until=2000, customer_fingerprint="a")
    state = grace.load_state()
    assert state.last_known_valid_until == 3000


def test_mark_observed_expired_returns_true_only_once(sandbox_home):
    first = grace.mark_observed_expired(customer_fingerprint="xyz")
    second = grace.mark_observed_expired(customer_fingerprint="xyz")
    third = grace.mark_observed_expired(customer_fingerprint="xyz")
    assert first is True
    assert second is False
    assert third is False


def test_reset_state_clears_file(sandbox_home):
    grace.remember_valid_license(valid_until=1000, customer_fingerprint="x")
    state_file = sandbox_home / "tenants" / "_default" / "global" / "license" / "state.json"
    assert state_file.exists()
    grace.reset_state()
    assert not state_file.exists()


def test_state_file_world_readable_rejected(sandbox_home):
    grace.remember_valid_license(valid_until=1000, customer_fingerprint="x")
    state_file = sandbox_home / "tenants" / "_default" / "global" / "license" / "state.json"
    os.chmod(state_file, 0o644)
    with pytest.raises(grace.GraceStateMalformed):
        grace.load_state()
