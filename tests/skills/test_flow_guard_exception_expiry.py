"""Flow Guard exceptions: the expiry the request computes must be one the checker can read back.

``expires_at`` was built as ``<tz-aware isoformat>`` + "Z" ("...+00:00Z"); ``_is_expired`` then turned
the "Z" into "+00:00" ("...+00:00+00:00") and ``fromisoformat`` raised. Every expiry check on a
freshly created exception failed, in code whose purpose is a *time-limited* override of a DENY.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import dataclasses
import pytest

from core.skills.os_skills.flow_guard.exception_system import ExceptionManager, ExceptionRequest, _parse_utc


def _request(**kw) -> ExceptionRequest:
    base = dict(flow_id="f1", data_class="pii", engine="claude-haiku", destination="console",
                policy_decision="deny", reason="needed", created_by="op1", ttl_hours=2, tenant_id="_default")
    base.update(kw)
    return ExceptionRequest(**base)


class _NullStore:
    def write_event(self, event):
        return "ref"


def _manager(tmp_path) -> ExceptionManager:
    return ExceptionManager(event_store=_NullStore(), tenant_id="_default", storage_dir=tmp_path)


def test_computed_expiry_is_readable_and_ttl_hours_after_the_request():
    req = _request(ttl_hours=2)
    assert req.expires_at.endswith("Z") and "+00:00" not in req.expires_at
    delta = _parse_utc(req.expires_at) - _parse_utc(req.timestamp)
    assert delta == timedelta(hours=2)


def test_a_fresh_exception_is_not_expired_and_a_past_one_is(tmp_path):
    mgr = _manager(tmp_path)
    assert mgr._is_expired(_request(ttl_hours=1)) is False
    long_ago = (datetime.now(timezone.utc) - timedelta(hours=5)).replace(tzinfo=None).isoformat() + "Z"
    assert mgr._is_expired(_request(timestamp=long_ago, ttl_hours=1)) is True


def test_an_unreadable_expiry_counts_as_expired_never_as_still_allowing(tmp_path):
    mgr = _manager(tmp_path)
    broken = dataclasses.replace(_request(), expires_at="2026-10-11T22:58:40+00:00+00:00")
    assert mgr._is_expired(broken) is True


def test_ttl_outside_one_to_twenty_four_hours_is_refused():
    for bad in (0, 25, 0.01):
        with pytest.raises(ValueError, match="ttl_hours"):
            _request(ttl_hours=bad)


def test_a_stored_expiry_beyond_the_ttl_or_a_future_creation_counts_as_expired(tmp_path):
    """Review SC-10: a persisted record could carry any expires_at."""
    mgr = _manager(tmp_path)
    assert mgr._is_expired(dataclasses.replace(_request(ttl_hours=1), expires_at="9999-12-31T23:59:59Z")) is True
    future = (datetime.now(timezone.utc) + timedelta(days=30)).replace(tzinfo=None).isoformat() + "Z"
    assert mgr._is_expired(_request(timestamp=future, ttl_hours=1)) is True
