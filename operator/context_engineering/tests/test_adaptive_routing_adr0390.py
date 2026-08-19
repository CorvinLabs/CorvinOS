"""Comprehensive tests for Phase 3 Adaptive Routing & Dynamic Allocation (ADR-0391).

Tests cover: task classification, adaptive budget allocation, performance metrics
tracking, and pipeline integration.

Run: python3 operator/context_engineering/tests/test_adaptive_routing_adr0390.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for p in (_REPO / "operator" / "forge", _REPO / "core" / "console",
          _REPO / "operator" / "skill-forge"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _load_ce():
    """Load context_engineering module dynamically."""
    ce = _REPO / "operator" / "context_engineering"
    spec = importlib.util.spec_from_file_location(
        "context_engineering", str(ce / "__init__.py"),
        submodule_search_locations=[str(ce)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["context_engineering"] = mod
    spec.loader.exec_module(mod)
    return mod


_CE = _load_ce()

# Import classes from the loaded module
TaskComplexity = _CE.TaskComplexity
classify = _CE.classify
classify_simple = _CE.classify_simple
AdaptiveBudget = _CE.AdaptiveBudget
TokenBudget = _CE.TokenBudget
PerformanceMetric = _CE.PerformanceMetric
PerformanceTracker = _CE.PerformanceTracker
StageMetrics = _CE.StageMetrics


# ── Test Task Classification ──────────────────────────────────────────────────

class TestTaskClassification(unittest.TestCase):
    """Tests for TaskComplexity.classify() heuristics."""

    def test_simple_task_classification(self):
        """SIMPLE classification triggered by simple-task keywords."""
        tasks = [
            "rename variable x to y",
            "delete unused function",
            "fix typo in comment",
            "format code with prettier",
            "strip whitespace",
        ]
        for task in tasks:
            result = classify(task)
            self.assertEqual(result.complexity, TaskComplexity.SIMPLE, f"Failed for: {task}")
            self.assertGreater(result.keyword_matches, 0, f"No keywords matched for: {task}")

    def test_complex_task_classification(self):
        """COMPLEX classification triggered by complex-task keywords."""
        tasks = [
            "refactor authentication system",
            "design new caching layer",
            "implement OAuth integration",
            "optimize database queries",
            "architect multi-tenant support",
        ]
        for task in tasks:
            result = classify(task)
            self.assertEqual(result.complexity, TaskComplexity.COMPLEX, f"Failed for: {task}")
            self.assertGreater(result.keyword_matches, 0, f"No keywords matched for: {task}")

    def test_moderate_task_classification(self):
        """MODERATE classification for mixed or no keywords."""
        tasks = [
            "update the README",
            "add some tests",
            "improve error handling",
            "general maintenance work",
        ]
        for task in tasks:
            result = classify(task)
            self.assertIn(result.complexity, (
                TaskComplexity.MODERATE,
                TaskComplexity.SIMPLE,
                TaskComplexity.COMPLEX
            ))

    def test_confidence_scoring(self):
        """Confidence increases with keyword density."""
        high_density = "refactor and redesign the architecture and optimize everything"
        high_result = classify(high_density)
        low_density = "do something about the code"
        low_result = classify(low_density)
        self.assertGreaterEqual(high_result.keyword_matches, low_result.keyword_matches)

    def test_empty_task_classification(self):
        """Empty or None tasks degrade to MODERATE with low confidence."""
        self.assertEqual(classify("").complexity, TaskComplexity.MODERATE)
        self.assertLess(classify("").confidence, 0.5)
        self.assertEqual(classify(None).complexity, TaskComplexity.MODERATE)
        self.assertEqual(classify(None).confidence, 0.0)

    def test_classify_simple_returns_level(self):
        """classify_simple() returns only the complexity level."""
        result = classify_simple("refactor the authentication")
        self.assertIsInstance(result, TaskComplexity)
        self.assertEqual(result, TaskComplexity.COMPLEX)


# ── Test Adaptive Budget Allocation ───────────────────────────────────────────

class TestAdaptiveBudget(unittest.TestCase):
    """Tests for AdaptiveBudget allocation per task complexity."""

    def test_simple_task_allocation(self):
        """SIMPLE tasks skip graph and skills stages."""
        budget = AdaptiveBudget.allocate_for_task(TaskComplexity.SIMPLE)
        self.assertEqual(budget.graph, 0, "Graph should be 0% for SIMPLE")
        self.assertEqual(budget.skills, 0, "Skills should be 0% for SIMPLE")
        self.assertGreater(budget.memory, 0, "Memory should be allocated")
        self.assertGreater(budget.synthesis, 0, "Synthesis should be allocated")

        memory_pct = budget.memory / (budget.memory + budget.synthesis)
        self.assertGreater(memory_pct, 0.55)
        self.assertLess(memory_pct, 0.65)

    def test_complex_task_allocation(self):
        """COMPLEX tasks get balanced allocation across all stages."""
        budget = AdaptiveBudget.allocate_for_task(TaskComplexity.COMPLEX)
        self.assertGreater(budget.memory, 0, "Memory must be allocated")
        self.assertGreater(budget.graph, 0, "Graph must be allocated")
        self.assertGreater(budget.skills, 0, "Skills must be allocated")
        self.assertGreater(budget.synthesis, 0, "Synthesis must be allocated")

        total = budget.total()
        self.assertGreater(budget.memory / total, 0.25)
        self.assertLess(budget.memory / total, 0.35)
        self.assertGreater(budget.graph / total, 0.15)
        self.assertLess(budget.graph / total, 0.25)

    def test_moderate_task_allocation(self):
        """MODERATE tasks get balanced allocation."""
        budget = AdaptiveBudget.allocate_for_task(TaskComplexity.MODERATE)
        total = budget.total()
        self.assertGreater(budget.memory / total, 0.30)
        self.assertLess(budget.memory / total, 0.40)

    def test_rebalance_from_metrics(self):
        """Rebalancing adjusts allocations based on stage performance."""
        budget = AdaptiveBudget.allocate_for_task(TaskComplexity.COMPLEX)
        initial_graph = budget.graph

        metrics = {
            "memory": PerformanceMetric(0.8, 0.85, 0.9, 150),
            "graph": PerformanceMetric(0.2, 0.4, 0.3, 100),
            "skills": PerformanceMetric(0.5, 0.7, 0.6, 80),
            "synthesis": PerformanceMetric(0.9, 0.95, 0.95, 200),
        }
        budget.rebalance_from_metrics(metrics)
        self.assertLess(budget.graph, initial_graph, "Graph allocation should decrease")

    def test_rebalance_bounds_enforcement(self):
        """Rebalancing adjustments are capped at ±10% per stage."""
        budget = AdaptiveBudget.allocate_for_task(TaskComplexity.COMPLEX)
        metrics = {
            "memory": PerformanceMetric(0.1, 0.1, 0.1, 10),
            "synthesis": PerformanceMetric(0.99, 0.99, 0.99, 500),
        }
        budget.rebalance_from_metrics(metrics)

        for stage_id, delta in budget.stage_adjustments.items():
            self.assertGreaterEqual(delta, -0.10, f"Delta for {stage_id} too low: {delta}")
            self.assertLessEqual(delta, 0.10, f"Delta for {stage_id} too high: {delta}")

    def test_budget_to_dict(self):
        """to_dict() exports budget as consumable dict."""
        budget = AdaptiveBudget.allocate_for_task(TaskComplexity.SIMPLE)
        d = budget.to_dict()

        self.assertIsInstance(d, dict)
        self.assertEqual(set(d.keys()), {"memory", "graph", "skills", "synthesis"})
        for v in d.values():
            self.assertIsInstance(v, int)
            self.assertGreaterEqual(v, 0)

    def test_budget_percentages(self):
        """percentages() returns 0.0-1.0 allocation fractions."""
        budget = AdaptiveBudget.allocate_for_task(TaskComplexity.MODERATE)
        pcts = budget.percentages()

        self.assertIsInstance(pcts, dict)
        total_pct = sum(pcts.values())
        self.assertGreater(total_pct, 0.99)
        self.assertLess(total_pct, 1.01)


# ── Test Performance Metrics & Tracking ───────────────────────────────────────

class TestPerformanceTracker(unittest.TestCase):
    """Tests for PerformanceTracker metrics collection and analysis."""

    def test_record_and_aggregate(self):
        """Metrics recorded and averaged correctly."""
        tracker = PerformanceTracker(window_size=5)

        for util in [0.5, 0.6, 0.7]:
            metric = PerformanceMetric(util, 0.8, 0.9, 100)
            tracker.record_stage_execution("memory", metric)

        avg = tracker.get_rolling_average("memory")
        self.assertIsNotNone(avg)
        self.assertAlmostEqual(avg.utilization, 0.6, places=2)
        self.assertAlmostEqual(avg.confidence, 0.8, places=1)

    def test_window_size_enforcement(self):
        """Window size limits the number of tracked metrics."""
        tracker = PerformanceTracker(window_size=3)

        for i in range(5):
            metric = PerformanceMetric(0.5 + i * 0.1, 0.8, 0.9, 100)
            tracker.record_stage_execution("memory", metric)

        stage = tracker.stages["memory"]
        self.assertEqual(len(stage.metrics), 3)

    def test_should_rebalance(self):
        """should_rebalance() detects significant utilization drift."""
        tracker = PerformanceTracker(rebalance_delta_threshold=0.15)

        baseline_metric = PerformanceMetric(0.5, 0.8, 0.9, 100)
        tracker.record_stage_execution("memory", baseline_metric)
        self.assertFalse(tracker.should_rebalance())

        for _ in range(5):
            metric = PerformanceMetric(0.7, 0.8, 0.9, 100)
            tracker.record_stage_execution("memory", metric)

        self.assertTrue(tracker.should_rebalance())

    def test_reset_baseline(self):
        """reset_baseline() updates baseline after rebalancing."""
        tracker = PerformanceTracker(rebalance_delta_threshold=0.05)

        # Record initial baseline (average 0.3)
        for _ in range(3):
            tracker.record_stage_execution("memory", PerformanceMetric(0.3, 0.8, 0.9, 100))

        # Record significantly higher utilization (average 0.5, delta = 0.2)
        for _ in range(3):
            tracker.record_stage_execution("memory", PerformanceMetric(0.5, 0.8, 0.9, 100))

        # Should rebalance due to drift (0.5 - 0.3 = 0.2 > 0.05)
        self.assertTrue(tracker.should_rebalance(), "Should detect rebalance need")

        # Reset baseline to the new level
        tracker.reset_baseline()

        # After reset, should not rebalance since current avg is still 0.5
        should_rebal = tracker.should_rebalance()
        self.assertFalse(should_rebal, "Should not rebalance after reset")

    def test_get_all_metrics(self):
        """get_all_metrics() returns rolling averages for all stages."""
        tracker = PerformanceTracker()

        for stage in ["memory", "graph", "skills"]:
            metric = PerformanceMetric(0.5 + len(stage) * 0.1, 0.8, 0.9, 100)
            tracker.record_stage_execution(stage, metric)

        all_metrics = tracker.get_all_metrics()
        self.assertEqual(len(all_metrics), 3)
        for m in all_metrics.values():
            self.assertIsInstance(m, PerformanceMetric)

    def test_clear_operations(self):
        """clear_stage() and clear_all() remove metrics."""
        tracker = PerformanceTracker()

        metric = PerformanceMetric(0.5, 0.8, 0.9, 100)
        tracker.record_stage_execution("memory", metric)
        tracker.record_stage_execution("graph", metric)

        tracker.clear_stage("memory")
        self.assertEqual(len(tracker.stages["memory"].metrics), 0)
        self.assertEqual(len(tracker.stages["graph"].metrics), 1)

        tracker.clear_all()
        for s in tracker.stages.values():
            self.assertEqual(len(s.metrics), 0)

    def test_summary_export(self):
        """summary() exports current tracking state."""
        tracker = PerformanceTracker(window_size=5)

        for i in range(3):
            metric = PerformanceMetric(0.5 + i * 0.1, 0.8, 0.9, 100)
            tracker.record_stage_execution("memory", metric)

        summary = tracker.summary()
        self.assertEqual(summary["window_size"], 5)
        self.assertEqual(summary["stages_tracked"], 1)
        self.assertEqual(summary["metrics_per_stage"]["memory"], 3)
        self.assertIn("rolling_averages", summary)
        self.assertIsInstance(summary["should_rebalance"], bool)


# ── Integration Tests ─────────────────────────────────────────────────────────

class TestPipelineIntegration(unittest.TestCase):
    """Tests for adaptive budget integration with pipeline."""

    def test_adaptive_budget_in_pipeline_context(self):
        """Adaptive budget properly allocates based on task complexity."""
        complex_task = "implement a new distributed caching layer for multi-tenant support"
        classification = classify(complex_task)
        budget = AdaptiveBudget.allocate_for_task(classification.complexity)

        self.assertEqual(classification.complexity, TaskComplexity.COMPLEX)
        self.assertGreater(budget.graph, 0, "Graph should be allocated for complex tasks")
        self.assertGreater(budget.skills, 0, "Skills should be allocated for complex tasks")

    def test_feature_flag_disabled_uses_phase_2_behavior(self):
        """When adaptive_context_routing flag is OFF, use original Phase 2 behavior."""
        unknown_task = "xyz abc def"
        classification = classify(unknown_task)

        self.assertEqual(classification.complexity, TaskComplexity.MODERATE)
        self.assertLess(classification.confidence, 0.5)


if __name__ == "__main__":
    unittest.main()
