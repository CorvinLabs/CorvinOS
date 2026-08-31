"""Test GoalContext: Persistent goal with SHA256 integrity (Phase 1).

Tests:
- Goal creation with hash computation
- Hash integrity verification
- Serialization/deserialization
- Audit event generation
"""

import pytest
import hashlib
from core.session_manager.goal_context import GoalContext


class TestGoalContextCreation:
    """Test GoalContext creation and validation."""

    def test_create_goal_context(self):
        """Test basic GoalContext creation."""
        goal = "Implement feature X with tests"
        ctx = GoalContext.create(goal)

        assert ctx.goal == goal
        assert len(ctx.goal_hash) == 64  # SHA256 hex digest length
        assert ctx.created_at.endswith("Z")

    def test_goal_hash_deterministic(self):
        """Test that same goal produces same hash."""
        goal = "Build a microservice"
        ctx1 = GoalContext.create(goal)
        ctx2 = GoalContext.create(goal)

        assert ctx1.goal_hash == ctx2.goal_hash

    def test_different_goals_different_hashes(self):
        """Test that different goals produce different hashes."""
        goal1 = "Build feature A"
        goal2 = "Build feature B"

        ctx1 = GoalContext.create(goal1)
        ctx2 = GoalContext.create(goal2)

        assert ctx1.goal_hash != ctx2.goal_hash

    def test_hash_computation_correct(self):
        """Test that hash matches SHA256 of goal."""
        goal = "Test goal"
        ctx = GoalContext.create(goal)
        expected_hash = hashlib.sha256(goal.encode("utf-8")).hexdigest()

        assert ctx.goal_hash == expected_hash

    def test_create_empty_goal_raises(self):
        """Test that empty goal raises ValueError."""
        with pytest.raises(ValueError, match="Goal cannot be empty"):
            GoalContext.create("")

    def test_create_whitespace_only_goal_raises(self):
        """Test that whitespace-only goal raises ValueError."""
        with pytest.raises(ValueError, match="Goal cannot be empty"):
            GoalContext.create("   ")

    def test_create_non_string_goal_raises(self):
        """Test that non-string goal raises ValueError."""
        with pytest.raises(ValueError, match="Goal must be a string"):
            GoalContext.create(123)  # type: ignore


class TestGoalContextIntegrity:
    """Test goal hash integrity verification."""

    def test_verify_integrity_success(self):
        """Test that valid hash passes integrity check."""
        goal = "Valid goal"
        ctx = GoalContext.create(goal)

        assert ctx.verify_integrity() is True

    def test_verify_integrity_fails_on_corrupted_goal(self):
        """Test that corrupted goal fails integrity check (fail-closed)."""
        goal = "Valid goal"
        ctx = GoalContext.create(goal)

        # Manually corrupt the goal (simulating data corruption)
        corrupted_ctx = GoalContext(
            goal="Corrupted goal",
            goal_hash=ctx.goal_hash,
            created_at=ctx.created_at,
        )

        with pytest.raises(AssertionError, match="Goal integrity check failed"):
            corrupted_ctx.verify_integrity()

    def test_verify_integrity_fails_on_corrupted_hash(self):
        """Test that corrupted hash fails integrity check."""
        goal = "Valid goal"
        ctx = GoalContext.create(goal)

        # Manually corrupt the hash
        corrupted_ctx = GoalContext(
            goal=ctx.goal,
            goal_hash="0" * 64,  # Invalid hash
            created_at=ctx.created_at,
        )

        with pytest.raises(AssertionError, match="Goal integrity check failed"):
            corrupted_ctx.verify_integrity()


class TestGoalContextSerialization:
    """Test serialization and deserialization."""

    def test_to_dict(self):
        """Test conversion to dict."""
        goal = "Test goal"
        ctx = GoalContext.create(goal)
        data = ctx.to_dict()

        assert data["goal"] == goal
        assert len(data["goal_hash"]) == 64
        assert data["created_at"].endswith("Z")

    def test_from_dict_success(self):
        """Test reconstruction from dict."""
        goal = "Test goal"
        ctx = GoalContext.create(goal)
        data = ctx.to_dict()

        restored_ctx = GoalContext.from_dict(data)

        assert restored_ctx.goal == ctx.goal
        assert restored_ctx.goal_hash == ctx.goal_hash
        assert restored_ctx.created_at == ctx.created_at

    def test_from_dict_missing_goal_raises(self):
        """Test that missing goal field raises ValueError."""
        data = {
            "goal_hash": "abc123",
            "created_at": "2026-08-30T00:00:00Z",
        }

        with pytest.raises(ValueError, match="goal field is required"):
            GoalContext.from_dict(data)

    def test_from_dict_missing_hash_raises(self):
        """Test that missing goal_hash field raises ValueError."""
        data = {
            "goal": "Test goal",
            "created_at": "2026-08-30T00:00:00Z",
        }

        with pytest.raises(ValueError, match="goal_hash field is required"):
            GoalContext.from_dict(data)

    def test_from_dict_missing_created_at_raises(self):
        """Test that missing created_at field raises ValueError."""
        data = {
            "goal": "Test goal",
            "goal_hash": "abc123",
        }

        with pytest.raises(ValueError, match="created_at field is required"):
            GoalContext.from_dict(data)

    def test_from_dict_corrupted_hash_raises(self):
        """Test that corrupted hash raises AssertionError on from_dict."""
        data = {
            "goal": "Test goal",
            "goal_hash": "0" * 64,  # Wrong hash
            "created_at": "2026-08-30T00:00:00Z",
        }

        with pytest.raises(AssertionError, match="Goal integrity check failed"):
            GoalContext.from_dict(data)

    def test_round_trip_serialization(self):
        """Test serialization and deserialization round-trip."""
        goals = [
            "Simple goal",
            "Complex goal with special chars: @#$%^&*()",
            "Multi-line\ngoal\ntext",
        ]

        for goal in goals:
            ctx = GoalContext.create(goal)
            data = ctx.to_dict()
            restored = GoalContext.from_dict(data)

            assert restored.goal == ctx.goal
            assert restored.goal_hash == ctx.goal_hash
            assert restored.created_at == ctx.created_at


class TestGoalContextAuditEvents:
    """Test audit event generation."""

    def test_to_audit_event(self):
        """Test conversion to audit event format."""
        goal = "Test goal"
        ctx = GoalContext.create(goal)
        event = ctx.to_audit_event()

        assert event["event_type"] == "goal_context.created"
        assert event["goal_hash"] == ctx.goal_hash
        assert event["created_at"].endswith("Z")
        # Ensure goal text is NOT in audit event (privacy/GDPR)
        assert "goal" not in event or event.get("goal") is None

    def test_audit_event_contains_no_pii(self):
        """Test that audit event contains no PII (goal text)."""
        goal = "Implement API for user data export per GDPR Art. 17"
        ctx = GoalContext.create(goal)
        event = ctx.to_audit_event()

        # Convert to string and verify no goal text appears
        event_str = str(event)
        assert "user data" not in event_str.lower()
        assert "GDPR" not in event_str
        # Only hash and metadata should be present
        assert "goal_hash" in event_str


class TestGoalContextImmutability:
    """Test that GoalContext is immutable."""

    def test_goal_context_is_frozen(self):
        """Test that GoalContext fields cannot be modified."""
        ctx = GoalContext.create("Test goal")

        with pytest.raises((AttributeError, TypeError)):
            ctx.goal = "Modified goal"  # type: ignore

        with pytest.raises((AttributeError, TypeError)):
            ctx.goal_hash = "modified_hash"  # type: ignore

        with pytest.raises((AttributeError, TypeError)):
            ctx.created_at = "modified_time"  # type: ignore
