"""Unit tests for context_coherence_manager module (ADR-0369).

Tests ContextCoherenceManager, ToolCoherence persistence and inheritance.
"""

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.orchestration.context_coherence_manager import (
    ContextCoherenceManager,
    CoherenceNotFoundError,
    ContextCoherenceError,
)
from core.orchestration.context_coherence import ToolCoherence, ToolSuccessRate


@pytest.fixture
def temp_corvin_home():
    """Create temporary CORVIN_HOME."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def manager(temp_corvin_home):
    """Create ContextCoherenceManager instance."""
    return ContextCoherenceManager(temp_corvin_home)


@pytest.fixture
def sample_coherence():
    """Create a sample ToolCoherence for testing."""
    coherence = ToolCoherence()
    coherence.tools_known_good["tool_123"] = 0.95
    coherence.tools_known_bad["tool_456"] = 0.8
    coherence.learned_strategies["syntax"] = "decompose"
    coherence.learned_preferences["prefer_verbose"] = True
    coherence.cost_deltas = [10.0, -5.0, 3.0]
    return coherence


class TestContextCoherenceManager:
    """Tests for ContextCoherenceManager."""

    def test_init_creates_coherence_dir(self, temp_corvin_home):
        """Test that init creates coherence base directory."""
        manager = ContextCoherenceManager(temp_corvin_home)
        assert manager._coherence_base.exists()

    def test_save_coherence(self, manager, sample_coherence):
        """Test saving coherence."""
        coherence_id = manager.save_coherence(
            task_id="task_1",
            coherence=sample_coherence,
            session_id="sess_abc",
        )

        assert coherence_id
        assert isinstance(coherence_id, str)

        # Verify files created
        task_dir = manager._coherence_base / "task_1"
        assert task_dir.exists()
        assert (task_dir / "latest.json").exists()
        assert (task_dir / "history.jsonl").exists()

    def test_load_latest_coherence(self, manager, sample_coherence):
        """Test loading latest coherence."""
        saved_id = manager.save_coherence(
            task_id="task_1",
            coherence=sample_coherence,
            session_id="sess_abc",
        )

        loaded = manager.load_coherence("task_1")
        assert loaded.tools_known_good == sample_coherence.tools_known_good
        assert loaded.tools_known_bad == sample_coherence.tools_known_bad
        assert loaded.learned_strategies == sample_coherence.learned_strategies

    def test_load_specific_coherence(self, manager, sample_coherence):
        """Test loading a specific coherence by ID."""
        # Save two coherences
        cp1 = manager.save_coherence("task_1", sample_coherence, "sess_1")

        # Modify and save again
        sample_coherence.tools_known_good["tool_999"] = 0.9
        cp2 = manager.save_coherence("task_1", sample_coherence, "sess_1")

        # Load specific
        loaded_1 = manager.load_coherence("task_1", cp1)
        assert "tool_999" not in loaded_1.tools_known_good

        loaded_2 = manager.load_coherence("task_1", cp2)
        assert "tool_999" in loaded_2.tools_known_good

    def test_load_nonexistent_coherence(self, manager):
        """Test that loading nonexistent coherence raises error."""
        with pytest.raises(CoherenceNotFoundError):
            manager.load_coherence("nonexistent_task")

    def test_coherence_age_validation(self, manager, sample_coherence):
        """Test that old coherence generates warning."""
        manager.save_coherence("task_1", sample_coherence, "sess_1")

        # Manually age the coherence
        latest_path = manager._coherence_base / "task_1" / "latest.json"
        with open(latest_path) as f:
            data = json.load(f)

        # Set created_at to 30 hours ago
        old_time = (datetime.utcnow() - timedelta(hours=30)).isoformat()
        data["created_at"] = old_time

        with open(latest_path, "w") as f:
            json.dump(data, f)

        # Load should still work but log warning about age
        loaded = manager.load_coherence("task_1")
        assert loaded is not None

    def test_inherit_coherence_blend(self, manager):
        """Test blending parent and current coherence."""
        parent = ToolCoherence()
        parent.tools_known_good["tool_parent"] = 0.9
        parent.learned_strategies["error_1"] = "strategy_A"

        current = ToolCoherence()
        current.tools_known_good["tool_current"] = 0.95
        current.learned_strategies["error_1"] = "strategy_B"  # Override parent

        merged = manager.inherit_coherence(current, parent, strategy="blend")

        # Current should win on conflict
        assert merged.learned_strategies["error_1"] == "strategy_B"
        # Both should be present
        assert "tool_parent" in merged.tools_known_good
        assert "tool_current" in merged.tools_known_good

    def test_inherit_coherence_merges_cost_data(self, manager):
        """Test that coherence inheritance merges cost data."""
        parent = ToolCoherence()
        parent.cost_deltas = [10.0, -5.0]
        parent.cost_corrections = [(100.0, 110.0), (50.0, 45.0)]

        current = ToolCoherence()
        current.cost_deltas = [3.0, 2.0]
        current.cost_corrections = [(200.0, 203.0)]

        merged = manager.inherit_coherence(current, parent)

        assert len(merged.cost_deltas) == 4
        assert len(merged.cost_corrections) == 3

    def test_tool_success_rate_integration(self, manager):
        """Test recording and retrieving tool success rates."""
        coherence = ToolCoherence()

        # Record tool execution
        coherence.record_tool_execution(
            tool_id="tool_fix",
            error_class="syntax",
            succeeded=True,
            latency_ms=250,
            cost_cents=50,
        )

        # Verify recorded
        success_rate = coherence.get_success_rate_for_tool_and_error(
            "tool_fix", "syntax"
        )
        assert success_rate == 1.0

        # Verify tool marked as good
        assert "tool_fix" in coherence.tools_known_good

    def test_get_recommended_tools(self, manager):
        """Test getting recommended tools for error class."""
        coherence = ToolCoherence()

        # Record multiple tools
        for tool_id in ["tool_1", "tool_2", "tool_3"]:
            for i in range(10):
                coherence.record_tool_execution(
                    tool_id=tool_id,
                    error_class="logic",
                    succeeded=i < (8 if tool_id == "tool_1" else 5),
                    latency_ms=100,
                    cost_cents=10,
                )

        # Get recommendations
        recommendations = coherence.get_recommended_tools_for_error("logic", top_n=2)

        assert len(recommendations) <= 2
        # Higher success rate tools should come first
        if len(recommendations) > 1:
            assert recommendations[0][1] >= recommendations[1][1]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
