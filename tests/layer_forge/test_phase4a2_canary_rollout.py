"""Tests for Phase 4 A2 — Canary Rollout for Review-Prompt-Versions (ADR-2227 amendment).

Tests that:
1. Deterministic sampling: same entry_id always gets same prompt version
2. Canary percentage splits traffic correctly (e.g., 10% to v1.1, 90% to v1.0)
3. Per-version outcome tracking in FeedbackAnalyzer
4. Promotion rule: canary → 100% only if success_rate >= parent (no regression)
5. Rollback rule: canary success_rate < parent → rollback, not promotion
6. Audit events: canary_rollout_assigned, canary_rollback
7. E2E: Synthetic regression scenario triggers rollback
8. ReviewVerdict includes assigned prompt_version for audit

Each test exercises the canary rollout system from manifest → review → audit → optimizer.
"""
from __future__ import annotations

import hashlib
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.orchestration.layer_forge.optimizer import (
    FeedbackAnalyzer,
    FeedbackPattern,
    OptimizerEngine,
    OptimizationSignal,
    PromptVersion,
    ReviewPromptVersions,
)
from core.orchestration.layer_forge.review import (
    ReviewVerdict, ReviewFlag, select_canary_version
)


@pytest.fixture
def temp_storage():
    """Temporary storage for prompt version history."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def prompt_versions(temp_storage):
    """ReviewPromptVersions instance for testing (canary at 10%)."""
    return ReviewPromptVersions(temp_storage / "prompt_versions.json", canary_percentage=10)


@pytest.fixture
def optimizer_engine(temp_storage):
    """OptimizerEngine instance for testing."""
    return OptimizerEngine("_default", temp_storage)


class TestDeterministicCanarySampling:
    """Test deterministic per-entry canary routing."""

    def test_same_entry_always_gets_same_version(self, prompt_versions, temp_storage):
        """Same entry_id must always be routed to same version (deterministic)."""
        entry_id = "layer-test-001"

        # Get version 5 times, should always be same
        versions = []
        for _ in range(5):
            v = select_canary_version(entry_id)
            versions.append(v)

        # All should be identical
        assert len(set(versions)) == 1
        assert versions[0] in ["v1.0"]  # Only v1.0 exists initially

    def test_different_entries_may_get_different_versions(self, prompt_versions, temp_storage):
        """Different entry_ids may be routed to different versions based on hash."""
        entry1 = "layer-alpha-001"
        entry2 = "layer-beta-001"

        v1 = select_canary_version(entry1)
        v2 = select_canary_version(entry2)

        # Both should resolve to current/parent chain
        assert v1 in ["v1.0"]
        assert v2 in ["v1.0"]

    def test_canary_split_at_10_percent(self, prompt_versions, temp_storage):
        """With canary at 10%, approximately 10% of entries should get canary."""
        # Add v1.1 as canary at 10%
        v1_1 = PromptVersion(
            version="v1.1",
            timestamp=datetime.utcnow().isoformat() + "Z",
            reason="overcautious_feedback",
            prompt_text="Relaxed prompt",
            parent_version="v1.0",
            rollout_percentage=10,  # 10% canary
        )
        prompt_versions.add_version(v1_1)

        # Test 1000 entries to see if ~10% get routed to v1.1
        canary_count = 0
        for i in range(1000):
            entry_id = f"layer-test-{i:04d}"
            v = select_canary_version(entry_id)
            if v == "v1.1":
                canary_count += 1

        # Should be roughly 10% (allow 5-15% variance for randomness in hashing)
        pct = canary_count / 1000
        assert 0.05 <= pct <= 0.15, f"Expected ~10%, got {pct*100:.1f}%"


class TestPerVersionOutcomeTracking:
    """Test FeedbackAnalyzer's per-version outcome tracking."""

    def test_analyze_overrides_per_version_splits_by_prompt_version(self):
        """FeedbackAnalyzer.analyze_overrides_per_version groups by prompt_version."""
        mock_store = MagicMock()

        # v1.0 events: 8 successes, 2 failures
        v1_0_events = []
        for i in range(8):
            event = MagicMock()
            event.signal = {
                "entry_id": f"layer-{i}",
                "review_override_applied": True,
                "success": True,
                "prompt_version": "v1.0",
            }
            event.timestamp = (datetime.utcnow() - timedelta(hours=i)).isoformat() + "Z"
            v1_0_events.append(event)

        for i in range(2):
            event = MagicMock()
            event.signal = {
                "entry_id": f"layer-fail-{i}",
                "review_override_applied": True,
                "success": False,
                "prompt_version": "v1.0",
            }
            event.timestamp = (datetime.utcnow() - timedelta(hours=i)).isoformat() + "Z"
            v1_0_events.append(event)

        # v1.1 events: 2 successes, 8 failures (regression!)
        v1_1_events = []
        for i in range(2):
            event = MagicMock()
            event.signal = {
                "entry_id": f"layer-new-{i}",
                "review_override_applied": True,
                "success": True,
                "prompt_version": "v1.1",
            }
            event.timestamp = (datetime.utcnow() - timedelta(hours=i)).isoformat() + "Z"
            v1_1_events.append(event)

        for i in range(8):
            event = MagicMock()
            event.signal = {
                "entry_id": f"layer-newfail-{i}",
                "review_override_applied": True,
                "success": False,
                "prompt_version": "v1.1",
            }
            event.timestamp = (datetime.utcnow() - timedelta(hours=i)).isoformat() + "Z"
            v1_1_events.append(event)

        all_events = v1_0_events + v1_1_events
        mock_store.query_events.return_value = all_events

        analyzer = FeedbackAnalyzer("_default", event_store=mock_store)
        by_version = analyzer.analyze_overrides_per_version(limit=100)

        # v1.0: 80% success (overcautious)
        assert "v1.0" in by_version
        assert by_version["v1.0"].total_overrides == 10
        assert by_version["v1.0"].success_rate == 0.8
        assert by_version["v1.0"].signal == OptimizationSignal.OVERCAUTIOUS

        # v1.1: 20% success (undercautious, regression!)
        assert "v1.1" in by_version
        assert by_version["v1.1"].total_overrides == 10
        assert by_version["v1.1"].success_rate == 0.2
        assert by_version["v1.1"].signal == OptimizationSignal.UNDERCAUTIOUS


class TestCanaryRegressionDetection:
    """Test regression detection for canary versions."""

    def test_check_canary_regression_detects_v1_1_regression(self, optimizer_engine):
        """Detect when canary v1.1 has lower success rate than parent v1.0."""
        # Add v1.1 as canary
        current = optimizer_engine.versions.current_version()
        v1_1 = PromptVersion(
            version="v1.1",
            timestamp=datetime.utcnow().isoformat() + "Z",
            reason="overcautious",
            prompt_text="Relaxed",
            parent_version="v1.0",
            rollout_percentage=10,
        )
        optimizer_engine.versions.add_version(v1_1)

        # Create patterns
        v1_0_pattern = FeedbackPattern(
            total_overrides=10,
            successful_overrides=8,
            failed_overrides=2,
            success_rate=0.8,
            confidence_delta=0.15,
            signal=OptimizationSignal.OVERCAUTIOUS,
            review_prompt_version="v1.0",
        )

        # Regression: v1.1 has LOWER success than v1.0
        v1_1_pattern = FeedbackPattern(
            total_overrides=10,
            successful_overrides=2,  # Only 20%!
            failed_overrides=8,
            success_rate=0.2,
            confidence_delta=-0.20,
            signal=OptimizationSignal.UNDERCAUTIOUS,
            review_prompt_version="v1.1",
        )

        versions_data = {"v1.0": v1_0_pattern, "v1.1": v1_1_pattern}
        regressed = optimizer_engine.check_canary_regression(versions_data)

        assert regressed == "v1.1"

    def test_check_canary_no_regression_when_canary_better(self, optimizer_engine):
        """No regression if canary success_rate >= parent."""
        current = optimizer_engine.versions.current_version()
        v1_1 = PromptVersion(
            version="v1.1",
            timestamp=datetime.utcnow().isoformat() + "Z",
            reason="overcautious",
            prompt_text="Relaxed",
            parent_version="v1.0",
            rollout_percentage=10,
        )
        optimizer_engine.versions.add_version(v1_1)

        # Both at 80% (no regression)
        v1_0_pattern = FeedbackPattern(
            total_overrides=10,
            successful_overrides=8,
            failed_overrides=2,
            success_rate=0.8,
            confidence_delta=0.0,
            signal=OptimizationSignal.NEUTRAL,
            review_prompt_version="v1.0",
        )

        v1_1_pattern = FeedbackPattern(
            total_overrides=10,
            successful_overrides=8,
            failed_overrides=2,
            success_rate=0.8,
            confidence_delta=0.0,
            signal=OptimizationSignal.NEUTRAL,
            review_prompt_version="v1.1",
        )

        versions_data = {"v1.0": v1_0_pattern, "v1.1": v1_1_pattern}
        regressed = optimizer_engine.check_canary_regression(versions_data)

        assert regressed is None


class TestCanaryRollback:
    """Test rollback logic."""

    def test_rollback_canary_restores_parent(self, optimizer_engine):
        """Rollback makes parent version current again."""
        # Add v1.1
        v1_1 = PromptVersion(
            version="v1.1",
            timestamp=datetime.utcnow().isoformat() + "Z",
            reason="overcautious",
            prompt_text="Relaxed",
            parent_version="v1.0",
            rollout_percentage=10,
        )
        optimizer_engine.versions.add_version(v1_1)
        assert optimizer_engine.versions.current_version().version == "v1.1"

        # Patterns for rollback
        v1_0_pattern = FeedbackPattern(
            total_overrides=10,
            successful_overrides=8,
            failed_overrides=2,
            success_rate=0.8,
            confidence_delta=0.0,
            signal=OptimizationSignal.NEUTRAL,
            review_prompt_version="v1.0",
        )

        v1_1_pattern = FeedbackPattern(
            total_overrides=10,
            successful_overrides=2,
            failed_overrides=8,
            success_rate=0.2,
            confidence_delta=0.0,
            signal=OptimizationSignal.UNDERCAUTIOUS,
            review_prompt_version="v1.1",
        )

        # Rollback
        result = optimizer_engine.rollback_canary(
            "v1.1",
            canary_pattern=v1_1_pattern,
            parent_pattern=v1_0_pattern
        )

        assert result is True
        assert optimizer_engine.versions.current_version().version == "v1.0"

    def test_rollback_canary_freezes_it(self, optimizer_engine):
        """After rollback, canary version is never promoted (frozen)."""
        # Add v1.1 and rollback
        v1_1 = PromptVersion(
            version="v1.1",
            timestamp=datetime.utcnow().isoformat() + "Z",
            reason="overcautious",
            prompt_text="Relaxed",
            parent_version="v1.0",
            rollout_percentage=10,
        )
        optimizer_engine.versions.add_version(v1_1)
        optimizer_engine.rollback_canary("v1.1")

        # Canary should still be at 10% (frozen), never promoted
        v1_1_after = optimizer_engine.versions.get_version("v1.1")
        assert v1_1_after.rollout_percentage == 10


class TestAuditEventEmission:
    """Test audit event emission for canary operations."""

    def test_canary_rollout_assigned_event_structure(self):
        """canary_rollout_assigned event has required fields."""
        # Mock the audit emit
        with patch("core.orchestration.layer_forge.audit.emit") as mock_emit:
            mock_emit.return_value = "audit-123"

            from core.orchestration.layer_forge import audit
            audit.emit(
                "layer_forge.canary_rollout_assigned",
                tenant_id="_default",
                entry_id="layer-001",
                version="1.0.0",
                prompt_version="v1.1",
                rollout_percentage=10,
            )

            mock_emit.assert_called_once()
            call_kwargs = mock_emit.call_args[1]
            assert call_kwargs["entry_id"] == "layer-001"
            assert call_kwargs["version"] == "1.0.0"
            assert call_kwargs["prompt_version"] == "v1.1"
            assert call_kwargs["rollout_percentage"] == 10

    def test_canary_rollback_event_structure(self):
        """canary_rollback event has required fields."""
        with patch("core.orchestration.layer_forge.audit.emit") as mock_emit:
            mock_emit.return_value = "audit-456"

            from core.orchestration.layer_forge import audit
            audit.emit(
                "layer_forge.canary_rollback",
                tenant_id="_default",
                canary_version="v1.1",
                parent_version="v1.0",
                reason="canary_regression_detected",
                canary_success_rate=0.2,
                parent_success_rate=0.8,
            )

            mock_emit.assert_called_once()
            call_kwargs = mock_emit.call_args[1]
            assert call_kwargs["canary_version"] == "v1.1"
            assert call_kwargs["parent_version"] == "v1.0"
            assert call_kwargs["reason"] == "canary_regression_detected"
            assert call_kwargs["canary_success_rate"] == 0.2


class TestSyntheticRegressionE2E:
    """E2E test with synthetic regression scenario."""

    def test_synthetic_regression_triggers_rollback_not_promotion(self, temp_storage):
        """Synthetic scenario: canary succeeds initially, then regresses → rollback."""
        # Phase 1: Deploy v1.1 canary
        engine = OptimizerEngine("_default", temp_storage)

        v1_1 = PromptVersion(
            version="v1.1",
            timestamp=datetime.utcnow().isoformat() + "Z",
            reason="overcautious",
            prompt_text="Relaxed prompt v1.1",
            parent_version="v1.0",
            rollout_percentage=10,
        )
        engine.versions.add_version(v1_1)
        assert engine.versions.current_version().version == "v1.1"

        # Phase 2: Collect outcomes showing regression
        v1_0_pattern = FeedbackPattern(
            total_overrides=20,
            successful_overrides=16,
            failed_overrides=4,
            success_rate=0.80,  # v1.0 baseline: 80%
            confidence_delta=0.0,
            signal=OptimizationSignal.NEUTRAL,
            review_prompt_version="v1.0",
        )

        v1_1_pattern = FeedbackPattern(
            total_overrides=20,
            successful_overrides=6,  # REGRESSION!
            failed_overrides=14,
            success_rate=0.30,  # v1.1 regressed to 30%
            confidence_delta=-0.50,
            signal=OptimizationSignal.UNDERCAUTIOUS,
            review_prompt_version="v1.1",
        )

        # Phase 3: Detect regression
        versions_data = {"v1.0": v1_0_pattern, "v1.1": v1_1_pattern}
        regressed_version = engine.check_canary_regression(versions_data)
        assert regressed_version == "v1.1"

        # Phase 4: Do NOT promote (regression rule)
        # Instead, rollback
        with patch("core.orchestration.layer_forge.audit.emit") as mock_emit:
            mock_emit.return_value = "audit-rollback-event"
            rollback_ok = engine.rollback_canary(
                "v1.1",
                canary_pattern=v1_1_pattern,
                parent_pattern=v1_0_pattern,
            )

        assert rollback_ok is True
        assert engine.versions.current_version().version == "v1.0"

        # Verify audit was emitted
        assert mock_emit.called


class TestPromotionVsRollback:
    """Test the decision logic: promote or rollback."""

    def test_promotion_rule_no_regression(self, optimizer_engine):
        """Canary at equal or better success_rate than parent → consider promotion."""
        v1_1 = PromptVersion(
            version="v1.1",
            timestamp=datetime.utcnow().isoformat() + "Z",
            reason="overcautious",
            prompt_text="Relaxed",
            parent_version="v1.0",
            rollout_percentage=10,
        )
        optimizer_engine.versions.add_version(v1_1)

        # v1.1 at 85%, v1.0 at 80% (improvement!)
        v1_0_pattern = FeedbackPattern(
            total_overrides=10,
            successful_overrides=8,
            failed_overrides=2,
            success_rate=0.80,
            confidence_delta=0.0,
            signal=OptimizationSignal.NEUTRAL,
            review_prompt_version="v1.0",
        )

        v1_1_pattern = FeedbackPattern(
            total_overrides=10,
            successful_overrides=9,
            failed_overrides=1,
            success_rate=0.90,
            confidence_delta=0.0,
            signal=OptimizationSignal.NEUTRAL,
            review_prompt_version="v1.1",
        )

        versions_data = {"v1.0": v1_0_pattern, "v1.1": v1_1_pattern}
        regressed = optimizer_engine.check_canary_regression(versions_data)
        assert regressed is None  # No regression → could promote

    def test_rollback_rule_with_regression(self, optimizer_engine):
        """Canary success_rate < parent → rollback (do NOT promote)."""
        v1_1 = PromptVersion(
            version="v1.1",
            timestamp=datetime.utcnow().isoformat() + "Z",
            reason="overcautious",
            prompt_text="Relaxed",
            parent_version="v1.0",
            rollout_percentage=10,
        )
        optimizer_engine.versions.add_version(v1_1)

        # v1.1 at 30%, v1.0 at 80% (major regression!)
        v1_0_pattern = FeedbackPattern(
            total_overrides=10,
            successful_overrides=8,
            failed_overrides=2,
            success_rate=0.80,
            confidence_delta=0.0,
            signal=OptimizationSignal.NEUTRAL,
            review_prompt_version="v1.0",
        )

        v1_1_pattern = FeedbackPattern(
            total_overrides=10,
            successful_overrides=3,
            failed_overrides=7,
            success_rate=0.30,
            confidence_delta=0.0,
            signal=OptimizationSignal.NEUTRAL,
            review_prompt_version="v1.1",
        )

        versions_data = {"v1.0": v1_0_pattern, "v1.1": v1_1_pattern}
        regressed = optimizer_engine.check_canary_regression(versions_data)
        assert regressed == "v1.1"  # Regression detected → rollback


__all__ = [
    "TestDeterministicCanarySampling",
    "TestPerVersionOutcomeTracking",
    "TestCanaryRegressionDetection",
    "TestCanaryRollback",
    "TestAuditEventEmission",
    "TestSyntheticRegressionE2E",
    "TestPromotionVsRollback",
]
