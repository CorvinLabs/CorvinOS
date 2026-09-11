"""Tests for RegenerationScheduler."""

import pytest
from daemon.regeneration_scheduler import RegenerationScheduler, RegenerationQueueItem


def test_should_regenerate():
    """Test determining if regen is needed."""
    scheduler = RegenerationScheduler(loss_delta_threshold=0.05)

    # Loss improved by 0.1 (exceeds 0.05 threshold)
    assert scheduler.should_regenerate("skill1", -0.1)

    # Loss improved by 0.02 (below threshold)
    assert not scheduler.should_regenerate("skill1", -0.02)


def test_queue_regeneration():
    """Test queuing a skill for regen."""
    scheduler = RegenerationScheduler()

    item = scheduler.queue_regeneration(
        "skill1",
        reason="weight_change",
        priority=0.8,
        loss_delta=-0.1,
    )

    assert item is not None
    assert item.skill_id == "skill1"
    assert scheduler.is_queued("skill1")


def test_duplicate_queueing():
    """Test that duplicate queue attempts are rejected."""
    scheduler = RegenerationScheduler()

    item1 = scheduler.queue_regeneration("skill1", "weight_change")
    item2 = scheduler.queue_regeneration("skill1", "weight_change")

    assert item1 is not None
    assert item2 is None  # Duplicate


def test_get_next_batch():
    """Test getting next batch of skills to regen."""
    scheduler = RegenerationScheduler(batch_size=2)

    scheduler.queue_regeneration("s1", "weight_change", priority=0.5)
    scheduler.queue_regeneration("s2", "weight_change", priority=0.9)
    scheduler.queue_regeneration("s3", "weight_change", priority=0.3)

    batch = scheduler.get_next_batch()
    assert len(batch) == 2
    # Highest priority first
    assert batch[0].skill_id == "s2"
    assert batch[1].skill_id == "s1"

    # Queue should now be empty (batch removed)
    assert len(scheduler.queue) == 1


def test_mark_completed():
    """Test marking regen as completed."""
    scheduler = RegenerationScheduler()

    scheduler.queue_regeneration("s1", "weight_change")
    batch = scheduler.get_next_batch()

    scheduler.mark_completed("s1")
    assert "s1" in scheduler.skill_last_regen


def test_get_queue_status():
    """Test getting queue status."""
    scheduler = RegenerationScheduler()

    scheduler.queue_regeneration("s1", "weight_change", priority=0.8)
    scheduler.queue_regeneration("s2", "weight_change", priority=0.5)

    status = scheduler.get_queue_status()
    assert status["queued_count"] == 2
    assert status["processed_count"] == 0


def test_get_skills_waiting():
    """Test getting list of waiting skills."""
    scheduler = RegenerationScheduler()

    scheduler.queue_regeneration("s1", "weight_change")
    scheduler.queue_regeneration("s2", "weight_change")

    waiting = scheduler.get_skills_waiting()
    assert set(waiting) == {"s1", "s2"}


def test_reset():
    """Test resetting scheduler."""
    scheduler = RegenerationScheduler()

    scheduler.queue_regeneration("s1", "weight_change")
    scheduler.reset()

    assert len(scheduler.queue) == 0
    assert len(scheduler.processed) == 0
