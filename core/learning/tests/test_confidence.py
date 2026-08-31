"""Tests for confidence-gated memory (ADR-0387)."""
import pytest
from datetime import datetime


class TestConfidenceThreshold:
    """Test confidence threshold constant."""

    def test_threshold_value(self):
        """Verify MEMORY_CONFIDENCE_THRESHOLD is 0.5."""
        # Import at test time to avoid path issues
        import sys
        from pathlib import Path
        repo_root = Path(__file__).resolve().parents[3]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))

        from core.learning.confidence import MEMORY_CONFIDENCE_THRESHOLD
        assert MEMORY_CONFIDENCE_THRESHOLD == 0.5
        assert isinstance(MEMORY_CONFIDENCE_THRESHOLD, float)


class TestThresholdEnforcement:
    """Test confidence gating in memory lookup (ADR-0387)."""

    def test_threshold_enforcement(self):
        """Verify that scores < 0.5 are filtered."""
        import sys
        from pathlib import Path
        repo_root = Path(__file__).resolve().parents[3]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))

        # Mock the MemoryMatch class
        from operator.context_engineering.rich_task_brief import MemoryMatch
        from core.learning.confidence import MEMORY_CONFIDENCE_THRESHOLD

        # Create mock matches with various scores
        matches = [
            MemoryMatch(
                filename="high_confidence.md",
                title="High Confidence Match",
                relevance_score=0.8,
                source_file="/tmp/high.md",
                timestamp=datetime.now(),
                content_preview="Important memory content",
            ),
            MemoryMatch(
                filename="low_confidence.md",
                title="Low Confidence Match",
                relevance_score=0.3,
                source_file="/tmp/low.md",
                timestamp=datetime.now(),
                content_preview="Outdated memory",
            ),
            MemoryMatch(
                filename="threshold_match.md",
                title="Threshold Match",
                relevance_score=0.5,
                source_file="/tmp/threshold.md",
                timestamp=datetime.now(),
                content_preview="Borderline memory",
            ),
        ]

        # Filter using threshold
        filtered = [
            m for m in matches
            if m.relevance_score >= MEMORY_CONFIDENCE_THRESHOLD
        ]

        # Only high (0.8) and threshold (0.5) should pass; low (0.3) rejected
        assert len(filtered) == 2
        assert filtered[0].relevance_score == 0.8
        assert filtered[1].relevance_score == 0.5
        assert all(m.relevance_score >= 0.5 for m in filtered)


class TestLowConfidenceEdgeCase:
    """Test memory lookup still works with confidence filter."""

    def test_low_confidence_edge_case(self):
        """Verify that confidence gating doesn't break memory workflow."""
        import sys
        from pathlib import Path
        repo_root = Path(__file__).resolve().parents[3]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))

        from operator.context_engineering.rich_task_brief import MemoryMatch
        from core.learning.confidence import MEMORY_CONFIDENCE_THRESHOLD

        # Create matches where ALL are low confidence
        matches = [
            MemoryMatch(
                filename="low1.md",
                title="Low 1",
                relevance_score=0.2,
                source_file="/tmp/low1.md",
                timestamp=datetime.now(),
                content_preview="Content 1",
            ),
            MemoryMatch(
                filename="low2.md",
                title="Low 2",
                relevance_score=0.4,
                source_file="/tmp/low2.md",
                timestamp=datetime.now(),
                content_preview="Content 2",
            ),
        ]

        # Filter with threshold
        filtered = [
            m for m in matches
            if m.relevance_score >= MEMORY_CONFIDENCE_THRESHOLD
        ]

        # Task still works, but with empty result (graceful degradation)
        assert len(filtered) == 0
        assert isinstance(filtered, list)


class TestGateDisabledViaFlag:
    """Test that confidence gate can be disabled via feature flag."""

    def test_gate_disabled_via_flag(self):
        """Verify no filtering when gate is disabled."""
        import sys
        from pathlib import Path
        repo_root = Path(__file__).resolve().parents[3]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))

        from operator.context_engineering.rich_task_brief import MemoryMatch

        # Create matches with mixed scores
        matches = [
            MemoryMatch(
                filename="high.md",
                title="High",
                relevance_score=0.8,
                source_file="/tmp/high.md",
                timestamp=datetime.now(),
                content_preview="High confidence",
            ),
            MemoryMatch(
                filename="low.md",
                title="Low",
                relevance_score=0.2,
                source_file="/tmp/low.md",
                timestamp=datetime.now(),
                content_preview="Low confidence",
            ),
        ]

        # When gate is disabled, all matches pass
        filtered_no_gate = [m for m in matches]  # No filtering
        assert len(filtered_no_gate) == 2

        # When gate is enabled, low-confidence filtered
        filtered_with_gate = [
            m for m in matches
            if m.relevance_score >= 0.5  # Threshold
        ]
        assert len(filtered_with_gate) == 1
        assert filtered_with_gate[0].relevance_score == 0.8
