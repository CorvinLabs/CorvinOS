"""Tests for DaemonIntegration."""

import pytest
from daemon.integration import DaemonIntegration, DaemonState
from daemon.execution_listener import SkillExecutedEvent
from daemon.feedback_collector import FeedbackEvent
from daemon.change_detector import SourceChangedEvent
import tempfile
import json


def test_daemon_bootstrap():
    """Test daemon bootstrap."""
    daemon = DaemonIntegration()
    daemon.bootstrap()

    assert daemon.is_running == False  # Not started yet


def test_daemon_start_stop():
    """Test daemon lifecycle."""
    daemon = DaemonIntegration()

    daemon.start()
    assert daemon.is_running == True

    daemon.stop()
    assert daemon.is_running == False


def test_on_source_changed():
    """Test handling source change events."""
    daemon = DaemonIntegration()
    daemon.start()

    event = SourceChangedEvent(
        source_id="source1",
        change_type="added",
        new_hash="abc123",
    )

    daemon.on_source_changed(event)
    assert "source1" in daemon.graph.nodes


def test_on_skill_executed():
    """Test handling skill execution events."""
    daemon = DaemonIntegration()
    daemon.start()

    event = SkillExecutedEvent(
        skill_id="skill1",
        source_id="source1",
        success=True,
        outcome_quality=0.85,
    )

    daemon.on_skill_executed(event)
    assert "skill1" in daemon.graph.nodes


def test_on_feedback():
    """Test handling feedback events."""
    daemon = DaemonIntegration()
    daemon.start()

    # First, create a skill
    exec_event = SkillExecutedEvent(skill_id="skill1", source_id="source1", success=True)
    daemon.on_skill_executed(exec_event)

    # Then give feedback
    feedback_event = FeedbackEvent(
        skill_id="skill1",
        rating=5,
        signal_type="positive",
    )

    daemon.on_feedback(feedback_event)

    # Weight should be initialized
    assert daemon.learner.get_weight("skill1") is not None


def test_get_daemon_state():
    """Test getting daemon state snapshot."""
    daemon = DaemonIntegration()
    daemon.start()

    # Add some state
    daemon.learner.initialize_weight("w1", 0.6)
    daemon.learner.update_weight("w1", 0.8, 0.7, 0.1)

    state = daemon.get_daemon_state()
    assert state.weights["w1"] is not None
    assert state.weight_updates_count > 0


def test_checkpoint_save_load():
    """Test saving and loading checkpoints."""
    with tempfile.TemporaryDirectory() as tmpdir:
        daemon = DaemonIntegration(corvin_home=tmpdir)
        daemon.start()

        # Add some state
        daemon.learner.initialize_weight("w1", 0.6)
        daemon.learner.update_weight("w1", 0.8, 0.7, 0.1)
        daemon.tick_count = 100

        # Save checkpoint
        daemon._save_checkpoint()

        # Create new daemon and load
        daemon2 = DaemonIntegration(corvin_home=tmpdir)
        latest = daemon2._find_latest_checkpoint()
        assert latest is not None

        # Load should succeed
        assert daemon2._load_checkpoint(latest)


def test_checkpoint_checksum_validation():
    """Test that corrupted checkpoints are rejected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        daemon = DaemonIntegration(corvin_home=tmpdir)
        daemon.start()

        daemon.learner.initialize_weight("w1", 0.6)
        daemon._save_checkpoint()

        # Load and corrupt the checkpoint
        latest = daemon._find_latest_checkpoint()
        with open(latest, "r") as f:
            data = json.load(f)

        # Corrupt the checksum
        data["checksum"] = "corrupted_checksum"
        with open(latest, "w") as f:
            json.dump(data, f)

        # Load should fail
        daemon2 = DaemonIntegration(corvin_home=tmpdir)
        assert not daemon2._load_checkpoint(latest)


def test_process_event_tick():
    """Test processing an event loop tick."""
    daemon = DaemonIntegration()
    daemon.start()

    result = daemon.process_event_tick()
    assert result["status"] != "not_running"
    assert "timestamp" in result


def test_checkpoint_on_tick_interval():
    """Test that checkpoints are saved periodically."""
    with tempfile.TemporaryDirectory() as tmpdir:
        daemon = DaemonIntegration(corvin_home=tmpdir)
        daemon.start()

        # Process 61 ticks (should trigger checkpoint at tick 60)
        for _ in range(61):
            daemon.process_event_tick()

        # Should have created a checkpoint
        latest = daemon._find_latest_checkpoint()
        assert latest is not None


def test_reset():
    """Test resetting daemon."""
    daemon = DaemonIntegration()
    daemon.start()

    daemon.learner.initialize_weight("w1", 0.5)
    daemon.detector.detect_changes({"s1": "content"})

    daemon.reset()
    assert len(daemon.learner.get_all_weights()) == 0
    assert len(daemon.detector.get_all_events()) == 0
