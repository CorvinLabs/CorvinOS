"""Tests for Context Coherence — Cross-session tool/strategy inheritance (Gap 5, ADR-0325)."""

import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from core.orchestration.context_coherence import (
    ToolCoherence,
    ToolSuccessRate,
    SessionCheckpointWithCoherence,
    ContextCoherenceManager,
    ConflictResolutionStrategy,
)


@pytest.fixture
def base_coherence():
    """Create a base ToolCoherence for testing."""
    return ToolCoherence(
        parent_session_id=None,
        parent_coherence_id=None,
        coherence_chain=[],
        tenant_id="_default",
    )


@pytest.fixture
def parent_coherence():
    """Create a parent coherence with some learning history."""
    coherence = ToolCoherence(
        parent_session_id="parent_session_1",
        parent_coherence_id="parent_coh_1",
        coherence_chain=["parent_coh_1"],
        tenant_id="_default",
    )

    # Add some tool success rates
    coherence.record_tool_execution(
        tool_id="tool_1",
        error_class="syntax",
        succeeded=True,
        latency_ms=100,
        cost_cents=50,
    )
    coherence.record_tool_execution(
        tool_id="tool_1",
        error_class="syntax",
        succeeded=True,
        latency_ms=110,
        cost_cents=55,
    )
    coherence.record_tool_execution(
        tool_id="tool_2",
        error_class="logic",
        succeeded=False,
        latency_ms=200,
        cost_cents=100,
    )

    return coherence


@pytest.fixture
def coherence_manager():
    """Create a ContextCoherenceManager for testing."""
    return ContextCoherenceManager(max_age_hours=24)


class TestToolSuccessRate:
    """Tests for ToolSuccessRate dataclass."""

    def test_tool_success_rate_creation(self):
        """Test creating a ToolSuccessRate."""
        rate = ToolSuccessRate(
            error_class="syntax",
            success_count=18,
            total_count=20,
            avg_latency_ms=100,
            avg_cost_cents=50,
            last_used_timestamp=datetime.now(timezone.utc),
        )

        assert rate.error_class == "syntax"
        assert rate.success_count == 18
        assert rate.total_count == 20
        assert rate.success_rate == 0.9
        assert rate.confidence == min(1.0, 20 / 30)  # 20/30 samples

    def test_tool_success_rate_confidence_converges(self):
        """Test that confidence converges at 30 samples."""
        rate_10 = ToolSuccessRate(
            error_class="logic",
            success_count=8,
            total_count=10,
            avg_latency_ms=100,
            avg_cost_cents=50,
            last_used_timestamp=datetime.now(timezone.utc),
        )
        assert rate_10.confidence == 10 / 30  # 0.333

        rate_30 = ToolSuccessRate(
            error_class="logic",
            success_count=24,
            total_count=30,
            avg_latency_ms=100,
            avg_cost_cents=50,
            last_used_timestamp=datetime.now(timezone.utc),
        )
        assert rate_30.confidence == 1.0  # Capped at 1.0

        rate_50 = ToolSuccessRate(
            error_class="logic",
            success_count=40,
            total_count=50,
            avg_latency_ms=100,
            avg_cost_cents=50,
            last_used_timestamp=datetime.now(timezone.utc),
        )
        assert rate_50.confidence == 1.0  # Still capped at 1.0


class TestToolCoherence:
    """Tests for ToolCoherence dataclass."""

    def test_tool_coherence_creation(self, base_coherence):
        """Test creating a ToolCoherence."""
        assert base_coherence.parent_session_id is None
        assert base_coherence.tenant_id == "_default"
        assert len(base_coherence.tools_known_good) == 0
        assert len(base_coherence.tools_known_bad) == 0

    def test_tool_coherence_is_not_stale_when_recent(self, base_coherence):
        """Test that recent coherence is not marked stale."""
        assert not base_coherence.is_stale(max_age_hours=24)

    def test_tool_coherence_is_stale_when_old(self):
        """Test that old coherence is marked stale."""
        old_coherence = ToolCoherence(
            created_at=datetime.now(timezone.utc) - timedelta(hours=25),
            tenant_id="_default",
        )
        assert old_coherence.is_stale(max_age_hours=24)

    def test_record_tool_execution_success(self, base_coherence):
        """Test recording a successful tool execution."""
        base_coherence.record_tool_execution(
            tool_id="tool_1",
            error_class="syntax",
            succeeded=True,
            latency_ms=100,
            cost_cents=50,
        )

        assert "tool_1" in base_coherence.tools_known_good
        assert base_coherence.tools_known_good["tool_1"] == 1.0
        assert "syntax" in base_coherence.success_rates_per_error

        rate = base_coherence.success_rates_per_error["syntax"]["tool_1"]
        assert rate.success_count == 1
        assert rate.total_count == 1
        assert rate.success_rate == 1.0

    def test_record_tool_execution_failure(self, base_coherence):
        """Test recording a failed tool execution."""
        base_coherence.record_tool_execution(
            tool_id="tool_bad",
            error_class="logic",
            succeeded=False,
            latency_ms=200,
            cost_cents=100,
        )

        assert "tool_bad" in base_coherence.tools_known_bad
        assert "tool_bad" not in base_coherence.tools_known_good

    def test_record_tool_execution_multiple_calls_averaging(self, base_coherence):
        """Test that multiple executions average latency and cost."""
        base_coherence.record_tool_execution(
            tool_id="tool_1",
            error_class="syntax",
            succeeded=True,
            latency_ms=100,
            cost_cents=50,
        )
        base_coherence.record_tool_execution(
            tool_id="tool_1",
            error_class="syntax",
            succeeded=True,
            latency_ms=200,
            cost_cents=100,
        )

        rate = base_coherence.success_rates_per_error["syntax"]["tool_1"]
        assert rate.total_count == 2
        assert rate.success_count == 2
        # Average: (100 + 200) / 2 = 150
        assert rate.avg_latency_ms == 150
        # Average: (50 + 100) / 2 = 75
        assert rate.avg_cost_cents == 75

    def test_get_success_rate_for_tool_and_error(self, parent_coherence):
        """Test querying success rate for tool and error class."""
        rate = parent_coherence.get_success_rate_for_tool_and_error(
            tool_id="tool_1",
            error_class="syntax",
        )
        assert rate == 1.0  # 2/2 successful

        rate_not_found = parent_coherence.get_success_rate_for_tool_and_error(
            tool_id="tool_1",
            error_class="unknown",
        )
        assert rate_not_found is None

    def test_get_recommended_tools_for_error(self, parent_coherence):
        """Test getting recommended tools ranked by success rate."""
        # Add more tools to parent
        parent_coherence.record_tool_execution(
            tool_id="tool_3",
            error_class="syntax",
            succeeded=True,
            latency_ms=80,
            cost_cents=40,
        )
        parent_coherence.record_tool_execution(
            tool_id="tool_3",
            error_class="syntax",
            succeeded=True,
            latency_ms=85,
            cost_cents=45,
        )
        parent_coherence.record_tool_execution(
            tool_id="tool_3",
            error_class="syntax",
            succeeded=False,
            latency_ms=90,
            cost_cents=50,
        )

        # tool_1: 2/2 (100%), tool_3: 2/3 (67%)
        recommendations = parent_coherence.get_recommended_tools_for_error(
            error_class="syntax",
            top_n=3,
            min_confidence=0.2,
        )

        assert len(recommendations) == 2
        # Should be sorted by success rate (tool_1 first, then tool_3)
        assert recommendations[0][0] == "tool_1"
        assert recommendations[0][1] == 1.0
        assert recommendations[1][0] == "tool_3"
        assert abs(recommendations[1][1] - 0.667) < 0.01

    def test_record_cost_estimate(self, base_coherence):
        """Test recording cost estimates for calibration."""
        base_coherence.record_cost_estimate(estimated=100, actual=110)
        base_coherence.record_cost_estimate(estimated=200, actual=180)

        assert len(base_coherence.cost_deltas) == 2
        assert base_coherence.cost_deltas[0] == 10  # 110 - 100
        assert base_coherence.cost_deltas[1] == -20  # 180 - 200

        # Average error: (|10| + |-20|) / 2 = 15
        assert base_coherence.average_cost_error() == 15.0

    def test_tool_coherence_serialization(self, parent_coherence):
        """Test serializing and deserializing coherence."""
        parent_coherence.learned_strategies["syntax"] = "direct_fix"
        parent_coherence.learned_preferences["model"] = "opus"

        data = parent_coherence.to_dict()
        restored = ToolCoherence.from_dict(data)

        assert restored.parent_session_id == parent_coherence.parent_session_id
        assert restored.tenant_id == parent_coherence.tenant_id
        assert restored.learned_strategies == parent_coherence.learned_strategies
        assert restored.learned_preferences == parent_coherence.learned_preferences

        # Verify tool success rates survived serialization
        restored_rate = restored.get_success_rate_for_tool_and_error(
            tool_id="tool_1",
            error_class="syntax",
        )
        assert restored_rate == 1.0


class TestSessionCheckpointWithCoherence:
    """Tests for SessionCheckpointWithCoherence."""

    def test_checkpoint_creation(self, base_coherence):
        """Test creating a checkpoint with coherence."""
        checkpoint = SessionCheckpointWithCoherence(
            task_id="task_1",
            session_id="session_1",
            parent_session_id=None,
            coherence=base_coherence,
            completion_percentage=50.0,
            tenant_id="_default",
        )

        assert checkpoint.task_id == "task_1"
        assert checkpoint.session_id == "session_1"
        assert checkpoint.coherence == base_coherence

    def test_checkpoint_serialization(self, parent_coherence):
        """Test serializing and deserializing checkpoint."""
        checkpoint = SessionCheckpointWithCoherence(
            task_id="task_1",
            session_id="session_1",
            parent_session_id="parent_session_1",
            coherence=parent_coherence,
            completion_percentage=75.0,
            tenant_id="_default",
        )

        data = checkpoint.to_dict()
        restored = SessionCheckpointWithCoherence.from_dict(data)

        assert restored.task_id == "task_1"
        assert restored.session_id == "session_1"
        assert restored.parent_session_id == "parent_session_1"
        assert restored.completion_percentage == 75.0
        assert restored.coherence is not None
        assert restored.coherence.parent_session_id == "parent_session_1"


class TestContextCoherenceManager:
    """Tests for ContextCoherenceManager."""

    def test_create_coherence_without_parent(self, coherence_manager):
        """Test creating a new coherence without parent."""
        coherence = coherence_manager.create_coherence(
            task_id="task_1",
            session_id="session_1",
            tenant_id="_default",
        )

        assert coherence.parent_session_id is None
        assert coherence.coherence_chain == []

        # Verify cached
        cached = coherence_manager.get_coherence("task_1")
        assert cached == coherence

    def test_create_coherence_with_fresh_parent(
        self, coherence_manager, parent_coherence
    ):
        """Test creating coherence with fresh parent."""
        coherence = coherence_manager.create_coherence(
            task_id="task_1",
            session_id="session_1",
            tenant_id="_default",
            parent_coherence=parent_coherence,
        )

        assert coherence.parent_session_id == "parent_session_1"
        assert len(coherence.coherence_chain) > 0

    def test_create_coherence_rejects_stale_parent(self, coherence_manager):
        """Test that stale parent is rejected."""
        stale_parent = ToolCoherence(
            parent_session_id="old_session",
            created_at=datetime.now(timezone.utc) - timedelta(hours=25),
            tenant_id="_default",
        )

        coherence = coherence_manager.create_coherence(
            task_id="task_1",
            session_id="session_1",
            tenant_id="_default",
            parent_coherence=stale_parent,
        )

        # Stale parent should be ignored
        assert coherence.parent_session_id is None

    def test_inherit_parent_context_success(
        self, coherence_manager, parent_coherence
    ):
        """Test inheriting from parent context."""
        # Create child coherence
        child = coherence_manager.create_coherence(
            task_id="task_child",
            session_id="session_2",
            tenant_id="_default",
        )

        # Inherit from parent
        success = coherence_manager.inherit_parent_context(
            task_id="task_child",
            parent_coherence=parent_coherence,
            strategy=ConflictResolutionStrategy.BLEND,
        )

        assert success

        # Verify inheritance
        child = coherence_manager.get_coherence("task_child")
        assert len(child.tools_known_good) > 0  # Should have inherited tools
        assert len(child.learned_strategies) == len(parent_coherence.learned_strategies)

    def test_inherit_parent_context_rejects_stale(self, coherence_manager):
        """Test that inheritance rejects stale parent."""
        child = coherence_manager.create_coherence(
            task_id="task_child",
            session_id="session_2",
            tenant_id="_default",
        )

        stale_parent = ToolCoherence(
            parent_session_id="old",
            created_at=datetime.now(timezone.utc) - timedelta(hours=25),
            tenant_id="_default",
        )

        success = coherence_manager.inherit_parent_context(
            task_id="task_child",
            parent_coherence=stale_parent,
            strategy=ConflictResolutionStrategy.BLEND,
        )

        assert not success

    def test_inherit_parent_context_tenant_mismatch(self, coherence_manager):
        """Test that inheritance rejects tenant mismatch."""
        child = coherence_manager.create_coherence(
            task_id="task_child",
            session_id="session_2",
            tenant_id="tenant_a",
        )

        parent = ToolCoherence(
            parent_session_id="old",
            tenant_id="tenant_b",  # Different tenant!
        )

        success = coherence_manager.inherit_parent_context(
            task_id="task_child",
            parent_coherence=parent,
            strategy=ConflictResolutionStrategy.BLEND,
        )

        assert not success

    def test_inherit_conflict_resolution_parent_preferred(
        self, coherence_manager, parent_coherence
    ):
        """Test PARENT_PREFERRED conflict resolution."""
        # Create child with own tools
        child = coherence_manager.create_coherence(
            task_id="task_child",
            session_id="session_2",
            tenant_id="_default",
        )
        child_coherence = coherence_manager.get_coherence("task_child")
        child_coherence.tools_known_good["my_tool"] = 0.9

        # Inherit with PARENT_PREFERRED
        coherence_manager.inherit_parent_context(
            task_id="task_child",
            parent_coherence=parent_coherence,
            strategy=ConflictResolutionStrategy.PARENT_PREFERRED,
        )

        # Parent tools should override
        updated = coherence_manager.get_coherence("task_child")
        assert "my_tool" not in updated.tools_known_good

    def test_inherit_conflict_resolution_current_preferred(
        self, coherence_manager, parent_coherence
    ):
        """Test CURRENT_PREFERRED conflict resolution."""
        # Create child with own tools
        child = coherence_manager.create_coherence(
            task_id="task_child",
            session_id="session_2",
            tenant_id="_default",
        )
        child_coherence = coherence_manager.get_coherence("task_child")
        child_coherence.tools_known_good["my_tool"] = 0.9

        # Inherit with CURRENT_PREFERRED
        coherence_manager.inherit_parent_context(
            task_id="task_child",
            parent_coherence=parent_coherence,
            strategy=ConflictResolutionStrategy.CURRENT_PREFERRED,
        )

        # Child tools should be preserved
        updated = coherence_manager.get_coherence("task_child")
        assert "my_tool" in updated.tools_known_good
        assert updated.tools_known_good["my_tool"] == 0.9

    def test_inherit_conflict_resolution_blend(
        self, coherence_manager, parent_coherence
    ):
        """Test BLEND conflict resolution."""
        # Create child with own tools
        child = coherence_manager.create_coherence(
            task_id="task_child",
            session_id="session_2",
            tenant_id="_default",
        )
        child_coherence = coherence_manager.get_coherence("task_child")
        child_coherence.tools_known_good["my_tool"] = 0.9

        # Inherit with BLEND
        coherence_manager.inherit_parent_context(
            task_id="task_child",
            parent_coherence=parent_coherence,
            strategy=ConflictResolutionStrategy.BLEND,
        )

        # Both child and parent tools should be present
        updated = coherence_manager.get_coherence("task_child")
        assert "my_tool" in updated.tools_known_good
        # Parent tools also added
        assert len(updated.tools_known_good) >= 1

    def test_validate_coherence_chain_no_cycle(self, coherence_manager):
        """Test that valid DAG passes validation."""
        coherence = coherence_manager.create_coherence(
            task_id="task_1",
            session_id="session_1",
            tenant_id="_default",
        )
        coherence.coherence_chain = ["coh_1", "coh_2", "coh_3"]  # Valid DAG

        assert coherence_manager.validate_coherence_chain(coherence)

    def test_validate_coherence_chain_detects_cycle(self, coherence_manager):
        """Test that cycles are detected."""
        coherence = coherence_manager.create_coherence(
            task_id="task_1",
            session_id="session_1",
            tenant_id="_default",
        )
        coherence.coherence_chain = ["coh_1", "coh_2", "coh_1"]  # Cycle!

        assert not coherence_manager.validate_coherence_chain(coherence)

    def test_clear_coherence(self, coherence_manager):
        """Test clearing coherence from cache."""
        coherence = coherence_manager.create_coherence(
            task_id="task_1",
            session_id="session_1",
            tenant_id="_default",
        )

        assert coherence_manager.get_coherence("task_1") is not None

        coherence_manager.clear_coherence("task_1")

        assert coherence_manager.get_coherence("task_1") is None

    def test_coherence_manager_get_nonexistent_task(self, coherence_manager):
        """Test getting coherence for nonexistent task."""
        result = coherence_manager.get_coherence("nonexistent")
        assert result is None

    def test_multi_session_coherence_chain(self, coherence_manager):
        """Test creating a coherence chain across 3 sessions."""
        # Session 1
        coh_1 = coherence_manager.create_coherence(
            task_id="task_1",
            session_id="session_1",
            tenant_id="_default",
        )
        coh_1_obj = coherence_manager.get_coherence("task_1")
        coh_1_obj.record_tool_execution(
            tool_id="tool_a",
            error_class="syntax",
            succeeded=True,
            latency_ms=100,
            cost_cents=50,
        )

        # Session 2 inherits from Session 1
        coh_2 = coherence_manager.create_coherence(
            task_id="task_2",
            session_id="session_2",
            tenant_id="_default",
            parent_coherence=coh_1_obj,
        )

        assert len(coh_2.coherence_chain) >= 1

        # Session 3 inherits from Session 2
        coh_2_obj = coherence_manager.get_coherence("task_2")
        coh_3 = coherence_manager.create_coherence(
            task_id="task_3",
            session_id="session_3",
            tenant_id="_default",
            parent_coherence=coh_2_obj,
        )

        # Chain should grow
        assert len(coh_3.coherence_chain) >= len(coh_2.coherence_chain)


class TestContextCoherenceIntegration:
    """Integration tests for context coherence across workflows."""

    def test_end_to_end_multi_session_learning(self, coherence_manager):
        """Test end-to-end learning across multiple sessions."""
        # Session 1: Learn that tool_a works for syntax errors
        coh_1 = coherence_manager.create_coherence(
            task_id="task_1",
            session_id="session_1",
            tenant_id="_default",
        )
        coh_1.record_tool_execution(
            tool_id="tool_a",
            error_class="syntax",
            succeeded=True,
            latency_ms=100,
            cost_cents=50,
        )
        coh_1.learned_strategies["syntax"] = "use_tool_a"

        # Session 2: Inherit and continue learning
        coh_1_obj = coherence_manager.get_coherence("task_1")
        coh_2 = coherence_manager.create_coherence(
            task_id="task_2",
            session_id="session_2",
            tenant_id="_default",
            parent_coherence=coh_1_obj,
        )

        coherence_manager.inherit_parent_context(
            task_id="task_2",
            parent_coherence=coh_1_obj,
            strategy=ConflictResolutionStrategy.BLEND,
        )

        # Verify inheritance
        coh_2_obj = coherence_manager.get_coherence("task_2")
        recommendations = coh_2_obj.get_recommended_tools_for_error(
            error_class="syntax",
            top_n=1,
        )

        assert len(recommendations) > 0
        assert recommendations[0][0] == "tool_a"
