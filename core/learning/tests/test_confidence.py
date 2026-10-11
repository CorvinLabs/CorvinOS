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






