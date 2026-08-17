"""Tests for reachability proof (Phase 2)."""
import pytest
from pathlib import Path
from core.learning import e2e_for, ReachabilityMonitor, LearningEventStore
import tempfile


def test_e2e_decorator_marking():
    """Test @e2e_for decorator marks a test function."""
    @e2e_for("pattern_test")
    def test_something():
        pass
    
    assert hasattr(test_something, "_e2e_for")
    assert test_something._e2e_for == "pattern_test"


def test_reachability_monitor_initialization():
    """Test monitor can be created."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = LearningEventStore(Path(tmpdir))
        monitor = ReachabilityMonitor(store, Path("tests"))
        
        # Should have empty e2e_tests initially (no test files found)
        assert isinstance(monitor.e2e_tests, dict)


def test_reachability_check_coverage():
    """Test coverage checker detects missing tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = LearningEventStore(Path(tmpdir))
        
        # Register a pattern with no E2E test
        from core.learning import TreeNode
        pattern = TreeNode(
            id="pattern_no_test",
            level="pattern",
            name="Untested Pattern"
        )
        store.register_node(pattern)
        
        monitor = ReachabilityMonitor(store, Path("tests"))
        coverage = monitor.check_coverage()
        
        # Should report missing E2E and no production usage
        assert "No E2E test for pattern_no_test" in coverage["issues"]
        assert "Never used in production: pattern_no_test" in coverage["issues"]
