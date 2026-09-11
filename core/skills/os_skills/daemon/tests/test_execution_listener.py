"""Tests for SkillExecutionListener."""

import pytest
from daemon.execution_listener import SkillExecutionListener, SkillExecutedEvent


def test_record_execution():
    """Test recording a skill execution."""
    listener = SkillExecutionListener()

    event = SkillExecutedEvent(
        skill_id="skill1",
        source_id="source1",
        duration_ms=100.0,
        success=True,
        outcome_quality=0.9,
    )

    listener.record_execution(event)
    assert len(listener.get_all_events()) == 1


def test_skill_stats():
    """Test tracking skill statistics."""
    listener = SkillExecutionListener()

    # Record multiple executions
    listener.record_execution(SkillExecutedEvent(
        skill_id="skill1",
        source_id="source1",
        duration_ms=100.0,
        success=True,
        outcome_quality=0.9,
    ))
    listener.record_execution(SkillExecutedEvent(
        skill_id="skill1",
        source_id="source1",
        duration_ms=150.0,
        success=False,
        error="timeout",
    ))

    stats = listener.get_skill_stats("skill1")
    assert stats is not None
    assert stats["exec_count"] == 2
    assert stats["error_count"] == 1
    assert stats["error_rate"] == 0.5
    assert 100 <= stats["avg_latency_ms"] <= 150


def test_event_validation():
    """Test validation of execution events."""
    # Valid event
    event = SkillExecutedEvent(
        skill_id="skill1",
        source_id="source1",
        duration_ms=50.0,
        success=True,
    )
    assert event.validate()

    # Invalid: missing skill_id
    event_invalid = SkillExecutedEvent(
        skill_id="",
        source_id="source1",
        duration_ms=50.0,
        success=True,
    )
    assert not event_invalid.validate()

    # Invalid: negative duration
    event_neg = SkillExecutedEvent(
        skill_id="skill1",
        source_id="source1",
        duration_ms=-10.0,
        success=True,
    )
    assert not event_neg.validate()

    # Invalid: quality outside [0, 1]
    event_bad_quality = SkillExecutedEvent(
        skill_id="skill1",
        source_id="source1",
        duration_ms=50.0,
        success=True,
        outcome_quality=1.5,
    )
    assert not event_bad_quality.validate()


def test_get_events_for_skill():
    """Test filtering events by skill."""
    listener = SkillExecutionListener()

    listener.record_execution(SkillExecutedEvent(skill_id="s1", source_id="src1", success=True))
    listener.record_execution(SkillExecutedEvent(skill_id="s2", source_id="src1", success=True))
    listener.record_execution(SkillExecutedEvent(skill_id="s1", source_id="src1", success=True))

    s1_events = listener.get_events_for_skill("s1")
    assert len(s1_events) == 2
    assert all(e.skill_id == "s1" for e in s1_events)


def test_reset():
    """Test resetting listener."""
    listener = SkillExecutionListener()
    listener.record_execution(SkillExecutedEvent(skill_id="s1", source_id="src1", success=True))

    listener.reset()
    assert len(listener.get_all_events()) == 0
    assert listener.skill_stats == {}
