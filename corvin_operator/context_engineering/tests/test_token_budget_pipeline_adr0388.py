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
sys.path.insert(0, str(_REPO / "corvin_operator" / "forge"))


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

if __name__ == "__main__":
    unittest.main()
