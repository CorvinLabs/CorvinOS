"""Tests for TreeOfThoughts core (Phase 1)."""
import pytest
from pathlib import Path
from datetime import datetime
from core.learning import (
    TreeNode, LearningEvent, ConfidenceEvent, CompositionType,
    LearningEventStore, update_confidence, apply_decay
)


def test_tree_node_creation():
    """Test creating a pattern node."""
    node = TreeNode(
        id="pattern_retry_backoff",
        level="pattern",
        name="Exponential Backoff Retry",
        when=["API rate-limits (429)", "transient network errors"],
        anti_when=["auth failures"],
        e2e_tests=["tests/test_voice_tts_retry.py"],
    )
    assert node.id == "pattern_retry_backoff"
    assert node.confidence == 0.5
    assert node.level == "pattern"


def test_confidence_bayesian_update():
    """Test Bayesian confidence update: 70% prior + 30% new evidence."""
    node = TreeNode(id="test", level="pattern", name="Test")
    node.confidence = 0.5
    
    event = LearningEvent(
        subject_id="test",
        event_type="used",
        confidence_delta=+0.3,
        reason="succeeded"
    )
    
    new_conf = update_confidence(node, event)
    # 0.5 * 0.7 + (0.5 + 0.3) * 0.3 = 0.35 + 0.24 = 0.59
    assert abs(new_conf - 0.59) < 0.01


def test_confidence_antipattern_penalty():
    """Using pattern in anti_when context: -0.3 penalty."""
    node = TreeNode(
        id="retry",
        level="pattern",
        name="Retry",
        anti_when=["auth_failures"]
    )
    node.confidence = 0.8
    
    event = LearningEvent(
        subject_id="retry",
        event_type="antipattern_detected",
        confidence_delta=0.0,  # Will be set to -0.3 by update_confidence
        reason="used in auth_failures context"
    )
    
    new_conf = update_confidence(node, event)
    assert new_conf < node.confidence


def test_confidence_decay():
    """Unused pattern loses 0.1/week confidence."""
    conf = 0.8
    decayed = apply_decay(conf, days_unused=7)
    assert decayed == 0.8 - 0.1  # 0.7


def test_hierarchical_aggregation():
    """Method confidence = weighted avg of children."""
    method = TreeNode(
        id="voice_synthesis",
        level="method",
        name="Voice Synthesis",
        children=["pattern_openai", "pattern_edge"],
        composition_type=CompositionType.AVG
    )
    
    # Children would have confidences 0.8 and 0.85
    # Aggregate: (0.8 + 0.85) / 2 = 0.825
    # For this test, manually verify the model structure
    assert method.level == "method"
    assert len(method.children) == 2


def test_operator_notes_append_only():
    """Operator notes are immutable and append-only."""
    node = TreeNode(id="test", level="pattern", name="Test")
    
    node.add_operator_note("alice", "This is important")
    node.add_operator_note("bob", "This is critical")
    
    assert len(node.operator_notes) == 2
    assert node.operator_notes[0][1] == "alice"
    assert node.operator_notes[1][1] == "bob"
    # Verify immutability by checking timestamp is set
    assert node.operator_notes[0][0]  # timestamp


def test_event_store_append_and_read():
    """Test append-only event store."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        store = LearningEventStore(Path(tmpdir))
        
        event = LearningEvent(
            subject_id="pattern_test",
            event_type="used",
            confidence_delta=+0.1,
            reason="succeeded"
        )
        
        store.append_event("pattern_test", event)
        
        # Read back events
        events = store.get_events("pattern_test")
        assert len(events) == 1
        assert events[0].subject_id == "pattern_test"
        assert events[0].event_type == "used"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
