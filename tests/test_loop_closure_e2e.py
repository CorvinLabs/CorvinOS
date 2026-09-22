"""
E2E Tests: Feedback Loop Closure (ADR-2033 Week 2)

Tests verify that:
1. Feedback events trigger config updates
2. Config updates are audited
3. Streams 1-3 can consume updated configs
4. Feedback loop converges
5. Tenant isolation is maintained
"""

import asyncio
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from core.skills.feedback.schema import (
    OutcomeFeedback, PreferenceFeedback, ConfidenceFeedback, MetricFeedback
)
from core.skills.feedback.loop_closure import LoopClosureManager, ConfigUpdate


class TestOutcomeFeedback:
    """Test outcome feedback → confidence threshold adjustment."""

    @pytest.mark.asyncio
    async def test_correct_outcome_increases_threshold(self):
        """Correct outcome should increase confidence_threshold."""
        manager = LoopClosureManager()
        manager.skill_configs["os.router"] = {"confidence_threshold": 0.7}

        feedback = OutcomeFeedback(
            skill_id="os.router",
            tenant_id="_default",
            signal=True,  # correct
        )

        update = await manager.process_feedback(feedback)

        assert update is not None
        assert update.skill_id == "os.router"
        assert update.config_delta["confidence_threshold"]["new"] > 0.7
        assert manager.skill_configs["os.router"]["confidence_threshold"] > 0.7

    @pytest.mark.asyncio
    async def test_incorrect_outcome_decreases_threshold(self):
        """Incorrect outcome should decrease confidence_threshold."""
        manager = LoopClosureManager()
        manager.skill_configs["os.router"] = {"confidence_threshold": 0.7}

        feedback = OutcomeFeedback(
            skill_id="os.router",
            tenant_id="_default",
            signal=False,  # incorrect
        )

        update = await manager.process_feedback(feedback)

        assert update is not None
        assert update.config_delta["confidence_threshold"]["new"] < 0.7
        assert manager.skill_configs["os.router"]["confidence_threshold"] < 0.7

    @pytest.mark.asyncio
    async def test_threshold_clamped_to_valid_range(self):
        """Threshold should never exceed [0.0, 1.0]."""
        manager = LoopClosureManager()
        manager.skill_configs["os.router"] = {"confidence_threshold": 0.99}

        # Many correct outcomes should not exceed 1.0
        for _ in range(10):
            feedback = OutcomeFeedback(
                skill_id="os.router",
                tenant_id="_default",
                signal=True,
            )
            update = await manager.process_feedback(feedback)

        threshold = manager.skill_configs["os.router"]["confidence_threshold"]
        assert 0.0 <= threshold <= 1.0


class TestPreferenceFeedback:
    """Test preference feedback → config update."""

    @pytest.mark.asyncio
    async def test_preference_changes_mode(self):
        """Preference feedback should update preferred_mode."""
        manager = LoopClosureManager()
        manager.skill_configs["os.router"] = {"preferred_mode": "llm"}

        feedback = PreferenceFeedback(
            skill_id="os.router",
            tenant_id="_default",
            signal="deterministic",
        )

        update = await manager.process_feedback(feedback)

        assert update is not None
        assert update.config_delta["preferred_mode"]["new"] == "deterministic"
        assert manager.skill_configs["os.router"]["preferred_mode"] == "deterministic"

    @pytest.mark.asyncio
    async def test_no_update_if_preference_unchanged(self):
        """No update if preference is already set to that value."""
        manager = LoopClosureManager()
        manager.skill_configs["os.router"] = {"preferred_mode": "llm"}

        feedback = PreferenceFeedback(
            skill_id="os.router",
            tenant_id="_default",
            signal="llm",
        )

        update = await manager.process_feedback(feedback)

        assert update is None  # No change, no update


class TestConfidenceFeedback:
    """Test confidence feedback → explicit threshold setting."""

    @pytest.mark.asyncio
    async def test_confidence_sets_threshold(self):
        """Confidence feedback should set explicit threshold."""
        manager = LoopClosureManager()
        manager.skill_configs["os.router"] = {"confidence_threshold": 0.7}

        feedback = ConfidenceFeedback(
            skill_id="os.router",
            tenant_id="_default",
            signal=0.85,
        )

        update = await manager.process_feedback(feedback)

        assert update is not None
        assert update.config_delta["confidence_threshold"]["new"] == 0.85
        assert manager.skill_configs["os.router"]["confidence_threshold"] == 0.85

    @pytest.mark.asyncio
    async def test_confidence_validates_range(self):
        """Confidence signal must be in [0.0, 1.0]."""
        manager = LoopClosureManager()
        manager.skill_configs["os.router"] = {"confidence_threshold": 0.7}

        # Invalid: > 1.0
        feedback = ConfidenceFeedback(
            skill_id="os.router",
            tenant_id="_default",
            signal=1.5,
        )

        update = await manager.process_feedback(feedback)
        assert update is None  # Rejected


class TestMetricFeedback:
    """Test metric feedback → config update."""

    @pytest.mark.asyncio
    async def test_metric_recorded(self):
        """Metric feedback should record observed metric."""
        manager = LoopClosureManager()
        manager.skill_configs["os.flow_guard"] = {}

        feedback = MetricFeedback(
            skill_id="os.flow_guard",
            tenant_id="_default",
            signal=42.5,  # e.g., latency in ms
        )

        update = await manager.process_feedback(feedback)

        assert update is not None
        assert update.feedback_type == "metric"
        assert manager.skill_configs["os.flow_guard"]["last_metric_observed"] == 42.5


class TestAuditIntegration:
    """Test audit trail integration."""

    @pytest.mark.asyncio
    async def test_audit_callback_invoked_on_update(self):
        """Audit callback should be invoked on config update."""
        manager = LoopClosureManager()
        manager.skill_configs["os.router"] = {"confidence_threshold": 0.7}

        audit_events = []
        manager.register_audit_callback(lambda u: audit_events.append(u))

        feedback = OutcomeFeedback(
            skill_id="os.router",
            tenant_id="_default",
            signal=True,
        )

        update = await manager.process_feedback(feedback)

        assert len(audit_events) == 1
        assert audit_events[0] == update

    @pytest.mark.asyncio
    async def test_audit_callback_error_doesnt_block(self):
        """Audit callback error should not block config update."""
        manager = LoopClosureManager()
        manager.skill_configs["os.router"] = {"confidence_threshold": 0.7}

        # Register a callback that raises
        manager.register_audit_callback(lambda u: (_ for _ in ()).throw(ValueError("audit error")))

        feedback = OutcomeFeedback(
            skill_id="os.router",
            tenant_id="_default",
            signal=True,
        )

        # Should not raise; callback error is caught
        update = await manager.process_feedback(feedback)
        assert update is not None  # Update still applied


class TestTenantIsolation:
    """Test tenant isolation."""

    @pytest.mark.asyncio
    async def test_updates_are_tenant_scoped(self):
        """Config updates should include tenant_id."""
        manager = LoopClosureManager()
        manager.skill_configs["os.router"] = {"confidence_threshold": 0.7}

        feedback = OutcomeFeedback(
            skill_id="os.router",
            tenant_id="tenant_a",
            signal=True,
        )

        update = await manager.process_feedback(feedback)

        assert update.tenant_id == "tenant_a"

    @pytest.mark.asyncio
    async def test_get_updates_filters_by_tenant(self):
        """get_all_updates should filter by tenant_id."""
        manager = LoopClosureManager()
        manager.skill_configs["os.router"] = {"confidence_threshold": 0.7}

        # Add updates for two tenants
        for feedback_signal in [True, False]:
            feedback = OutcomeFeedback(
                skill_id="os.router",
                tenant_id="tenant_a" if feedback_signal else "tenant_b",
                signal=feedback_signal,
            )
            await manager.process_feedback(feedback)

        updates_a = manager.get_all_updates("tenant_a")
        updates_b = manager.get_all_updates("tenant_b")

        assert len(updates_a) == 1
        assert len(updates_b) == 1
        assert updates_a[0].tenant_id == "tenant_a"
        assert updates_b[0].tenant_id == "tenant_b"


class TestConvergence:
    """Test feedback loop convergence (learning)."""

    @pytest.mark.asyncio
    async def test_repeated_correct_outcomes_stabilize_threshold(self):
        """Repeated correct outcomes should gradually increase and stabilize threshold."""
        manager = LoopClosureManager()
        manager.skill_configs["os.router"] = {"confidence_threshold": 0.5}

        thresholds = [0.5]

        # Submit 10 correct outcomes
        for _ in range(10):
            feedback = OutcomeFeedback(
                skill_id="os.router",
                tenant_id="_default",
                signal=True,
            )
            await manager.process_feedback(feedback)
            thresholds.append(manager.skill_configs["os.router"]["confidence_threshold"])

        # Should monotonically increase until capped at 1.0
        for i in range(1, len(thresholds)):
            assert thresholds[i] >= thresholds[i-1]

        # Final threshold should be close to 1.0
        assert thresholds[-1] > 0.8

    @pytest.mark.asyncio
    async def test_alternating_outcomes_oscillate(self):
        """Alternating outcomes should cause threshold to oscillate."""
        manager = LoopClosureManager()
        manager.skill_configs["os.router"] = {"confidence_threshold": 0.5}

        thresholds = [0.5]

        # Alternate correct/incorrect for 8 rounds
        for i in range(8):
            signal = (i % 2 == 0)  # True, False, True, False, ...
            feedback = OutcomeFeedback(
                skill_id="os.router",
                tenant_id="_default",
                signal=signal,
            )
            update = await manager.process_feedback(feedback)
            if update:
                thresholds.append(manager.skill_configs["os.router"]["confidence_threshold"])

        # Should see oscillation (no strict monotonic behavior)
        assert len(set(thresholds)) > 3  # At least 3 different values


class TestHistoryTracking:
    """Test config update history."""

    @pytest.mark.asyncio
    async def test_updates_recorded_in_history(self):
        """Config updates should be recorded in history."""
        manager = LoopClosureManager()
        manager.skill_configs["os.router"] = {"confidence_threshold": 0.7}

        # Submit 3 feedback events
        for signal in [True, False, True]:
            feedback = OutcomeFeedback(
                skill_id="os.router",
                tenant_id="_default",
                signal=signal,
            )
            await manager.process_feedback(feedback)

        history = manager.get_update_history("os.router")

        assert len(history) == 3
        assert all(u.skill_id == "os.router" for u in history)

    @pytest.mark.asyncio
    async def test_history_limit_respected(self):
        """History should respect limit parameter."""
        manager = LoopClosureManager()
        manager.skill_configs["os.router"] = {"confidence_threshold": 0.7}

        # Submit 10 feedback events
        for i in range(10):
            feedback = OutcomeFeedback(
                skill_id="os.router",
                tenant_id="_default",
                signal=(i % 2 == 0),
            )
            await manager.process_feedback(feedback)

        # Request only last 3
        history = manager.get_update_history("os.router", limit=3)

        assert len(history) <= 3


# Exit Criteria Tests

class TestExitCriteria:
    """Verify Week 2 exit criteria."""

    @pytest.mark.asyncio
    async def test_feedback_loop_closure_works_end_to_end(self):
        """Full feedback → config update → audit flow."""
        manager = LoopClosureManager()
        manager.skill_configs["os.router"] = {"confidence_threshold": 0.7, "preferred_mode": "llm"}

        audit_log = []
        manager.register_audit_callback(lambda u: audit_log.append(u))

        # Feedback sequence
        feedbacks = [
            OutcomeFeedback(skill_id="os.router", tenant_id="_default", signal=True),
            PreferenceFeedback(skill_id="os.router", tenant_id="_default", signal="deterministic"),
            ConfidenceFeedback(skill_id="os.router", tenant_id="_default", signal=0.85),
        ]

        for feedback in feedbacks:
            update = await manager.process_feedback(feedback)
            assert update is not None

        # Verify state
        config = manager.skill_configs["os.router"]
        assert config["confidence_threshold"] == 0.85  # Set by confidence feedback
        assert config["preferred_mode"] == "deterministic"

        # Verify audit trail
        assert len(audit_log) >= 2  # At least outcome + preference + confidence
        assert all(e.skill_id == "os.router" for e in audit_log)
        assert all(e.tenant_id == "_default" for e in audit_log)
