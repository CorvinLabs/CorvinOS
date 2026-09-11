"""Tests for DataSourceChangeDetector."""

import pytest
from daemon.change_detector import DataSourceChangeDetector, SourceChangedEvent


def test_detect_added_source():
    """Test detection of a new source."""
    detector = DataSourceChangeDetector()

    # First detection (empty)
    events = detector.detect_changes({
        "memory:tier2": "content1",
        "rag": "content2",
    })
    assert len(events) == 2
    assert any(e.source_id == "memory:tier2" and e.change_type == "added" for e in events)
    assert any(e.source_id == "rag" and e.change_type == "added" for e in events)


def test_detect_updated_source():
    """Test detection of a source update."""
    detector = DataSourceChangeDetector()

    # First detection
    detector.detect_changes({"source1": "content_v1"})

    # Second detection with updated content
    events = detector.detect_changes({"source1": "content_v2"})
    assert len(events) == 1
    assert events[0].change_type == "updated"
    assert events[0].source_id == "source1"


def test_detect_deleted_source():
    """Test detection of a deleted source."""
    detector = DataSourceChangeDetector()

    # First detection
    detector.detect_changes({"source1": "content", "source2": "content"})

    # Second detection with one removed
    events = detector.detect_changes({"source1": "content"})
    assert len(events) == 1
    assert events[0].change_type == "deleted"
    assert events[0].source_id == "source2"


def test_no_changes():
    """Test that identical sources produce no events."""
    detector = DataSourceChangeDetector()

    detector.detect_changes({"source1": "same"})
    events = detector.detect_changes({"source1": "same"})
    assert len(events) == 0


def test_event_validation():
    """Test event validation."""
    event = SourceChangedEvent(
        source_id="source1",
        change_type="added",
        new_hash="abc123"
    )
    assert event.validate()

    # Invalid: missing new_hash for added
    event_invalid = SourceChangedEvent(
        source_id="source1",
        change_type="added",
        new_hash=None
    )
    assert not event_invalid.validate()


def test_get_all_events():
    """Test retrieving all detected events."""
    detector = DataSourceChangeDetector()

    detector.detect_changes({"s1": "c1"})
    detector.detect_changes({"s1": "c2", "s2": "c3"})

    all_events = detector.get_all_events()
    assert len(all_events) >= 2


def test_reset():
    """Test resetting detector state."""
    detector = DataSourceChangeDetector()
    detector.detect_changes({"s1": "c1"})

    detector.reset()
    assert detector.seen_sources == {}
    assert detector.events == []
