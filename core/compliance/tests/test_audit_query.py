"""Tests for the audit-chain walker with filters."""
from __future__ import annotations

import time

import pytest

from corvin_compliance_reports import audit_query


def test_iter_events_returns_nothing_when_chain_missing(sandbox_home):
    events = list(audit_query.iter_events(tenant_id="_default"))
    assert events == []


def test_iter_events_round_trip(sandbox_home, seed_chain):
    seed_chain("disclosure.shown", channel="discord", uid="user1")
    seed_chain("disclosure.action", action="joined", uid="user1")
    seed_chain("consent.granted", uid="user1", mode="durable")
    events = list(audit_query.iter_events(tenant_id="_default"))
    assert len(events) == 3
    assert events[0]["event_type"] == "disclosure.shown"
    assert events[2]["event_type"] == "consent.granted"


def test_filter_by_prefix(sandbox_home, seed_chain):
    seed_chain("disclosure.shown", uid="a")
    seed_chain("consent.granted", uid="a")
    seed_chain("disclosure.action", uid="a")
    events = list(audit_query.iter_events(
        tenant_id="_default", event_type_prefix="disclosure.",
    ))
    assert {e["event_type"] for e in events} == {
        "disclosure.shown", "disclosure.action",
    }


def test_filter_by_event_types(sandbox_home, seed_chain):
    seed_chain("disclosure.shown", uid="a")
    seed_chain("consent.granted", uid="a")
    seed_chain("consent.revoked", uid="a")
    events = list(audit_query.iter_events(
        tenant_id="_default",
        event_types=("consent.granted", "consent.revoked"),
    ))
    assert len(events) == 2


def test_filter_by_time_range(sandbox_home, chain_path):
    """Inject events at specific timestamps using the seed_chain mechanism."""
    from forge import security_events as _se
    import json
    # We can't easily backdate via the public emit API; instead write
    # raw chain entries with controlled timestamps. The chain rules
    # require hash linking, so we use the real writer at specific
    # times via time.time() monkey-patching.
    base = 1_700_000_000

    # Custom writer that bypasses the time hop
    for offset, et in [
        (0, "a.event"), (100, "b.event"), (200, "c.event"), (300, "d.event"),
    ]:
        _se.write_event(
            event_type=et, details={"x": offset}, path=chain_path,
            ts=base + offset,
        )

    filtered = list(audit_query.iter_events(
        tenant_id="_default", start_ts=base + 100, end_ts=base + 250,
    ))
    types = [e["event_type"] for e in filtered]
    assert types == ["b.event", "c.event"]


def test_compute_stats_empty_chain(sandbox_home):
    s = audit_query.compute_stats(tenant_id="_default")
    assert s.total_events == 0
    assert s.first_event_hash is None
    assert s.last_event_hash is None
    # Empty file = trivially intact
    assert s.chain_intact is True


def test_compute_stats_aggregates(sandbox_home, seed_chain):
    seed_chain("disclosure.shown", uid="a")
    seed_chain("disclosure.shown", uid="b")
    seed_chain("consent.granted", uid="a", severity="INFO")
    seed_chain("license.violated", reason="signature-invalid", severity="WARNING")
    s = audit_query.compute_stats(tenant_id="_default")
    assert s.total_events == 4
    assert s.by_event_type["disclosure.shown"] == 2
    assert s.by_event_type["consent.granted"] == 1
    assert s.by_event_type["license.violated"] == 1
    assert s.by_severity["INFO"] >= 3
    assert s.by_severity["WARNING"] == 1
    assert s.chain_intact is True
    assert s.first_event_hash != s.last_event_hash


def test_mutually_exclusive_filters_raise(sandbox_home, seed_chain):
    seed_chain("x.event", a=1)
    with pytest.raises(ValueError):
        list(audit_query.iter_events(
            tenant_id="_default",
            event_type_prefix="x.",
            event_types=("y.event",),
        ))
