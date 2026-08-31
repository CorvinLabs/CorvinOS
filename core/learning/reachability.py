"""Reachability monitor: verify patterns have E2E + production usage (Phase 2)."""
from __future__ import annotations
from .storage import LearningEventStore
from .models import TreeNode
from pathlib import Path
import inspect


class ReachabilityMonitor:
    """Track E2E tests and production usage per pattern."""
    
    def __init__(self, store: LearningEventStore, test_dir: Path = None):
        self.store = store
        self.test_dir = test_dir or Path("tests")
        self.e2e_tests: dict[str, list[str]] = {}  # pattern_id → test files
        self.scan_tests()
    
    def scan_tests(self) -> None:
        """Find all @e2e_for(...) decorated tests."""
        import sys
        import importlib.util
        
        for test_file in sorted(self.test_dir.glob("test_*.py")):
            try:
                spec = importlib.util.spec_from_file_location(test_file.stem, test_file)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                
                for name, obj in inspect.getmembers(module, inspect.isfunction):
                    if hasattr(obj, "_e2e_for"):
                        pattern_id = obj._e2e_for
                        if pattern_id not in self.e2e_tests:
                            self.e2e_tests[pattern_id] = []
                        self.e2e_tests[pattern_id].append(str(test_file))
            except Exception:
                # Silently skip unparseable test files
                pass
    
    def check_coverage(self) -> dict[str, list[str]]:
        """Verify every pattern has E2E test + production usage.
        
        Returns: {issues: [list of missing items]}
        """
        issues = []
        
        for pattern in self.store.all_nodes():
            if pattern.level != "pattern":
                continue
            
            e2e_exists = pattern.id in self.e2e_tests
            prod_used = pattern.calls_in_production > 0
            
            if not e2e_exists:
                issues.append(f"No E2E test for {pattern.id}")
            if not prod_used:
                issues.append(f"Never used in production: {pattern.id}")
        
        return {"issues": issues}
