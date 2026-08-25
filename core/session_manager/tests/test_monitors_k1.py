"""Tests for Phase 2.2 Monitor Subsystems (k=1).

k=1: DataClasses + GoalAlignmentMonitor
- Base classes and datastructures
- GoalAlignmentMonitor: semantic similarity detection
- 10 unit tests
"""

import pytest
from datetime import datetime
from core.session_manager.monitors.base import (
    MonitorAlert,
    AlertType,
    MonitorConfig,
    MonitorState,
)
from core.session_manager.monitors.goal_alignment import (
    GoalAlignmentMonitor,
    GoalAlignmentState,
)


class MockHub:
    """Mock SubsystemHub for testing."""

    def __init__(self):
        self.published_events = []

    def subscribe(self, event_name, handler):
        pass

    def publish_event(self, event_name, event_data):
        self.published_events.append((event_name, event_data))


class TestMonitorAlert:
    """Test MonitorAlert dataclass."""

    def test_alert_creation(self):
        """Test basic alert creation."""
        alert = MonitorAlert(
            alert_type=AlertType.GOAL_DRIFT_DETECTED,
            session_id="sess-001",
            task_id="task-001",
            tenant_id="default",
            severity="warning",
            reason="Goal similarity dropped below threshold",
        )

        assert alert.alert_type == AlertType.GOAL_DRIFT_DETECTED
        assert alert.session_id == "sess-001"
        assert alert.severity == "warning"
        assert alert.event_id  # UUID generated

    def test_alert_to_audit_event(self):
        """Test audit event conversion."""
        alert = MonitorAlert(
            alert_type=AlertType.GOAL_DRIFT_DETECTED,
            session_id="sess-001",
            task_id="task-001",
            tenant_id="default",
            severity="warning",
            reason="Test reason",
        )

        audit_event = alert.to_audit_event()

        assert audit_event["event_type"] == "session.monitor.goal_drift_detected"
        assert audit_event["tenant_id"] == "default"
        assert audit_event["session_id"] == "sess-001"
        assert audit_event["severity"] == "warning"


class TestMonitorConfig:
    """Test MonitorConfig dataclass."""

    def test_default_config(self):
        """Test default configuration."""
        config = MonitorConfig()

        assert config.enabled is True
        assert config.check_interval_seconds == 5
        assert config.alert_cooldown_seconds == 60
        assert config.max_alerts_per_session == 100

    def test_custom_config(self):
        """Test custom configuration."""
        config = MonitorConfig(
            enabled=False,
            check_interval_seconds=10,
            alert_cooldown_seconds=30,
        )

        assert config.enabled is False
        assert config.check_interval_seconds == 10
        assert config.alert_cooldown_seconds == 30


class TestGoalAlignmentMonitor:
    """Test GoalAlignmentMonitor."""

    def setup_method(self):
        """Setup test fixtures."""
        self.hub = MockHub()
        self.monitor = GoalAlignmentMonitor()
        self.monitor.startup(self.hub)

    def teardown_method(self):
        """Cleanup."""
        self.monitor.shutdown()

    # ========================================================================
    # Test 1: Similarity Calculation
    # ========================================================================

    def test_similarity_identical_texts(self):
        """Test similarity of identical texts (should be 1.0)."""
        text = "analyze database schema and generate migration scripts"
        similarity = self.monitor._calculate_similarity(text, text)

        # Jaccard similarity of identical sets is 1.0
        assert similarity == 1.0

    def test_similarity_completely_different_texts(self):
        """Test similarity of completely different texts (should be ~0.0)."""
        text1 = "analyze database schema"
        text2 = "unrelated text about weather forecast"
        similarity = self.monitor._calculate_similarity(text1, text2)

        assert similarity < 0.1

    def test_similarity_partial_overlap(self):
        """Test similarity with partial word overlap."""
        text1 = "analyze database schema"
        text2 = "analyze migration scripts schema"
        similarity = self.monitor._calculate_similarity(text1, text2)

        # Both have "analyze" and "schema", so non-zero
        assert 0.3 < similarity < 0.7

    def test_similarity_case_insensitive(self):
        """Test similarity is case-insensitive."""
        text1 = "Analyze Database Schema"
        text2 = "analyze database schema"
        similarity = self.monitor._calculate_similarity(text1, text2)

        assert similarity == 1.0

    # ========================================================================
    # Test 2-3: Goal Alignment Detection
    # ========================================================================

    def test_set_goal(self):
        """Test setting original goal for a session."""
        goal = "analyze and document the entire database schema"
        self.monitor.set_goal("sess-001", "task-001", "default", goal)

        state = self.monitor.session_states["sess-001"]
        assert state.original_goal == goal

    def test_no_alert_on_high_similarity(self):
        """Test no alert when current work aligns with goal."""
        goal = "analyze database schema"
        self.monitor.set_goal("sess-001", "task-001", "default", goal)

        state = self.monitor.session_states["sess-001"]
        state.metadata["current_work"] = "analyze schema and database"

        alert = self.monitor.evaluate_session(state)

        # High similarity (>0.6), should not alert
        assert alert is None
        assert state.consecutive_low_scores == 0

    def test_single_low_similarity_no_alert(self):
        """Test single low similarity doesn't trigger alert.

        Alert only triggers after 3 consecutive low scores.
        """
        goal = "analyze database schema"
        self.monitor.set_goal("sess-001", "task-001", "default", goal)

        state = self.monitor.session_states["sess-001"]
        state.metadata["current_work"] = "completely different task"

        alert = self.monitor.evaluate_session(state)

        # Single low score, consecutive_low_count < 3
        assert alert is None
        assert state.consecutive_low_scores == 1

    def test_three_consecutive_low_similarities_alert(self):
        """Test alert triggers on 3 consecutive low similarities."""
        goal = "analyze database schema"
        self.monitor.set_goal("sess-001", "task-001", "default", goal)

        state = self.monitor.session_states["sess-001"]

        # First iteration: low similarity
        state.metadata["current_work"] = "write documentation about colors"
        alert1 = self.monitor.evaluate_session(state)
        assert alert1 is None
        assert state.consecutive_low_scores == 1

        # Second iteration: low similarity
        state.metadata["current_work"] = "implement UI components for dashboard"
        alert2 = self.monitor.evaluate_session(state)
        assert alert2 is None
        assert state.consecutive_low_scores == 2

        # Third iteration: low similarity → ALERT
        state.metadata["current_work"] = "discuss weather patterns and climate"
        alert3 = self.monitor.evaluate_session(state)

        assert alert3 is not None
        assert alert3.alert_type == AlertType.GOAL_DRIFT_DETECTED
        assert alert3.severity == "warning"
        # Counter resets after alert
        assert state.consecutive_low_scores == 0

    def test_reset_counter_on_high_similarity(self):
        """Test consecutive counter resets on high similarity."""
        goal = "analyze database schema"
        self.monitor.set_goal("sess-001", "task-001", "default", goal)

        state = self.monitor.session_states["sess-001"]

        # Low similarity iteration 1
        state.metadata["current_work"] = "unrelated text"
        self.monitor.evaluate_session(state)
        assert state.consecutive_low_scores == 1

        # Low similarity iteration 2
        state.metadata["current_work"] = "different topic"
        self.monitor.evaluate_session(state)
        assert state.consecutive_low_scores == 2

        # High similarity → reset counter
        state.metadata["current_work"] = "analyze database schema and create plans"
        alert = self.monitor.evaluate_session(state)
        assert alert is None
        assert state.consecutive_low_scores == 0

    def test_alert_published_to_hub(self):
        """Test alert is published to EventBus."""
        goal = "analyze database"
        self.monitor.set_goal("sess-001", "task-001", "default", goal)

        state = self.monitor.session_states["sess-001"]

        # Trigger 3 low scores
        for _ in range(3):
            state.metadata["current_work"] = "unrelated text"
            alert = self.monitor.evaluate_session(state)

        # Alert should have been published
        assert len(self.hub.published_events) == 1
        event_name, event_data = self.hub.published_events[0]
        assert event_name == "monitor.goal_drift_detected"
        assert event_data["event_type"] == "session.monitor.goal_drift_detected"

    def test_cooldown_prevents_repeated_alerts(self):
        """Test alert cooldown prevents spam."""
        goal = "analyze database"
        self.monitor.set_goal("sess-001", "task-001", "default", goal)
        self.monitor.config.alert_cooldown_seconds = 60  # 60 second cooldown

        state = self.monitor.session_states["sess-001"]

        # Trigger first alert
        for _ in range(3):
            state.metadata["current_work"] = "unrelated"
            alert = self.monitor.evaluate_session(state)

        first_alert_timestamp = state.last_alert_timestamp

        # Try to trigger again immediately
        for _ in range(3):
            state.metadata["current_work"] = "unrelated"
            alert = self.monitor.evaluate_session(state)
            # Should not alert due to cooldown
            assert alert is None

        # Timestamp should not have changed
        assert state.last_alert_timestamp == first_alert_timestamp
