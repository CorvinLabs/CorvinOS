"""Tests for Phase 3 C — Learning Optimizer Loop (ADR-2227 amendment, PLAN-0934).

Tests that:
1. FeedbackAnalyzer detects override+success patterns
2. OptimizerEngine suggests prompt updates based on patterns
3. ReviewPromptVersions manages immutable version history
4. Config versioning is correctly tracked
5. Optimizer events are audited
6. Pattern significance calculation works
7. Convergence toward better prompts is measurable
8. E2E loop: feedback → pattern → update → audit

Each test exercises the learning loop from outcome signals to suggested/applied
optimizer updates.
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.orchestration.layer_forge.optimizer import (
    FeedbackAnalyzer,
    FeedbackPattern,
    OptimizerEngine,
    OptimizerUpdate,
    OptimizationSignal,
    PromptVersion,
    ReviewPromptVersions,
)
from core.orchestration.layer_forge.review import ReviewVerdict, ReviewFlag


@pytest.fixture
def temp_storage():
    """Temporary storage for prompt version history."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def prompt_versions(temp_storage):
    """ReviewPromptVersions instance for testing."""
    return ReviewPromptVersions(temp_storage / "prompt_versions.json")


@pytest.fixture
def optimizer_engine(temp_storage):
    """OptimizerEngine instance for testing."""
    return OptimizerEngine("_default", temp_storage)


class TestReviewPromptVersions:
    """Test immutable prompt version management."""

    def test_initialize_with_v1_0(self, prompt_versions):
        """First call creates v1.0 if none exists."""
        current = prompt_versions.current_version()
        assert current.version == "v1.0"
        assert current.reason == "initial"
        assert current.parent_version is None

    def test_add_new_version(self, prompt_versions):
        """Add a new version and mark it current."""
        v1_0 = prompt_versions.current_version()

        v1_1 = PromptVersion(
            version="v1.1",
            timestamp=datetime.utcnow().isoformat() + "Z",
            reason="overcautious_feedback",
            prompt_text="Updated prompt text",
            parent_version="v1.0",
        )
        prompt_versions.add_version(v1_1)

        current = prompt_versions.current_version()
        assert current.version == "v1.1"
        assert current.prompt_text == "Updated prompt text"
        assert current.parent_version == "v1.0"

    def test_version_immutability(self, prompt_versions):
        """Adding the same version twice raises ValueError."""
        v1_0 = prompt_versions.current_version()

        with pytest.raises(ValueError, match="already exists"):
            prompt_versions.add_version(v1_0)

    def test_get_specific_version(self, prompt_versions):
        """Retrieve a specific version by key."""
        v1_0 = prompt_versions.current_version()
        retrieved = prompt_versions.get_version("v1.0")

        assert retrieved is not None
        assert retrieved.version == v1_0.version
        assert retrieved.prompt_text == v1_0.prompt_text

    def test_get_nonexistent_version(self, prompt_versions):
        """Getting a nonexistent version returns None."""
        v99 = prompt_versions.get_version("v99.0")
        assert v99 is None

    def test_version_persistence(self, temp_storage):
        """Version history persists across instances."""
        # Create and add a version
        v1 = ReviewPromptVersions(temp_storage / "prompt_versions.json")
        current1 = v1.current_version()

        v1_1 = PromptVersion(
            version="v1.1",
            timestamp=datetime.utcnow().isoformat() + "Z",
            reason="test",
            prompt_text="New text",
            parent_version="v1.0",
        )
        v1.add_version(v1_1)

        # Load in a new instance
        v2 = ReviewPromptVersions(temp_storage / "prompt_versions.json")
        current2 = v2.current_version()

        assert current2.version == "v1.1"
        assert current2.prompt_text == "New text"


class TestFeedbackAnalyzer:
    """Test feedback pattern detection."""

    def test_analyzer_initialization(self):
        """FeedbackAnalyzer initializes with tenant."""
        analyzer = FeedbackAnalyzer("test_tenant")
        assert analyzer.tenant_id == "test_tenant"

    def test_no_outcomes_available(self):
        """Analyzer returns neutral pattern when no outcomes exist."""
        analyzer = FeedbackAnalyzer("test_tenant", event_store=None)
        pattern = analyzer.analyze_recent_overrides()

        assert pattern.total_overrides == 0
        assert pattern.success_rate == 0.0
        assert pattern.signal == OptimizationSignal.NEUTRAL

    def test_mock_outcomes_with_high_success_rate(self):
        """Analyzer detects overcautious pattern (high override success rate)."""
        # Create mock event store
        mock_store = MagicMock()
        mock_events = []

        # 8 successful overrides
        for i in range(8):
            event = MagicMock()
            event.signal = {
                "entry_id": f"layer-{i}",
                "review_override_applied": True,
                "success": True,
            }
            event.timestamp = (datetime.utcnow() - timedelta(hours=i)).isoformat() + "Z"
            mock_events.append(event)

        # 2 failed overrides
        for i in range(2):
            event = MagicMock()
            event.signal = {
                "entry_id": f"layer-fail-{i}",
                "review_override_applied": True,
                "success": False,
            }
            event.timestamp = (datetime.utcnow() - timedelta(hours=i)).isoformat() + "Z"
            mock_events.append(event)

        mock_store.query_events.return_value = mock_events

        analyzer = FeedbackAnalyzer("test_tenant", event_store=mock_store)
        pattern = analyzer.analyze_recent_overrides(limit=50)

        assert pattern.total_overrides == 10
        assert pattern.successful_overrides == 8
        assert pattern.failed_overrides == 2
        assert pattern.success_rate == 0.8
        assert pattern.signal == OptimizationSignal.OVERCAUTIOUS
        assert pattern.confidence_delta > 0

    def test_mock_outcomes_with_low_success_rate(self):
        """Analyzer detects undercautious pattern (low override success rate)."""
        mock_store = MagicMock()
        mock_events = []

        # 2 successful overrides
        for i in range(2):
            event = MagicMock()
            event.signal = {
                "entry_id": f"layer-{i}",
                "review_override_applied": True,
                "success": True,
            }
            event.timestamp = (datetime.utcnow() - timedelta(hours=i)).isoformat() + "Z"
            mock_events.append(event)

        # 8 failed overrides
        for i in range(8):
            event = MagicMock()
            event.signal = {
                "entry_id": f"layer-fail-{i}",
                "review_override_applied": True,
                "success": False,
            }
            event.timestamp = (datetime.utcnow() - timedelta(hours=i)).isoformat() + "Z"
            mock_events.append(event)

        mock_store.query_events.return_value = mock_events

        analyzer = FeedbackAnalyzer("test_tenant", event_store=mock_store)
        pattern = analyzer.analyze_recent_overrides(limit=50)

        assert pattern.total_overrides == 10
        assert pattern.successful_overrides == 2
        assert pattern.failed_overrides == 8
        assert pattern.success_rate == 0.2
        assert pattern.signal == OptimizationSignal.UNDERCAUTIOUS
        assert pattern.confidence_delta < 0

    def test_balanced_pattern_is_neutral(self):
        """Balanced success/failure results in neutral signal."""
        mock_store = MagicMock()
        mock_events = []

        # 5 successful, 5 failed = 50% success
        for i in range(5):
            event = MagicMock()
            event.signal = {
                "entry_id": f"layer-{i}",
                "review_override_applied": True,
                "success": True,
            }
            event.timestamp = (datetime.utcnow() - timedelta(hours=i)).isoformat() + "Z"
            mock_events.append(event)

        for i in range(5):
            event = MagicMock()
            event.signal = {
                "entry_id": f"layer-fail-{i}",
                "review_override_applied": True,
                "success": False,
            }
            event.timestamp = (datetime.utcnow() - timedelta(hours=i)).isoformat() + "Z"
            mock_events.append(event)

        mock_store.query_events.return_value = mock_events

        analyzer = FeedbackAnalyzer("test_tenant", event_store=mock_store)
        pattern = analyzer.analyze_recent_overrides(limit=50)

        assert pattern.success_rate == 0.5
        assert pattern.signal == OptimizationSignal.NEUTRAL
        assert pattern.confidence_delta == 0.0


class TestFeedbackPatternSignificance:
    """Test pattern significance calculation."""

    def test_pattern_significance_high_success(self):
        """Significant pattern: >= 5 overrides, >= 70% success."""
        pattern = FeedbackPattern(
            total_overrides=7,
            successful_overrides=5,
            failed_overrides=2,
            success_rate=0.714,  # 5/7
            confidence_delta=0.15,
            signal=OptimizationSignal.OVERCAUTIOUS,
            review_prompt_version="v1.0",
        )
        assert pattern.is_significant is True

    def test_pattern_not_significant_too_few_overrides(self):
        """Not significant: too few overrides (< 5)."""
        pattern = FeedbackPattern(
            total_overrides=3,
            successful_overrides=2,
            failed_overrides=1,
            success_rate=0.667,
            confidence_delta=0.15,
            signal=OptimizationSignal.OVERCAUTIOUS,
            review_prompt_version="v1.0",
        )
        assert pattern.is_significant is False

    def test_pattern_not_significant_low_success_rate(self):
        """Not significant: success rate < 70%."""
        pattern = FeedbackPattern(
            total_overrides=10,
            successful_overrides=6,
            failed_overrides=4,
            success_rate=0.60,  # < 70%
            confidence_delta=0.10,
            signal=OptimizationSignal.OVERCAUTIOUS,
            review_prompt_version="v1.0",
        )
        assert pattern.is_significant is False


class TestOptimizerEngine:
    """Test optimizer engine and update suggestions."""

    def test_suggest_update_overcautious(self, optimizer_engine):
        """Suggest prompt update for overcautious review."""
        pattern = FeedbackPattern(
            total_overrides=7,
            successful_overrides=6,
            failed_overrides=1,
            success_rate=0.857,
            confidence_delta=0.15,
            signal=OptimizationSignal.OVERCAUTIOUS,
            review_prompt_version="v1.0",
        )

        update = optimizer_engine.suggest_update(pattern)

        assert update is not None
        assert update.old_version == "v1.0"
        assert update.new_version == "v1.1"
        assert update.signal == OptimizationSignal.OVERCAUTIOUS
        assert update.success_rate == 0.857
        assert update.total_overrides == 7

    def test_suggest_update_undercautious(self, optimizer_engine):
        """Suggest prompt update for undercautious review."""
        pattern = FeedbackPattern(
            total_overrides=7,
            successful_overrides=1,
            failed_overrides=6,
            success_rate=0.143,
            confidence_delta=-0.20,
            signal=OptimizationSignal.UNDERCAUTIOUS,
            review_prompt_version="v1.0",
        )

        update = optimizer_engine.suggest_update(pattern)

        assert update is not None
        assert update.old_version == "v1.0"
        assert update.new_version == "v1.1"
        assert update.signal == OptimizationSignal.UNDERCAUTIOUS

    def test_suggest_update_not_significant(self, optimizer_engine):
        """No suggestion for non-significant pattern."""
        pattern = FeedbackPattern(
            total_overrides=2,
            successful_overrides=1,
            failed_overrides=1,
            success_rate=0.5,
            confidence_delta=0.0,
            signal=OptimizationSignal.NEUTRAL,
            review_prompt_version="v1.0",
        )

        update = optimizer_engine.suggest_update(pattern)
        assert update is None

    def test_apply_update_creates_new_version(self, optimizer_engine):
        """Applying an update creates a new version and marks it current."""
        old_version = optimizer_engine.versions.current_version()

        update = OptimizerUpdate(
            old_version="v1.0",
            new_version="v1.1",
            reason="overcautious feedback",
            signal=OptimizationSignal.OVERCAUTIOUS,
            success_rate=0.8,
            total_overrides=10,
        )

        new_prompt_text = "Updated adversarial review prompt (v1.1)"
        optimizer_engine.apply_update(update, new_prompt_text)

        current = optimizer_engine.versions.current_version()
        assert current.version == "v1.1"
        assert current.prompt_text == new_prompt_text
        assert current.parent_version == "v1.0"
        assert current.reason == "overcautious feedback"

    def test_next_version_increments_minor(self):
        """Next version from v1.0 is v1.1."""
        next_v = OptimizerEngine._next_version("v1.0")
        assert next_v == "v1.1"

    def test_next_version_rolls_over_major(self):
        """Next version from v1.9 is v2.0."""
        next_v = OptimizerEngine._next_version("v1.9")
        assert next_v == "v2.0"

    def test_next_version_chain(self):
        """Version increments correctly through a chain."""
        # One step each — the first version of this test listed v1.2 -> v1.9, which is not a step.
        versions = ["v1.0", "v1.1", "v1.2", "v1.3", "v1.4", "v1.5", "v1.6", "v1.7", "v1.8", "v1.9", "v2.0", "v2.1"]
        for i, current in enumerate(versions[:-1]):
            next_v = OptimizerEngine._next_version(current)
            assert next_v == versions[i + 1], (current, next_v)
        assert OptimizerEngine._next_version("v9.9") == "v10.0"

    def test_emit_update_event(self, optimizer_engine):
        """Emit optimizer update audit event."""
        update = OptimizerUpdate(
            old_version="v1.0",
            new_version="v1.1",
            reason="overcautious feedback",
            signal=OptimizationSignal.OVERCAUTIOUS,
            success_rate=0.8,
            total_overrides=10,
        )

        with patch("core.orchestration.layer_forge.audit.emit") as mock_emit:
            mock_emit.return_value = "audit-event-id-123"
            result = optimizer_engine.emit_update_event(update)

        assert result is True
        mock_emit.assert_called_once()
        # Verify the event was called with the right details
        call_kwargs = mock_emit.call_args[1]
        assert call_kwargs["old_version"] == "v1.0"
        assert call_kwargs["new_version"] == "v1.1"
        assert call_kwargs["signal"] == "overcautious"


class TestLearningOptimizerE2E:
    """E2E tests of the complete learning optimizer loop."""

    def test_full_loop_overcautious(self, temp_storage):
        """Full E2E: overcautious feedback → pattern → update → audit."""
        # 1. Create feedback analyzer and mock outcomes
        mock_store = MagicMock()
        mock_events = []
        for i in range(8):
            event = MagicMock()
            event.signal = {
                "entry_id": f"layer-{i}",
                "review_override_applied": True,
                "success": True,
            }
            event.timestamp = (datetime.utcnow() - timedelta(hours=i)).isoformat() + "Z"
            mock_events.append(event)

        mock_store.query_events.return_value = mock_events

        analyzer = FeedbackAnalyzer("_default", event_store=mock_store)
        pattern = analyzer.analyze_recent_overrides()

        assert pattern.is_significant is True
        assert pattern.signal == OptimizationSignal.OVERCAUTIOUS

        # 2. Engine suggests update
        engine = OptimizerEngine("_default", temp_storage)
        update = engine.suggest_update(pattern)

        assert update is not None
        assert update.signal == OptimizationSignal.OVERCAUTIOUS

        # 3. Apply update
        new_prompt = "Relaxed adversarial review prompt (v1.1)"
        engine.apply_update(update, new_prompt)

        # 4. Verify new version is current
        current = engine.versions.current_version()
        assert current.version == "v1.1"
        assert current.prompt_text == new_prompt

        # 5. Emit audit event
        with patch("core.orchestration.layer_forge.audit.emit") as mock_emit:
            mock_emit.return_value = "audit-123"
            result = engine.emit_update_event(update)
            assert result is True

    def test_config_versioning_convergence(self, temp_storage):
        """Test that config versioning tracks convergence across multiple updates."""
        engine = OptimizerEngine("_default", temp_storage)

        # Simulate 3 rounds of feedback-driven updates
        updates_and_prompts = [
            (
                OptimizerUpdate(
                    old_version="v1.0",
                    new_version="v1.1",
                    reason="Round 1: overcautious",
                    signal=OptimizationSignal.OVERCAUTIOUS,
                    success_rate=0.8,
                    total_overrides=10,
                ),
                "Prompt v1.1: relaxed concerns",
            ),
            (
                OptimizerUpdate(
                    old_version="v1.1",
                    new_version="v1.2",
                    reason="Round 2: still overcautious",
                    signal=OptimizationSignal.OVERCAUTIOUS,
                    success_rate=0.75,
                    total_overrides=12,
                ),
                "Prompt v1.2: further relaxed",
            ),
            (
                OptimizerUpdate(
                    old_version="v1.2",
                    new_version="v1.3",
                    reason="Round 3: now balanced",
                    signal=OptimizationSignal.NEUTRAL,
                    success_rate=0.6,
                    total_overrides=15,
                ),
                "Prompt v1.3: balanced approach",
            ),
        ]

        for update, prompt in updates_and_prompts:
            engine.apply_update(update, prompt)

        # Verify final version is v1.3
        current = engine.versions.current_version()
        assert current.version == "v1.3"
        assert current.prompt_text == "Prompt v1.3: balanced approach"

        # Verify version chain
        v1_2 = engine.versions.get_version("v1.2")
        assert v1_2 is not None
        assert v1_2.parent_version == "v1.1"

        v1_1 = engine.versions.get_version("v1.1")
        assert v1_1 is not None
        assert v1_1.parent_version == "v1.0"

    def test_review_verdict_includes_prompt_version(self):
        """ReviewVerdict correctly includes and tracks prompt version."""
        # Default version
        v1 = ReviewVerdict("PASS", flags=[], prompt_version="v1.0")
        assert v1.prompt_version == "v1.0"

        # Explicit version
        v2 = ReviewVerdict("FLAGGED", flags=[ReviewFlag.SCOPE_CREEP], prompt_version="v1.1")
        assert v2.prompt_version == "v1.1"

        # to_dict includes version
        d = v2.to_dict()
        assert d["prompt_version"] == "v1.1"

    def test_review_prompt_version_default(self):
        """ReviewVerdict defaults to REVIEW_PROMPT_VERSION."""
        from core.orchestration.layer_forge.review import REVIEW_PROMPT_VERSION

        v = ReviewVerdict("PASS", flags=[])
        assert v.prompt_version == REVIEW_PROMPT_VERSION


class TestOptimizerAuditIntegration:
    """Test audit event integration."""

    def test_optimizer_audit_event_complete(self):
        """Optimizer audit events contain all required fields."""
        update = OptimizerUpdate(
            old_version="v1.0",
            new_version="v1.1",
            reason="Overcautious feedback from operators",
            signal=OptimizationSignal.OVERCAUTIOUS,
            success_rate=0.85,
            total_overrides=20,
        )

        # Verify OptimizerUpdate has all required fields
        assert update.old_version == "v1.0"
        assert update.new_version == "v1.1"
        assert update.reason is not None
        assert update.signal == OptimizationSignal.OVERCAUTIOUS
        assert update.success_rate == 0.85
        assert update.total_overrides == 20
        assert update.timestamp is not None  # Auto-generated


__all__ = [
    "TestReviewPromptVersions",
    "TestFeedbackAnalyzer",
    "TestFeedbackPatternSignificance",
    "TestOptimizerEngine",
    "TestLearningOptimizerE2E",
    "TestOptimizerAuditIntegration",
]
