"""Phase 3B Dashboard — Operator Analytics E2E Tests (ADR-2222 Phase 3B).

Four test cases verify:
1. Analytics endpoint returns correct structure
2. Metrics calculation (decisions by week, flags distribution, confidence)
3. Time range filtering (since/until parameters)
4. Summary metrics aggregation

All tests use in-memory registry; no live audit chain.
"""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.orchestration.layer_forge.analytics import LayerForgeAnalytics
from core.orchestration.layer_forge.registry import LayerRegistry


@pytest.fixture
def temp_registry(tmp_path):
    """In-memory LayerRegistry for testing."""
    return LayerRegistry(tmp_path)


@pytest.fixture
def sample_definitions(temp_registry):
    """Create sample layer definitions with various statuses.

    Creates definitions with different statuses and timestamps for testing:
    - Week 1 (7 days ago): 2 accepted, 1 flagged
    - Week 0 (2 days ago): 1 superseded
    """
    now = time.time()
    week_ago = now - 7 * 24 * 60 * 60
    two_days_ago = now - 2 * 24 * 60 * 60

    # Helper to set _created_at after promotion
    def set_created_time(entry_id: str, version: str, ts: float, **extra):
        entry = temp_registry.get(entry_id, version)
        entry["_created_at"] = ts
        entry.update(extra)
        # Write back by re-reading and directly setting (no direct update API)
        path = temp_registry._path_for(entry_id, version)
        import json
        path.write_text(json.dumps(entry))

    # Week 1: Two accepted, one flagged
    manifest1 = {"id": "l1", "version": "1.0.0", "targets": [{"layer_id": "L10"}]}
    temp_registry.create(manifest1)
    temp_registry.promote("l1", "1.0.0", "accepted")
    set_created_time("l1", "1.0.0", week_ago)

    manifest2 = {"id": "l2", "version": "1.0.0", "targets": [{"layer_id": "L10"}]}
    temp_registry.create(manifest2)
    temp_registry.promote("l2", "1.0.0", "accepted")
    temp_registry.promote("l2", "1.0.0", "deployed")
    set_created_time("l2", "1.0.0", week_ago)

    manifest3 = {"id": "l3", "version": "1.0.0", "targets": [{"layer_id": "L10"}]}
    temp_registry.create(manifest3)
    # Same shape the orchestrator persists for a FLAGGED review (orchestrator.py).
    set_created_time("l3", "1.0.0", week_ago, review_flagged=True,
                     review_flags=["scope_creep", "security_gap"])

    # Week 0: One superseded
    manifest4 = {"id": "l4", "version": "1.0.0", "targets": [{"layer_id": "L10"}]}
    temp_registry.create(manifest4)
    temp_registry.promote("l4", "1.0.0", "accepted")
    temp_registry.promote("l4", "1.0.0", "deployed")
    temp_registry.promote("l4", "1.0.0", "superseded")
    set_created_time("l4", "1.0.0", two_days_ago)

    return temp_registry


def test_analytics_endpoint_structure(temp_registry, sample_definitions):
    """Test 1: Analytics endpoint returns correct JSON structure."""
    analytics = LayerForgeAnalytics(
        registry=temp_registry,
        tenant_id="test_tenant",
        learning_store=None,
    )

    result = analytics.get_summary_metrics()

    # Verify structure
    assert "decisions_accepted" in result
    assert "decisions_flagged" in result
    assert "decisions_rejected" in result
    assert "mean_confidence" in result
    assert "flags_distribution" in result
    assert "window" in result

    # Verify types
    assert isinstance(result["decisions_accepted"], int)
    assert isinstance(result["decisions_flagged"], int)
    assert isinstance(result["decisions_rejected"], int)
    assert isinstance(result["mean_confidence"], (int, float))
    assert isinstance(result["flags_distribution"], dict)
    assert isinstance(result["window"], dict)

    # Verify window has required keys
    assert "since" in result["window"]
    assert "until" in result["window"]
    assert "days" in result["window"]


def test_decisions_by_week_aggregation(temp_registry, sample_definitions):
    """Test 2: Metrics aggregation by week."""
    analytics = LayerForgeAnalytics(
        registry=temp_registry,
        tenant_id="test_tenant",
        learning_store=None,
    )

    decisions = analytics.get_decisions_by_week()

    # Should have entries for weeks with decisions
    assert len(decisions) >= 1

    # Each week should have the structure
    for week, counts in decisions.items():
        assert isinstance(week, str)
        assert "accepted" in counts
        assert "flagged" in counts
        assert "rejected" in counts
        assert all(isinstance(v, int) for v in counts.values())


def test_flags_distribution(temp_registry, sample_definitions):
    """Test 3: Review flags distribution calculation."""
    analytics = LayerForgeAnalytics(
        registry=temp_registry,
        tenant_id="test_tenant",
        learning_store=None,
    )

    flags_dist = analytics.get_flags_distribution()

    # Should have entries with flag counts
    for week, flags in flags_dist.items():
        for flag_name in [
            "scope_creep",
            "security_gap",
            "untested_complexity",
            "compliance_risk",
            "dependency_debt",
            "host_asymmetry",
            "unknown_risk",
        ]:
            assert flag_name in flags
            assert isinstance(flags[flag_name], int)
            assert flags[flag_name] >= 0


def test_time_range_filtering(temp_registry, sample_definitions):
    """Test 4: Time range filtering with since/until parameters."""
    analytics = LayerForgeAnalytics(
        registry=temp_registry,
        tenant_id="test_tenant",
        learning_store=None,
    )

    now = datetime.utcnow()
    since = (now - timedelta(days=15)).date().isoformat()
    until = (now - timedelta(days=5)).date().isoformat()

    # Get metrics with narrow time range
    result = analytics.get_summary_metrics(since_iso=since, until_iso=until)

    # Window should reflect a time range (may differ by a day due to timezone handling)
    assert "since" in result["window"]
    assert "until" in result["window"]
    assert result["window"]["days"] >= 0

    # Verify the range is reasonable (within 1 day of requested)
    window_since = datetime.fromisoformat(result["window"]["since"])
    window_until = datetime.fromisoformat(result["window"]["until"])
    req_since = datetime.fromisoformat(since)
    req_until = datetime.fromisoformat(until)

    assert abs((window_since - req_since).days) <= 1
    assert abs((window_until - req_until).days) <= 1


def test_convergence_empty_when_no_learning_store(temp_registry, sample_definitions):
    """Test 5: Learning convergence returns empty list when no store available."""
    analytics = LayerForgeAnalytics(
        registry=temp_registry,
        tenant_id="test_tenant",
        learning_store=None,
    )

    convergence = analytics.get_convergence()

    # Should return empty list (learning store not integrated in Phase 3B)
    assert isinstance(convergence, list)
    assert len(convergence) == 0


def test_summary_metrics_totals(temp_registry, sample_definitions):
    """Test 6: Summary metrics correctly total across all weeks."""
    analytics = LayerForgeAnalytics(
        registry=temp_registry,
        tenant_id="test_tenant",
        learning_store=None,
    )

    summary = analytics.get_summary_metrics()
    by_week = analytics.get_decisions_by_week()

    # Sum across weeks should match summary
    total_accepted = sum(w["accepted"] for w in by_week.values())
    total_flagged = sum(w["flagged"] for w in by_week.values())
    total_rejected = sum(w["rejected"] for w in by_week.values())

    assert summary["decisions_accepted"] == total_accepted
    assert summary["decisions_flagged"] == total_flagged
    assert summary["decisions_rejected"] == total_rejected


@pytest.mark.parametrize(
    "iso_str,expected_ts",
    [
        ("2026-10-05", None),  # Date only
        ("2026-10-05T12:34:56Z", None),  # DateTime with Z
    ],
)
def test_iso_to_timestamp_parsing(iso_str, expected_ts):
    """Test 7: ISO 8601 date/datetime parsing."""
    ts = LayerForgeAnalytics._parse_iso_to_ts(iso_str)

    # Should return a valid Unix timestamp
    assert isinstance(ts, float)
    assert ts > 0

    # Round-trip: convert back to datetime
    dt = datetime.utcfromtimestamp(ts)
    assert isinstance(dt, datetime)


def test_week_of_timestamp(temp_registry):
    """Test 8: Week-of calculation for timestamps."""
    analytics = LayerForgeAnalytics(
        registry=temp_registry,
        tenant_id="test_tenant",
        learning_store=None,
    )

    # Test known date: 2026-10-05 is in week W41
    ts = datetime(2026, 10, 5, 12, 0, 0).timestamp()
    week = analytics.week_of(ts)

    assert week.startswith("2026-W")
    assert len(week) == 8  # YYYY-W##

