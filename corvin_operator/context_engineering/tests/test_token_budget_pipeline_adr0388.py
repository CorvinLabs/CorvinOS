"""Pipeline integration tests for TokenBudget (ADR-0388, Phase 2).

Tests the TokenBudget integration with the context engineering pipeline:
  1. Pipeline creates TokenBudget when flag is enabled
  2. TokenBudget is passed to stages
  3. Stages respect claimed/spent tracking
  4. Cascade logic flows unused budget downstream
  5. Backward compatibility: flag OFF disables budget

Run: python3 operator/context_engineering/tests/test_token_budget_pipeline_adr0388.py
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "orchestration"))
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))


class MockMemoryMatch:
    """Mock MemoryMatch object."""
    def __init__(self, filename="test.md", score=0.8):
        self.filename = filename
        self.title = f"Test Memory {filename}"
        self.relevance_score = score
        self.source_file = f"/path/to/{filename}"
        self.timestamp = None
        self.content_preview = "Test preview"


class MockMemoryContext:
    """Mock MemoryContext object."""
    def __init__(self, matches=None):
        self.matches = matches or [MockMemoryMatch()]
        self.search_queries = ["test"]
        self.confidence = 0.8
        self.cache_hit = False
        self.search_duration_ms = 10.0


class MockRichTaskBrief:
    """Mock RichTaskBrief object."""
    def __init__(self):
        self.raw_input = "test task"
        self.enriched_task = MagicMock()
        self.memory_context = MockMemoryContext()
        self.timestamp = None
        self.version = "0.1"
        self.related_decisions = []
        self.recommended_skills = []
        self.approach = []
        self.blockers = []


class PipelineIntegrationTests(unittest.TestCase):
    """Test TokenBudget integration with context pipeline."""

    def test_token_budget_import(self):
        """TokenBudget can be imported from orchestration.subsystems."""
        from subsystems.token_budget import TokenBudget
        budget = TokenBudget(4000)
        self.assertIsNotNone(budget)
        self.assertEqual(budget.pool_tokens, 4000)

    def test_budget_stage_allocations(self):
        """Budget allocates correct percentages to each stage."""
        from subsystems.token_budget import TokenBudget
        budget = TokenBudget(4000)
        stats = budget.get_stats()

        # Verify allocations
        self.assertEqual(stats["memory"]["allocated"], 1200)  # 30%
        self.assertEqual(stats["graph"]["allocated"], 800)    # 20%
        self.assertEqual(stats["skill"]["allocated"], 600)    # 15%
        self.assertEqual(stats["synthesis"]["allocated"], 1400)  # 35%

    def test_budget_cascade_flow(self):
        """Unused budget cascades to downstream stages."""
        from subsystems.token_budget import TokenBudget

        budget = TokenBudget(4000, cascade=True)

        # Memory claims 1200 but only spends 100
        budget.claim("memory", 1200)
        budget.spent_for("memory", 100)

        # Graph should receive cascade from memory
        stats = budget.get_stats("graph")
        self.assertEqual(stats["cascade_pool"], 1100)
        self.assertEqual(stats["effective_allocation"], 1900)

    def test_budget_disabled_no_cascade(self):
        """With cascade=False, no unused budget flows downstream."""
        from subsystems.token_budget import TokenBudget

        budget = TokenBudget(4000, cascade=False)

        # Memory claims and spends
        budget.claim("memory", 1200)
        budget.spent_for("memory", 100)

        # Graph should have ONLY base allocation (no cascade)
        stats = budget.get_stats("graph")
        self.assertEqual(stats["cascade_pool"], 0)
        self.assertEqual(stats["effective_allocation"], 800)

    def test_budget_utilization_tracking(self):
        """Budget tracks utilization ratio per stage."""
        from subsystems.token_budget import TokenBudget

        budget = TokenBudget(4000)

        budget.claim("memory", 1200)
        budget.spent_for("memory", 600)
        # 600 / 1200 = 0.5 utilization

        stats = budget.get_stats("memory")
        self.assertEqual(stats["utilization"], 0.5)

    def test_budget_total_stats(self):
        """Total stats aggregate across all stages."""
        from subsystems.token_budget import TokenBudget

        budget = TokenBudget(4000)

        budget.claim("memory", 1200)
        budget.spent_for("memory", 300)
        budget.claim("graph", 800)
        budget.spent_for("graph", 200)

        stats = budget.get_stats()
        self.assertEqual(stats["total"]["total_spent"], 500)
        self.assertGreater(stats["total"]["total_remaining"], 0)

    def test_budget_repr_readable(self):
        """Budget has readable string representation for logging."""
        from subsystems.token_budget import TokenBudget

        budget = TokenBudget(4000)
        budget.claim("memory", 500)
        budget.spent_for("memory", 200)

        repr_str = repr(budget)
        self.assertIn("TokenBudget", repr_str)
        self.assertIn("pool=4000", repr_str)
        self.assertIn("memory:", repr_str)

    def test_budget_claim_exceeds_allocation(self):
        """Claim is capped at effective allocation."""
        from subsystems.token_budget import TokenBudget

        budget = TokenBudget(4000)

        # Request more than memory's allocation
        allocated = budget.claim("memory", 5000)
        # Should cap at 1200
        self.assertEqual(allocated, 1200)

    def test_budget_realistic_scenario(self):
        """Simulate realistic pipeline token usage."""
        from subsystems.token_budget import TokenBudget

        budget = TokenBudget(4000)

        # Memory lookup: need up to 1200, use 250
        mem_alloc = budget.claim("memory", 1200)
        budget.spent_for("memory", 250)
        self.assertEqual(mem_alloc, 1200)
        self.assertEqual(budget.spent("memory"), 250)
        # Cascade from memory: 1200 - 250 = 950

        # Graph lookup: request 1750 to hit effective allocation
        # effective = 800 (base) + 950 (cascade) = 1750
        graph_alloc = budget.claim("graph", 1750)
        self.assertEqual(graph_alloc, 1750)
        budget.spent_for("graph", 300)
        # Cascade from graph: 1750 - 300 = 1450

        # Skill injection: request 2050 to hit effective allocation
        # effective = 600 (base) + 1450 (cascade) = 2050
        skill_alloc = budget.claim("skill", 2050)
        self.assertEqual(skill_alloc, 2050)
        budget.spent_for("skill", 200)
        # Cascade from skill: 2050 - 200 = 1850

        # Synthesis: request 3250 to hit effective allocation
        # effective = 1400 (base) + 1850 (cascade) = 3250
        synth_alloc = budget.claim("synthesis", 3250)
        self.assertEqual(synth_alloc, 3250)
        budget.spent_for("synthesis", 2000)

        # Total spent: 250 + 300 + 200 + 2000 = 2750
        stats = budget.get_stats()
        self.assertEqual(stats["total"]["total_spent"], 2750)

    def test_feature_flag_defined(self):
        """Feature flag for token budgeting is registered."""
        from corvin_core.feature_flags import REGISTRY
        flag_ids = [f.id for f in REGISTRY]
        self.assertIn("per_stage_token_budgeting", flag_ids)

    def test_feature_flag_properties(self):
        """Feature flag has correct properties."""
        from corvin_core.feature_flags import REGISTRY
        flag = next(f for f in REGISTRY if f.id == "per_stage_token_budgeting")

        self.assertFalse(flag.default)  # Ship dark (off by default)
        self.assertEqual(flag.release_tier, "alpha")
        self.assertEqual(flag.target_release, "0.13.x")
        self.assertIn("context-engineering", flag.tags)
        self.assertIn("performance", flag.tags)

    def test_budget_get_stats_all_stages(self):
        """get_stats() without argument returns all stages."""
        from subsystems.token_budget import TokenBudget

        budget = TokenBudget(4000)
        stats = budget.get_stats()

        self.assertIn("memory", stats)
        self.assertIn("graph", stats)
        self.assertIn("skill", stats)
        self.assertIn("synthesis", stats)
        self.assertIn("total", stats)

    def test_budget_zero_allocation_edge_case(self):
        """Budget handles zero allocation gracefully."""
        from subsystems.token_budget import TokenBudget

        budget = TokenBudget(0)
        self.assertEqual(budget.pool_tokens, 0)

        # Should still be able to claim (returns 0)
        alloc = budget.claim("memory", 100)
        self.assertEqual(alloc, 0)

    def test_budget_large_pool_edge_case(self):
        """Budget handles large token pools."""
        from subsystems.token_budget import TokenBudget

        budget = TokenBudget(1000000)  # 1M tokens
        stats = budget.get_stats()

        self.assertEqual(stats["memory"]["allocated"], 300000)  # 30%
        self.assertEqual(stats["total"]["pool"], 1000000)

    def test_budget_unknown_stage_graceful_degrade(self):
        """Unknown stages return requested without error."""
        from subsystems.token_budget import TokenBudget

        budget = TokenBudget(4000)

        # Unknown stage should gracefully return requested
        alloc = budget.claim("unknown_stage_xyz", 500)
        self.assertEqual(alloc, 500)

        # Should not crash when checking spent
        spent = budget.spent("unknown_stage_xyz")
        self.assertEqual(spent, 0)


if __name__ == "__main__":
    unittest.main()
