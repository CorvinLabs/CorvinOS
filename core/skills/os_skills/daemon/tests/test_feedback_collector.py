"""Tests for FeedbackCollector."""

import pytest
from daemon.feedback_collector import FeedbackCollector, FeedbackEvent


def test_record_feedback():
    """Test recording feedback."""
    collector = FeedbackCollector()

    event = FeedbackEvent(
        skill_id="skill1",
        rating=5,
        signal_type="positive",
    )

    collector.record_feedback(event)
    assert len(collector.get_all_events()) == 1


def test_create_feedback_event():
    """Test convenience method for creating feedback."""
    collector = FeedbackCollector()

    event = collector.create_feedback_event("skill1", rating=5)
    assert event.signal_type == "positive"
    assert event.rating == 5

    event2 = collector.create_feedback_event("skill1", rating=1)
    assert event2.signal_type == "negative"

    event3 = collector.create_feedback_event("skill1", rating=3)
    assert event3.signal_type == "neutral"


def test_event_validation():
    """Test validation of feedback events."""
    # Valid
    event = FeedbackEvent(skill_id="skill1", rating=3, signal_type="neutral")
    assert event.validate()

    # Invalid: missing skill_id
    event_invalid = FeedbackEvent(skill_id="", rating=3, signal_type="neutral")
    assert not event_invalid.validate()

    # Invalid: rating outside [1, 5]
    event_bad_rating = FeedbackEvent(skill_id="skill1", rating=6, signal_type="neutral")
    assert not event_bad_rating.validate()

    # Invalid: bad signal type
    event_bad_signal = FeedbackEvent(skill_id="skill1", rating=3, signal_type="unknown")
    assert not event_bad_signal.validate()


def test_signal_distribution():
    """Test tracking signal distribution."""
    collector = FeedbackCollector()

    collector.create_feedback_event("s1", 5)  # positive
    collector.create_feedback_event("s1", 4)  # neutral
    collector.create_feedback_event("s1", 4)  # neutral
    collector.create_feedback_event("s1", 1)  # negative

    dist = collector.get_signal_distribution()
    assert dist["positive"] == 0.25
    assert dist["neutral"] == 0.5
    assert dist["negative"] == 0.25


def test_inverse_prevalence_weights():
    """Test inverse-prevalence weighting (rare signals matter more)."""
    collector = FeedbackCollector()

    # Create imbalanced feedback
    for _ in range(10):
        collector.create_feedback_event("s1", 3)  # neutral (70%)
    for _ in range(3):
        collector.create_feedback_event("s1", 5)  # positive (20%)
    for _ in range(1):
        collector.create_feedback_event("s1", 1)  # negative (7%)

    weights = collector.compute_inverse_prevalence_weights()

    # Rare signals should have higher weights
    assert weights["negative"] > weights["neutral"]
    assert weights["positive"] > weights["neutral"]

    # Weights should sum to 1
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_average_rating():
    """Test computing average rating."""
    collector = FeedbackCollector()

    collector.create_feedback_event("skill1", 5)
    collector.create_feedback_event("skill1", 3)
    collector.create_feedback_event("skill1", 4)

    avg = collector.get_average_rating("skill1")
    assert avg == pytest.approx(4.0, rel=1e-6)


def test_skill_summary():
    """Test generating skill feedback summary."""
    collector = FeedbackCollector()

    collector.create_feedback_event("skill1", 5)
    collector.create_feedback_event("skill1", 1)
    collector.create_feedback_event("skill1", 3)

    summary = collector.get_skill_summary("skill1")
    assert summary is not None
    assert summary["feedback_count"] == 3
    assert summary["average_rating"] == pytest.approx(3.0)
    assert summary["signal_counts"]["positive"] == 1
    assert summary["signal_counts"]["negative"] == 1
    assert summary["signal_counts"]["neutral"] == 1


def test_get_feedback_for_skill():
    """Test filtering feedback by skill."""
    collector = FeedbackCollector()

    collector.create_feedback_event("s1", 5)
    collector.create_feedback_event("s2", 3)
    collector.create_feedback_event("s1", 2)

    s1_feedback = collector.get_feedback_for_skill("s1")
    assert len(s1_feedback) == 2
    assert all(e.skill_id == "s1" for e in s1_feedback)


def test_audit_safe_dict():
    """Test that to_audit_safe_dict omits comments."""
    event = FeedbackEvent(
        skill_id="skill1",
        rating=5,
        signal_type="positive",
        comment="This is a secret comment",
    )

    safe_dict = event.to_audit_safe_dict()
    assert "comment" not in safe_dict
    assert safe_dict["skill_id"] == "skill1"
    assert safe_dict["rating"] == 5


def test_reset():
    """Test resetting collector."""
    collector = FeedbackCollector()
    collector.create_feedback_event("s1", 5)

    collector.reset()
    assert len(collector.get_all_events()) == 0
    assert collector.skill_feedback == {}
    assert collector.signal_distribution == {"positive": 0, "negative": 0, "neutral": 0}
