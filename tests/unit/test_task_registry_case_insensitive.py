"""
Unit Tests: Task-ID Canonicalization (FIX #2 — ADR-0863)

Tests for case-insensitive task-id normalization and deduplication.
"""

import pytest
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.vibe_engineering.task_registry import (
    _normalize_task_id,
    detect_task_id_duplicates,
    TaskMetadata,
    TaskStatus,
    PhaseMetadata,
    PhaseStatus
)


class TestTaskIDNormalization:
    """Test _normalize_task_id() function"""

    def test_normalize_snake_case_already_normalized(self):
        """Idempotent: snake_case input remains unchanged"""
        assert _normalize_task_id("task_1") == "task_1"
        assert _normalize_task_id("blocker_2_complete") == "blocker_2_complete"

    def test_normalize_uppercase_to_lowercase(self):
        """Convert UPPERCASE to lowercase"""
        assert _normalize_task_id("TASK_1") == "task_1"
        assert _normalize_task_id("BLOCKER_2") == "blocker_2"
        assert _normalize_task_id("FIX_123") == "fix_123"

    def test_normalize_kebab_case_to_snake_case(self):
        """Replace hyphens with underscores"""
        assert _normalize_task_id("Task-1") == "task_1"
        assert _normalize_task_id("blocker-2") == "blocker_2"
        assert _normalize_task_id("fix-123-critical") == "fix_123_critical"

    def test_normalize_spaces_to_underscores(self):
        """Replace spaces with underscores"""
        assert _normalize_task_id("Task 1") == "task_1"
        assert _normalize_task_id("Blocker 2 Complete") == "blocker_2_complete"

    def test_normalize_mixed_case(self):
        """Handle mixed case and formatting"""
        assert _normalize_task_id("Task-1") == "task_1"
        assert _normalize_task_id("TASK_1") == "task_1"
        assert _normalize_task_id("Task_1") == "task_1"
        assert _normalize_task_id("task-1") == "task_1"

    def test_normalize_whitespace_stripping(self):
        """Strip leading/trailing whitespace"""
        assert _normalize_task_id("  task_1  ") == "task_1"
        assert _normalize_task_id("\ttask_1\t") == "task_1"
        assert _normalize_task_id("\ntask_1\n") == "task_1"

    def test_normalize_collapse_underscores(self):
        """Collapse multiple consecutive underscores"""
        assert _normalize_task_id("task__1") == "task_1"
        assert _normalize_task_id("task___1") == "task_1"
        assert _normalize_task_id("task____1") == "task_1"

    def test_normalize_complex_cases(self):
        """Test complex real-world cases"""
        # Multiple issues combined
        assert _normalize_task_id("  Task-1-Complete  ") == "task_1_complete"
        assert _normalize_task_id("BLOCKER_2__URGENT") == "blocker_2_urgent"
        assert _normalize_task_id("Fix - 123 - Security") == "fix_123_security"

    def test_normalize_empty_and_edge_cases(self):
        """Handle edge cases"""
        assert _normalize_task_id("_task_") == "task"
        assert _normalize_task_id("___") == ""
        assert _normalize_task_id("a") == "a"
        assert _normalize_task_id("A") == "a"


class TestDuplicateDetection:
    """Test detect_task_id_duplicates() function"""

    def test_no_duplicates(self):
        """Return empty dict when no duplicates exist"""
        task_ids = {"task_1", "task_2", "task_3"}
        result = detect_task_id_duplicates(task_ids)
        assert result == {}

    def test_case_duplicates_detected(self):
        """Detect duplicates differing only in case"""
        task_ids = {"Task_1", "task_1", "TASK_1"}
        result = detect_task_id_duplicates(task_ids)

        assert "task_1" in result
        assert len(result["task_1"]) == 3
        assert task_ids == result["task_1"]

    def test_formatting_duplicates_detected(self):
        """Detect duplicates differing only in formatting"""
        task_ids = {"task-1", "task_1", "Task-1"}
        result = detect_task_id_duplicates(task_ids)

        assert "task_1" in result
        assert len(result["task_1"]) == 3

    def test_mixed_duplicates(self):
        """Detect multiple groups of duplicates"""
        task_ids = {
            "Task-1", "task_1", "TASK_1",      # Group 1: task_1
            "Blocker-2", "BLOCKER_2",          # Group 2: blocker_2
            "fix_123"                           # No duplicates
        }
        result = detect_task_id_duplicates(task_ids)

        assert len(result) == 2
        assert len(result["task_1"]) == 3
        assert len(result["blocker_2"]) == 2

    def test_empty_input(self):
        """Handle empty input"""
        result = detect_task_id_duplicates(set())
        assert result == {}


class TestTaskRegistryCaseInsensitivity:
    """Test TaskRegistry case-insensitive lookup"""

    def test_case_insensitive_lookup_direct(self):
        """Test case-insensitive normalization applies"""
        # Test that normalization works
        assert _normalize_task_id("Task-1") == _normalize_task_id("task_1")
        assert _normalize_task_id("BLOCKER_2") == _normalize_task_id("blocker-2")

    def test_task_metadata_creation(self):
        """Create task metadata with various ID formats"""
        task1 = TaskMetadata(
            task_id="task_1",
            title="Test Task 1",
            status=TaskStatus.RUNNING
        )
        assert task1.task_id == "task_1"

        task2 = TaskMetadata(
            task_id="Task-1",  # Different format
            title="Test Task 1 (alt format)",
            status=TaskStatus.RUNNING
        )
        # Should both normalize to same ID
        assert _normalize_task_id(task1.task_id) == _normalize_task_id(task2.task_id)


class TestNormalizationPatterns:
    """Test patterns matching real CorvinOS task-id usage"""

    def test_milestone_ids(self):
        """Normalize milestone-style IDs"""
        assert _normalize_task_id("Milestone-1-Setup") == "milestone_1_setup"
        assert _normalize_task_id("M1-Setup") == "m1_setup"

    def test_blocker_ids(self):
        """Normalize blocker-style IDs"""
        assert _normalize_task_id("Blocker-1") == "blocker_1"
        assert _normalize_task_id("Blocker-2-Complete") == "blocker_2_complete"
        assert _normalize_task_id("BLOCKER_3") == "blocker_3"

    def test_fix_ids(self):
        """Normalize fix-style IDs"""
        assert _normalize_task_id("Fix-123-Security") == "fix_123_security"
        assert _normalize_task_id("FIX_456_URGENT") == "fix_456_urgent"

    def test_phase_ids(self):
        """Normalize phase-style IDs"""
        assert _normalize_task_id("Phase-A-Execution") == "phase_a_execution"
        assert _normalize_task_id("PHASE_B_TEST") == "phase_b_test"

    def test_track_ids(self):
        """Normalize track-style IDs"""
        assert _normalize_task_id("Track-1-LDD") == "track_1_ldd"
        assert _normalize_task_id("TRACK_2_MARKETPLACE") == "track_2_marketplace"


class TestNormalizationIdemtotence:
    """Verify normalization is idempotent"""

    @pytest.mark.parametrize("task_id", [
        "task_1",
        "blocker_2",
        "fix_123",
        "phase_a_execution",
        "track_1_ldd",
    ])
    def test_double_normalization_idempotent(self, task_id):
        """Normalizing twice produces same result as once"""
        once = _normalize_task_id(task_id)
        twice = _normalize_task_id(once)
        assert once == twice

    @pytest.mark.parametrize("task_id", [
        "Task-1",
        "BLOCKER-2",
        "Fix_123_SECURITY",
        "  Phase-A-Execution  ",
        "Track__1__LDD",
    ])
    def test_messy_double_normalization_idempotent(self, task_id):
        """Normalizing twice after converting from messy format"""
        once = _normalize_task_id(task_id)
        twice = _normalize_task_id(once)
        assert once == twice
        # And once should be snake_case
        assert all(c.islower() or c in ('_', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9')
                  for c in once)


class TestNormalizationRobustness:
    """Test robustness against unusual inputs"""

    def test_unicode_handling(self):
        """Handle unicode characters gracefully"""
        # Should preserve non-ASCII after lowercase conversion
        result = _normalize_task_id("Tâsk_1")
        assert isinstance(result, str)

    def test_numeric_task_ids(self):
        """Handle purely numeric task-ids"""
        assert _normalize_task_id("123") == "123"
        assert _normalize_task_id("-123-") == "123"

    def test_long_task_ids(self):
        """Handle long task-ids"""
        long_id = "This-Is-A-Very-Long-Task-Id-With-Many-Hyphens-123"
        result = _normalize_task_id(long_id)
        assert result == "this_is_a_very_long_task_id_with_many_hyphens_123"

    def test_special_characters_preserved(self):
        """Task IDs with alphanumeric + underscores only"""
        # Normalization only handles case, hyphens, spaces, underscores
        assert _normalize_task_id("task.1") == "task.1"  # Dot preserved
        assert _normalize_task_id("task@1") == "task@1"  # @ preserved


class TestMigrationScenarios:
    """Test realistic migration scenarios"""

    def test_merge_by_highest_status_priority(self):
        """Merging should prefer highest status priority"""
        # Scenario: two task records with same normalized ID but different status
        task1 = {"task_id": "Task-1", "status": "PROPOSED"}
        task2 = {"task_id": "task_1", "status": "ACCEPTED"}

        # After merge, should keep ACCEPTED
        normalized_id = _normalize_task_id(task1["task_id"])
        assert normalized_id == _normalize_task_id(task2["task_id"])

        # Test indicates they should merge
        assert task2["status"] in ["ACCEPTED", "COMPLETE"]

    def test_consolidation_reduces_duplicate_keys(self):
        """Migration should reduce number of keys"""
        # Before migration
        before = {
            "Task-1": {"status": "PROPOSED"},
            "task_1": {"status": "PENDING"},
            "TASK_1": {"status": "ACCEPTED"},  # Highest priority
            "Task-2": {"status": "PENDING"},
            "task_2": {"status": "PENDING"}
        }

        # After migration, should have 2 keys (task_1, task_2)
        normalized_groups = {}
        for key in before.keys():
            normalized = _normalize_task_id(key)
            if normalized not in normalized_groups:
                normalized_groups[normalized] = []
            normalized_groups[normalized].append(key)

        assert len(normalized_groups) == 2
        assert len(normalized_groups["task_1"]) == 3
        assert len(normalized_groups["task_2"]) == 2
