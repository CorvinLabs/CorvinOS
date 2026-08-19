"""TokenBudget component tests (ADR-0388, Phase 2).

Tests the per-stage token budgeting system:
  1. Stage allocation percentages
  2. Claim/spent tracking
  3. Cascade logic (unused → downstream)
  4. Utilization metrics
  5. Pipeline integration

Run: python3 -m pytest core/orchestration/tests/test_token_budget_adr0388.py -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "orchestration"))

from subsystems.token_budget import TokenBudget, StageBudget, STAGE_ALLOCATIONS


class TestTokenBudgetAllocation(unittest.TestCase):
    """Test per-stage allocation percentages."""

    def setUp(self):
        self.budget = TokenBudget(pool_tokens=4000)

    def test_initial_allocation(self):
        """Each stage gets correct % of pool."""
        stats = self.budget.get_stats()
        pool = 4000

        # Memory: 30%
        self.assertEqual(stats["memory"]["allocated"], int(pool * 0.30))
        # Graph (ADR): 20%
        self.assertEqual(stats["graph"]["allocated"], int(pool * 0.20))
        # Skill: 15%
        self.assertEqual(stats["skill"]["allocated"], int(pool * 0.15))
        # Synthesis: 35%
        self.assertEqual(stats["synthesis"]["allocated"], int(pool * 0.35))

    def test_total_allocation_equals_pool(self):
        """Sum of all allocations equals the pool."""
        stats = self.budget.get_stats()
        total = sum(
            stats[stage]["allocated"]
            for stage in ["memory", "graph", "skill", "synthesis"]
        )
        self.assertEqual(total, 4000)

    def test_claim_within_limit(self):
        """Claimed ≤ allocated."""
        allocated = self.budget.claim("memory", 100)
        self.assertLessEqual(allocated, 1200)  # 30% of 4000
        self.assertEqual(allocated, 100)  # Should equal requested when within limit

    def test_claim_exceeds_limit(self):
        """Claim capped at allocated when requesting too much."""
        # Memory allocated = 1200 (30% of 4000)
        allocated = self.budget.claim("memory", 2000)
        self.assertEqual(allocated, 1200)  # Capped at allocation

    def test_claim_unknown_stage(self):
        """Unknown stage returns requested (graceful degrade)."""
        allocated = self.budget.claim("unknown_stage", 100)
        self.assertEqual(allocated, 100)

    def test_remaining_after_claim(self):
        """Remaining budget tracks correctly after claim."""
        self.budget.claim("memory", 500)
        stats = self.budget.get_stats("memory")
        # allocated=1200, claimed=500, spent=0 → remaining=1200-0=1200
        self.assertEqual(stats["remaining"], 1200)


class TestTokenBudgetCascade(unittest.TestCase):
    """Test cascade logic (unused → downstream)."""

    def setUp(self):
        self.budget = TokenBudget(pool_tokens=4000, cascade=True)

    def test_cascade_unused_downstream(self):
        """Unused budget flows to next stage."""
        # Memory: allocated=1200, use 200 → unused=1200-200=1000
        self.budget.claim("memory", 1200)
        self.budget.spent_for("memory", 200)

        # Graph should have: base(800) + cascade(1000) = 1800
        stats_before = self.budget.get_stats("graph")
        self.assertEqual(stats_before["cascade_pool"], 1000)

        # Claim for graph should see the cascade pool
        allocated = self.budget.claim("graph", 2000)
        # base(800) + cascade(1000) = 1800, cap at 1800
        self.assertEqual(allocated, 1800)

    def test_multiple_cascades(self):
        """Cascade chains through all stages."""
        # Memory: 1200 allocated, use 100 → cascade 1200-100=1100
        self.budget.claim("memory", 1200)
        self.budget.spent_for("memory", 100)

        # Graph: 800 base + 1100 cascade = 1900 available
        self.budget.claim("graph", 1900)
        self.budget.spent_for("graph", 500)
        # Graph unused: 1900 - 500 = 1400

        # Skill: 600 base + 1400 cascade = 2000 available
        stats = self.budget.get_stats("skill")
        self.assertEqual(stats["cascade_pool"], 1400)
        allocated = self.budget.claim("skill", 3000)
        self.assertEqual(allocated, 2000)

    def test_cascade_disabled(self):
        """Cascade=False disables cascading."""
        budget_no_cascade = TokenBudget(pool_tokens=4000, cascade=False)

        # Memory: use 100, would cascade 1100 but cascade is off
        budget_no_cascade.claim("memory", 100)
        budget_no_cascade.spent_for("memory", 100)

        # Graph: should only have base allocation
        stats = budget_no_cascade.get_stats("graph")
        self.assertEqual(stats["cascade_pool"], 0)

    def test_cascade_to_last_stage(self):
        """Synthesis (last stage) receives cascaded budget."""
        # Use first three stages
        self.budget.claim("memory", 1200)
        self.budget.spent_for("memory", 100)
        # Memory unused: 1200 - 100 = 1100

        self.budget.claim("graph", 1900)
        self.budget.spent_for("graph", 300)
        # Graph pool: 800 + 1100 = 1900, unused: 1900 - 300 = 1600

        self.budget.claim("skill", 2200)
        self.budget.spent_for("skill", 200)
        # Skill pool: 600 + 1600 = 2200, unused: 2200 - 200 = 2000

        # Synthesis: should receive 2000 cascade
        stats = self.budget.get_stats("synthesis")
        self.assertEqual(stats["cascade_pool"], 2000)
        # Synthesis: 1400 base + 2000 cascade = 3400
        allocated = self.budget.claim("synthesis", 5000)
        self.assertEqual(allocated, 3400)


class TestTokenBudgetTracking(unittest.TestCase):
    """Test token spend tracking and utilization."""

    def setUp(self):
        self.budget = TokenBudget(pool_tokens=4000)

    def test_spent_tracking(self):
        """Actual tokens recorded correctly."""
        self.budget.claim("memory", 500)
        self.budget.spent_for("memory", 250)
        self.assertEqual(self.budget.spent("memory"), 250)

    def test_spent_unknown_stage(self):
        """Unknown stage spent returns 0."""
        spent = self.budget.spent("unknown")
        self.assertEqual(spent, 0)

    def test_remaining_calculation(self):
        """Remaining budget computed correctly."""
        self.budget.claim("memory", 500)
        self.budget.spent_for("memory", 300)
        # Memory: allocated=1200, claimed=500, spent=300, remaining=1200-300=900
        self.assertEqual(self.budget.spent("memory"), 300)

        # Per-stage remaining (allocated - spent)
        stats = self.budget.get_stats("memory")
        self.assertEqual(stats["remaining"], 900)

    def test_utilization_metric(self):
        """Utilization ratio [0.0, 1.0]."""
        self.budget.claim("memory", 1200)
        self.budget.spent_for("memory", 600)
        # allocated=1200, spent=600 → util=0.5
        stats = self.budget.get_stats("memory")
        self.assertEqual(stats["utilization"], 0.5)

    def test_utilization_100_percent(self):
        """Full utilization when spent=allocated."""
        self.budget.claim("memory", 1200)
        self.budget.spent_for("memory", 1200)
        stats = self.budget.get_stats("memory")
        self.assertEqual(stats["utilization"], 1.0)

    def test_utilization_over_100_percent_capped(self):
        """Utilization capped at 1.0 if overspend."""
        self.budget.claim("memory", 1200)
        self.budget.spent_for("memory", 2000)  # Over budget
        stats = self.budget.get_stats("memory")
        self.assertEqual(stats["utilization"], 1.0)

    def test_total_spent_across_stages(self):
        """Total spent sums across all stages."""
        self.budget.claim("memory", 300)
        self.budget.spent_for("memory", 300)
        self.budget.claim("graph", 200)
        self.budget.spent_for("graph", 200)
        self.budget.claim("skill", 150)
        self.budget.spent_for("skill", 150)

        stats = self.budget.get_stats()
        total = stats["total"]["total_spent"]
        self.assertEqual(total, 650)

    def test_total_remaining(self):
        """Total remaining computed across all stages."""
        self.budget.claim("memory", 300)
        self.budget.spent_for("memory", 100)
        # Memory: allocated=1200, spent=100, remaining=1200-100=1100
        # Graph: allocated=800, cascade=1100 (from memory unused), remaining=1900-0=1900
        # Skill: allocated=600, cascade=0 (not reached yet), remaining=600-0=600
        # Synthesis: allocated=1400, cascade=0 (not reached yet), remaining=1400-0=1400
        # Total = 1100 + 1900 + 600 + 1400 = 5000
        stats = self.budget.get_stats()
        self.assertEqual(stats["total"]["total_remaining"], 5000)


class TestStageBudgetDataclass(unittest.TestCase):
    """Test StageBudget dataclass."""

    def test_remaining_method(self):
        """StageBudget.remaining() computes allocated - spent."""
        sb = StageBudget("test", allocated=1000, spent=300)
        self.assertEqual(sb.remaining(), 700)

    def test_remaining_zero_when_full(self):
        """Remaining is 0 when spent >= allocated."""
        sb = StageBudget("test", allocated=1000, spent=1000)
        self.assertEqual(sb.remaining(), 0)

    def test_utilization_zero_when_zero_allocated(self):
        """Utilization is 0.0 when allocated=0."""
        sb = StageBudget("test", allocated=0, spent=100)
        self.assertEqual(sb.utilization(), 0.0)

    def test_utilization_method(self):
        """StageBudget.utilization() computes spent/allocated."""
        sb = StageBudget("test", allocated=1000, spent=500)
        self.assertEqual(sb.utilization(), 0.5)


class TestTokenBudgetIntegration(unittest.TestCase):
    """Test realistic usage patterns (pipeline integration simulation)."""

    def test_realistic_pipeline_flow(self):
        """Simulate a real context pipeline."""
        budget = TokenBudget(pool_tokens=4000)

        # Stage 1: Memory lookup
        mem_allocated = budget.claim("memory", 1200)
        # Assume memory returns 500 tokens of relevant matches
        budget.spent_for("memory", 500)
        self.assertEqual(budget.spent("memory"), 500)

        # Stage 2: ADR graph
        # Budget gets: base(800) + cascade(1200-500=700) = 1500
        adr_allocated = budget.claim("graph", 1500)
        self.assertEqual(adr_allocated, 1500)
        budget.spent_for("graph", 400)
        self.assertEqual(budget.spent("graph"), 400)

        # Stage 3: Skills
        # Budget gets: base(600) + cascade(1500-400=1100) = 1700
        skill_allocated = budget.claim("skill", 1700)
        self.assertEqual(skill_allocated, 1700)
        budget.spent_for("skill", 300)

        # Stage 4: Synthesis
        # Budget gets: base(1400) + cascade(1700-300=1400) = 2800
        synth_allocated = budget.claim("synthesis", 5000)
        self.assertEqual(synth_allocated, 2800)
        budget.spent_for("synthesis", 2000)

        # Total spent: 500 + 400 + 300 + 2000 = 3200
        stats = budget.get_stats()
        self.assertEqual(stats["total"]["total_spent"], 3200)
        # Remaining: pool - spent
        # Memory: claimed=500, so remaining=1200-500=700
        # Graph: claimed=400, effective=1500, so remaining=1500-400=1100
        # Skill: claimed=300, effective=1700, so remaining=1700-300=1400
        # Synthesis: claimed=2000, effective=2800, so remaining=2800-2000=800
        # Total: 700 + 1100 + 1400 + 800 = 4000
        self.assertEqual(stats["total"]["total_remaining"], 4000)

    def test_budget_repr_string(self):
        """TokenBudget.__repr__() produces readable output."""
        budget = TokenBudget(pool_tokens=4000)
        budget.claim("memory", 500)
        budget.spent_for("memory", 250)
        repr_str = repr(budget)
        self.assertIn("TokenBudget", repr_str)
        self.assertIn("pool=4000", repr_str)
        self.assertIn("memory:", repr_str)

    def test_full_utilization_scenario(self):
        """Test scenario where all stages are fully utilized."""
        budget = TokenBudget(pool_tokens=4000)

        budget.claim("memory", 1200)
        budget.spent_for("memory", 1200)

        budget.claim("graph", 1900)  # 800 + 1100 cascade
        budget.spent_for("graph", 1900)

        budget.claim("skill", 2200)  # 600 + 1600 cascade
        budget.spent_for("skill", 2200)

        budget.claim("synthesis", 3400)  # 1400 + 2000 cascade
        budget.spent_for("synthesis", 3400)

        stats = budget.get_stats()
        # Total spent = 1200 + 1900 + 2200 + 3400 = 8700 (overspend due to cascade)
        # But each individual stage stays within its pool
        self.assertEqual(stats["memory"]["utilization"], 1.0)
        self.assertEqual(stats["graph"]["utilization"], 1.0)
        self.assertEqual(stats["skill"]["utilization"], 1.0)
        self.assertEqual(stats["synthesis"]["utilization"], 1.0)


if __name__ == "__main__":
    unittest.main()
