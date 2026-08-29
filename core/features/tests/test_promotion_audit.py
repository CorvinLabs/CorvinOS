"""Tests for promotion audit trail."""
import json
import tempfile
from pathlib import Path

import pytest

from core.features.promotion_audit import (
    PromotionAuditEvent,
    PromotionAuditTrail,
)


class TestPromotionAuditEvent:
    """PromotionAuditEvent tests."""

    def test_create_promotion_event(self):
        """Test creating a promotion event."""
        event = PromotionAuditEvent(
            event_type="feature.promotion_triggered",
            timestamp="2026-08-29T15:00:00Z",
            feature_id="test_feature",
            old_tier="alpha",
            new_tier="beta",
            reason="Age requirement met",
            actor="daemon",
        )
        assert event.feature_id == "test_feature"
        assert event.old_tier == "alpha"
        assert event.new_tier == "beta"

    def test_event_with_metrics_snapshot(self):
        """Test event with metrics snapshot."""
        metrics = {
            "error_rate_24h": 0.02,
            "adoption_rate": 0.05,
            "invocation_count_24h": 100,
        }
        event = PromotionAuditEvent(
            event_type="feature.promotion_triggered",
            timestamp="2026-08-29T15:00:00Z",
            feature_id="test_feature",
            old_tier="alpha",
            new_tier="beta",
            reason="Ready",
            actor="daemon",
            metrics_snapshot=metrics,
        )
        assert event.metrics_snapshot == metrics

    def test_event_to_dict(self):
        """Test event serialization."""
        event = PromotionAuditEvent(
            event_type="feature.promotion_triggered",
            timestamp="2026-08-29T15:00:00Z",
            feature_id="test",
            old_tier="alpha",
            new_tier="beta",
            reason="Ready",
            actor="daemon",
        )
        d = event.to_dict()
        assert d["feature_id"] == "test"
        assert d["event_type"] == "feature.promotion_triggered"

    def test_event_to_json(self):
        """Test event JSON serialization."""
        event = PromotionAuditEvent(
            event_type="feature.promotion_triggered",
            timestamp="2026-08-29T15:00:00Z",
            feature_id="test",
            old_tier="alpha",
            new_tier="beta",
            reason="Ready",
            actor="daemon",
        )
        json_str = event.to_json()
        parsed = json.loads(json_str)
        assert parsed["feature_id"] == "test"


class TestPromotionAuditTrail:
    """PromotionAuditTrail tests."""

    @pytest.fixture
    def audit_trail(self):
        """Create an audit trail with temp storage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = PromotionAuditTrail(
                storage_path=Path(tmpdir) / "promotion_audit.jsonl",
                enabled=True,
            )
            yield trail
            trail.reset()

    def test_audit_trail_initialization(self, audit_trail):
        """Test audit trail initialization."""
        assert audit_trail.enabled
        assert audit_trail.storage_path.parent.exists()

    def test_record_promotion_triggered(self, audit_trail):
        """Test recording a promotion trigger event."""
        result = audit_trail.record_promotion_triggered(
            feature_id="test_feature",
            old_tier="alpha",
            new_tier="beta",
            reason="Age requirement met",
            triggered_by="age_requirement",
        )
        assert result is True
        assert audit_trail.storage_path.exists()

    def test_record_promotion_approved(self, audit_trail):
        """Test recording a manual promotion."""
        result = audit_trail.record_promotion_approved(
            feature_id="test_feature",
            old_tier="alpha",
            new_tier="beta",
            reason="Maintainer override",
            maintainer_id="user123",
        )
        assert result is True

    def test_record_demotion(self, audit_trail):
        """Test recording a demotion event."""
        result = audit_trail.record_demotion(
            feature_id="test_feature",
            old_tier="beta",
            new_tier="alpha",
            reason="Error rate spike",
        )
        assert result is True

    def test_record_feedback(self, audit_trail):
        """Test recording user feedback."""
        result = audit_trail.record_feedback(
            feature_id="test_feature",
            feedback_score=0.85,
            user_note="Great feature!",
        )
        assert result is True

    def test_hash_chain_creation(self, audit_trail):
        """Test that hash chain is created."""
        audit_trail.record_promotion_triggered(
            feature_id="feat1",
            old_tier="alpha",
            new_tier="beta",
            reason="Ready",
        )

        # Read back and check hash chain
        with open(audit_trail.storage_path, "r") as f:
            log_entry = json.loads(f.readline())

        assert "hash" in log_entry
        assert "previous_hash" in log_entry
        # First entry has None previous_hash
        assert log_entry["previous_hash"] is None

    def test_hash_chain_continuity(self, audit_trail):
        """Test that hash chain is continuous across entries."""
        audit_trail.record_promotion_triggered(
            feature_id="feat1",
            old_tier="alpha",
            new_tier="beta",
            reason="Ready",
        )

        audit_trail.record_promotion_approved(
            feature_id="feat1",
            old_tier="beta",
            new_tier="stable",
            reason="Manual promotion",
        )

        # Read both entries
        with open(audit_trail.storage_path, "r") as f:
            entry1 = json.loads(f.readline())
            entry2 = json.loads(f.readline())

        # Second entry's previous_hash should equal first entry's hash
        assert entry2["previous_hash"] == entry1["hash"]

    def test_verify_chain_valid(self, audit_trail):
        """Test chain verification on valid chain."""
        audit_trail.record_promotion_triggered(
            feature_id="feat1",
            old_tier="alpha",
            new_tier="beta",
            reason="Ready",
        )

        audit_trail.record_demotion(
            feature_id="feat1",
            old_tier="beta",
            new_tier="alpha",
            reason="Error spike",
        )

        valid, message = audit_trail.verify_chain()
        assert valid is True

    def test_verify_chain_empty_log(self, audit_trail):
        """Test chain verification on empty log."""
        valid, message = audit_trail.verify_chain()
        assert valid is True
        assert "No audit log" in message

    def test_get_feature_history(self, audit_trail):
        """Test retrieving feature history."""
        audit_trail.record_promotion_triggered(
            feature_id="feat1",
            old_tier="alpha",
            new_tier="beta",
            reason="Ready",
        )

        audit_trail.record_feedback(
            feature_id="feat1",
            feedback_score=0.9,
        )

        history = audit_trail.get_feature_history("feat1")
        assert len(history) == 2
        assert history[0].event_type == "feature.promotion_triggered"
        assert history[1].event_type == "feature.feedback_recorded"

    def test_get_feature_history_other_feature(self, audit_trail):
        """Test that history only returns events for specific feature."""
        audit_trail.record_promotion_triggered(
            feature_id="feat1",
            old_tier="alpha",
            new_tier="beta",
            reason="Ready",
        )

        audit_trail.record_promotion_triggered(
            feature_id="feat2",
            old_tier="alpha",
            new_tier="beta",
            reason="Ready",
        )

        history1 = audit_trail.get_feature_history("feat1")
        history2 = audit_trail.get_feature_history("feat2")

        assert len(history1) == 1
        assert len(history2) == 1
        assert history1[0].feature_id == "feat1"
        assert history2[0].feature_id == "feat2"

    def test_disabled_audit_trail(self):
        """Test that disabled audit trail doesn't record."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = PromotionAuditTrail(
                storage_path=Path(tmpdir) / "audit.jsonl",
                enabled=False,
            )
            result = trail.record_promotion_triggered(
                feature_id="feat1",
                old_tier="alpha",
                new_tier="beta",
                reason="Ready",
            )
            assert result is False
            assert not trail.storage_path.exists()

    def test_compute_hash_deterministic(self):
        """Test that hash computation is deterministic."""
        event_json = '{"feature_id": "test", "event_type": "test"}'
        prev_hash = None

        hash1 = PromotionAuditTrail._compute_hash(event_json, prev_hash)
        hash2 = PromotionAuditTrail._compute_hash(event_json, prev_hash)

        assert hash1 == hash2

    def test_compute_hash_includes_previous(self):
        """Test that previous hash affects the computed hash."""
        event_json = '{"feature_id": "test", "event_type": "test"}'

        hash1 = PromotionAuditTrail._compute_hash(event_json, None)
        hash2 = PromotionAuditTrail._compute_hash(event_json, "some_previous_hash")

        assert hash1 != hash2  # Different previous hash = different result

    def test_audit_trail_reset(self, audit_trail):
        """Test resetting audit trail."""
        audit_trail.record_promotion_triggered(
            feature_id="feat1",
            old_tier="alpha",
            new_tier="beta",
            reason="Ready",
        )

        assert audit_trail.storage_path.exists()

        audit_trail.reset()
        assert not audit_trail.storage_path.exists()
        assert audit_trail._last_hash is None

    def test_complex_chain_scenario(self, audit_trail):
        """Test a complex promotion chain scenario."""
        # Simulate feature graduation through tiers
        audit_trail.record_promotion_triggered(
            feature_id="my_feature",
            old_tier="alpha",
            new_tier="beta",
            reason="7 days in alpha, error rate < 5%",
            triggered_by="age_requirement",
            metrics_snapshot={"error_rate_24h": 0.03, "invocation_count_24h": 150},
        )

        audit_trail.record_promotion_triggered(
            feature_id="my_feature",
            old_tier="beta",
            new_tier="stable",
            reason="30 days in beta, error rate < 1%",
            triggered_by="age_requirement",
            metrics_snapshot={"error_rate_24h": 0.008, "invocation_count_24h": 500},
        )

        audit_trail.record_demotion(
            feature_id="my_feature",
            old_tier="stable",
            new_tier="beta",
            reason="Error rate spike",
            metrics_snapshot={"error_rate_24h": 0.15},
        )

        # Verify chain
        valid, msg = audit_trail.verify_chain()
        assert valid is True

        # Verify history
        history = audit_trail.get_feature_history("my_feature")
        assert len(history) == 3
        assert history[0].old_tier == "alpha"
        assert history[1].old_tier == "beta"
        assert history[2].event_type == "feature.demoted"
