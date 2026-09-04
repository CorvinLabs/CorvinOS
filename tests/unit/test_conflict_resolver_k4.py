"""Tests for L5 k=4: Conflict Resolver (ADR-0581)."""

import pytest
from datetime import datetime, timedelta
from core.learning.conflict_resolver import (
    ConflictDetector,
    ConflictResolver,
    Conflict,
    ConflictType,
    ConflictStrategy,
)


class MockAuditBackend:
    """Mock audit backend for testing."""

    def __init__(self):
        self.events = []

    def write_event(self, event):
        self.events.append(event)
        return len(self.events)


def make_approval(skill_id, metric_name, request_time=None, ttl_expires=None):
    """Helper to create an approval record."""
    if request_time is None:
        request_time = datetime.utcnow().isoformat() + "Z"
    if ttl_expires is None:
        ttl_expires = (datetime.utcnow() + timedelta(hours=12)).isoformat() + "Z"

    return {
        "approval_id": f"approval_{skill_id}_{metric_name}",
        "operator_timestamp": request_time,
        "ttl_expires": ttl_expires,
        "skill_id": skill_id,
        "metric_name": metric_name,
    }


@pytest.fixture
def conflict_resolver():
    """Create a ConflictResolver with mock audit backend."""
    audit = MockAuditBackend()
    return ConflictResolver(tenant_id="_default", audit_backend=audit)


class TestConflictDetection:
    """Test conflict detection logic."""

    def test_no_conflict_different_metrics(self):
        """Test that different metrics don't conflict."""
        pending = {
            "skill_a": {
                "metric_x": make_approval("skill_a", "metric_x"),
            },
            "skill_b": {
                "metric_y": make_approval("skill_b", "metric_y"),
            },
        }

        conflicts = ConflictDetector.detect_conflicts(pending)

        assert len(conflicts) == 0

    def test_no_conflict_different_skills_same_metric(self):
        """Test detection when different Skills modify the same parameter."""
        now = datetime.utcnow()

        pending = {
            "skill_a": {
                "confidence_threshold": make_approval(
                    "skill_a",
                    "confidence_threshold",
                    request_time=now.isoformat() + "Z",
                    ttl_expires=(now + timedelta(hours=1)).isoformat() + "Z",
                ),
            },
            "skill_b": {
                "confidence_threshold": make_approval(
                    "skill_b",
                    "confidence_threshold",
                    request_time=(now + timedelta(minutes=5)).isoformat() + "Z",
                    ttl_expires=(now + timedelta(hours=2)).isoformat() + "Z",
                ),
            },
        }

        conflicts = ConflictDetector.detect_conflicts(pending)

        # Should detect conflict (overlapping time windows)
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == ConflictType.CONCURRENT_PARAMETER

    def test_conflict_non_overlapping_times(self):
        """Test that non-overlapping time windows don't conflict."""
        now = datetime.utcnow()

        pending = {
            "skill_a": {
                "param_x": make_approval(
                    "skill_a",
                    "param_x",
                    request_time=now.isoformat() + "Z",
                    ttl_expires=(now + timedelta(hours=1)).isoformat() + "Z",
                ),
            },
            "skill_b": {
                "param_x": make_approval(
                    "skill_b",
                    "param_x",
                    request_time=(now + timedelta(hours=2)).isoformat() + "Z",
                    ttl_expires=(now + timedelta(hours=3)).isoformat() + "Z",
                ),
            },
        }

        conflicts = ConflictDetector.detect_conflicts(pending)

        # No overlap
        assert len(conflicts) == 0

    def test_same_skill_no_conflict(self):
        """Test that same-Skill concurrency is NOT detected as cross-Skill conflict."""
        now = datetime.utcnow()

        pending = {
            "skill_a": {
                "metric_x": make_approval(
                    "skill_a",
                    "metric_x",
                    request_time=now.isoformat() + "Z",
                ),
                "metric_y": make_approval(
                    "skill_a",
                    "metric_y",
                    request_time=(now + timedelta(minutes=5)).isoformat() + "Z",
                ),
            },
        }

        conflicts = ConflictDetector.detect_conflicts(pending)

        # No conflict (different metrics, same Skill)
        assert len(conflicts) == 0


class TestConflictResolution:
    """Test conflict resolution strategies."""

    def test_serialize_by_default(self, conflict_resolver):
        """Test that SERIALIZE is the default strategy."""
        now = datetime.utcnow()

        pending = {
            "skill_a": {
                "param": make_approval(
                    "skill_a",
                    "param",
                    request_time=now.isoformat() + "Z",
                ),
            },
            "skill_b": {
                "param": make_approval(
                    "skill_b",
                    "param",
                    request_time=(now + timedelta(minutes=5)).isoformat() + "Z",
                ),
            },
        }

        resolutions = conflict_resolver.detect_and_resolve(pending)

        assert len(resolutions) == 1
        assert resolutions[0].strategy == ConflictStrategy.SERIALIZE

    def test_merge_on_opt_in(self, conflict_resolver):
        """Test that MERGE is used when both Skills opt-in."""
        now = datetime.utcnow()

        pending = {
            "skill_a": {
                "param": make_approval("skill_a", "param", request_time=now.isoformat() + "Z"),
            },
            "skill_b": {
                "param": make_approval(
                    "skill_b", "param", request_time=(now + timedelta(minutes=5)).isoformat() + "Z"
                ),
            },
        }

        # Both opt-in to merge
        conflict_resolver.set_merge_opt_in("skill_a", "skill_b", "param", True)

        resolutions = conflict_resolver.detect_and_resolve(pending)

        assert len(resolutions) == 1
        assert resolutions[0].strategy == ConflictStrategy.MERGE

    def test_disable_merge_opt_in(self, conflict_resolver):
        """Test disabling merge opt-in reverts to default."""
        conflict_resolver.set_merge_opt_in("skill_a", "skill_b", "param", True)
        conflict_resolver.set_merge_opt_in("skill_a", "skill_b", "param", False)

        now = datetime.utcnow()
        pending = {
            "skill_a": {
                "param": make_approval("skill_a", "param", request_time=now.isoformat() + "Z"),
            },
            "skill_b": {
                "param": make_approval(
                    "skill_b", "param", request_time=(now + timedelta(minutes=5)).isoformat() + "Z"
                ),
            },
        }

        resolutions = conflict_resolver.detect_and_resolve(pending)

        assert resolutions[0].strategy == ConflictStrategy.SERIALIZE


class TestAuditIntegration:
    """Test audit trail integration."""

    def test_conflict_audited(self, conflict_resolver):
        """Test that detected conflicts are audited."""
        now = datetime.utcnow()

        pending = {
            "skill_a": {
                "param": make_approval("skill_a", "param", request_time=now.isoformat() + "Z"),
            },
            "skill_b": {
                "param": make_approval(
                    "skill_b", "param", request_time=(now + timedelta(minutes=5)).isoformat() + "Z"
                ),
            },
        }

        conflict_resolver.detect_and_resolve(pending)

        # Check audit event
        audit_events = conflict_resolver.audit_backend.events
        assert len(audit_events) > 0
        assert audit_events[0]["event_type"] == "learning_conflict_detected"

    def test_audit_failure_continues_processing(self, conflict_resolver):
        """Test that audit failures don't block resolution."""

        class FailingAudit:
            def write_event(self, event):
                raise RuntimeError("Audit failed")

        conflict_resolver.audit_backend = FailingAudit()

        now = datetime.utcnow()
        pending = {
            "skill_a": {
                "param": make_approval("skill_a", "param", request_time=now.isoformat() + "Z"),
            },
            "skill_b": {
                "param": make_approval(
                    "skill_b", "param", request_time=(now + timedelta(minutes=5)).isoformat() + "Z"
                ),
            },
        }

        # Should not raise; resolution should continue despite audit failure
        resolutions = conflict_resolver.detect_and_resolve(pending)

        assert len(resolutions) == 1


class TestConflictStorage:
    """Test conflict storage and retrieval."""

    def test_get_conflicts(self, conflict_resolver):
        """Test retrieving all stored conflicts."""
        now = datetime.utcnow()

        pending = {
            "skill_a": {
                "param": make_approval("skill_a", "param", request_time=now.isoformat() + "Z"),
            },
            "skill_b": {
                "param": make_approval(
                    "skill_b", "param", request_time=(now + timedelta(minutes=5)).isoformat() + "Z"
                ),
            },
        }

        conflict_resolver.detect_and_resolve(pending)
        conflicts = conflict_resolver.get_conflicts()

        assert len(conflicts) == 1

    def test_get_conflict_by_id(self, conflict_resolver):
        """Test retrieving a specific conflict."""
        now = datetime.utcnow()

        pending = {
            "skill_a": {
                "param": make_approval("skill_a", "param", request_time=now.isoformat() + "Z"),
            },
            "skill_b": {
                "param": make_approval(
                    "skill_b", "param", request_time=(now + timedelta(minutes=5)).isoformat() + "Z"
                ),
            },
        }

        conflict_resolver.detect_and_resolve(pending)
        conflicts = conflict_resolver.get_conflicts()

        if conflicts:
            conflict_id = conflicts[0].conflict_id
            retrieved = conflict_resolver.get_conflict(conflict_id)
            assert retrieved is not None


class TestEdgeCases:
    """Test edge cases."""

    def test_three_skills_same_metric(self, conflict_resolver):
        """Test conflict detection with 3+ Skills on same metric."""
        now = datetime.utcnow()

        pending = {
            "skill_a": {
                "param": make_approval("skill_a", "param", request_time=now.isoformat() + "Z"),
            },
            "skill_b": {
                "param": make_approval(
                    "skill_b", "param", request_time=(now + timedelta(minutes=5)).isoformat() + "Z"
                ),
            },
            "skill_c": {
                "param": make_approval(
                    "skill_c", "param", request_time=(now + timedelta(minutes=3)).isoformat() + "Z"
                ),
            },
        }

        conflicts = ConflictDetector.detect_conflicts(pending)

        # Should detect 3 pairwise conflicts: (A,B), (A,C), (B,C)
        assert len(conflicts) == 3

    def test_empty_pending_approvals(self, conflict_resolver):
        """Test with no pending approvals."""
        conflicts = ConflictDetector.detect_conflicts({})

        assert len(conflicts) == 0

    def test_single_pending_approval(self, conflict_resolver):
        """Test with single pending approval (no conflicts possible)."""
        now = datetime.utcnow()
        pending = {
            "skill_a": {
                "param": make_approval("skill_a", "param", request_time=now.isoformat() + "Z"),
            },
        }

        conflicts = ConflictDetector.detect_conflicts(pending)

        assert len(conflicts) == 0
