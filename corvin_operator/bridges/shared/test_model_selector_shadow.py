"""E2E-wiring proof — model_selector_shadow.py's full loop.

Until 2026-09-10 this loop didn't exist at all: os.model_selector's
classifier (core/skills/os_skills/model_selector.py) had zero production
callers, and core.learning.model_selection_optimizer.ConfidenceOptimizer
(ADR-0644, Bayesian) had no outcome-feedback source. This proves, end to
end, through the real module boundary (no internal mocking of the pieces
under test):

  1. shadow_classify_task() classifies a real prompt and stashes a pending
     (task_type, model) recommendation keyed by chat_key.
  2. report_turn_outcome() consumes that pending entry and feeds a real
     quality signal into ConfidenceOptimizer.process_feedback.
  3. The result is visible through get_optimizer() — the SAME accessor
     core/console/corvin_console/routes/model_selection_analytics.py uses —
     proving the console-visible confidence numbers are real, not constants.
  4. Confidence survives a fresh ConfidenceOptimizer instance (simulating a
     second process, e.g. the console gateway reading what the bridge
     daemon wrote) via confidence_persistence.py.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))


class ModelSelectorShadowLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._prev_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp.name

        # Fresh singletons per test — module-level state (optimizer, pending
        # dict) must not leak between tests or hide a real persistence bug
        # behind an in-memory cache hit.
        import model_selector_shadow as mss
        mss._PENDING.clear()
        import core.learning.model_selection_optimizer as mso
        mso._optimizer = None
        self.mss = mss
        self.mso = mso

    def tearDown(self) -> None:
        if self._prev_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._prev_home
        self._tmp.cleanup()

    def test_full_loop_success_produces_real_confidence(self) -> None:
        for _ in range(6):
            self.mss.shadow_classify_task("list the files here", "_default", chat_key="chat-a")
            self.mss.report_turn_outcome("chat-a", success=True)

        opt = self.mso.get_optimizer()
        stats = opt.get_stats("SIMPLE", "claude-haiku-4-5-20251001", "_default")
        self.assertEqual(stats.n_samples, 6)
        self.assertAlmostEqual(stats.mean_quality, 0.85, places=6)
        self.assertGreater(stats.confidence_score, 0.5)  # moved up from the uninformed 0.5 prior

    def test_failure_outcome_pulls_confidence_down(self) -> None:
        for _ in range(6):
            self.mss.shadow_classify_task("list the files here", "_default", chat_key="chat-b")
            self.mss.report_turn_outcome("chat-b", success=False)

        opt = self.mso.get_optimizer()
        stats = opt.get_stats("SIMPLE", "claude-haiku-4-5-20251001", "_default")
        self.assertAlmostEqual(stats.mean_quality, 0.2, places=6)
        self.assertLess(stats.confidence_score, 0.5)

    def test_pending_entry_consumed_exactly_once(self) -> None:
        self.mss.shadow_classify_task("list the files here", "_default", chat_key="chat-c")
        self.mss.report_turn_outcome("chat-c", success=True)
        opt = self.mso.get_optimizer()
        n_after_first = opt.get_stats("SIMPLE", "claude-haiku-4-5-20251001", "_default").n_samples

        # A second outcome report with no new classification must be a no-op
        # (nothing pending) — never double-counts the same turn.
        self.mss.report_turn_outcome("chat-c", success=True)
        n_after_second = opt.get_stats("SIMPLE", "claude-haiku-4-5-20251001", "_default").n_samples
        self.assertEqual(n_after_first, n_after_second)

    def test_outcome_with_no_prior_classification_is_a_safe_noop(self) -> None:
        # e.g. a turn refused at the budget-preflight gate, before classify ever ran.
        self.mss.report_turn_outcome("never-classified-chat-key", success=True)  # must not raise

    def test_confidence_survives_a_fresh_optimizer_instance(self) -> None:
        """Simulates a second process (e.g. the console gateway) reading
        what a first process (the bridge daemon) wrote — the whole point of
        confidence_persistence.py."""
        for _ in range(6):
            self.mss.shadow_classify_task("list the files here", "_default", chat_key="chat-d")
            self.mss.report_turn_outcome("chat-d", success=True)

        self.mso._optimizer = None  # drop the singleton — force a fresh instance
        fresh = self.mso.get_optimizer()
        stats = fresh.get_stats("SIMPLE", "claude-haiku-4-5-20251001", "_default")
        self.assertEqual(stats.n_samples, 6)

    def test_saved_provider_override_is_what_gets_credited(self) -> None:
        """The model credited with the outcome must be whatever the operator
        actually saved for that tier — not the hardcoded provider default —
        or feedback silently attributes to the wrong model forever."""
        from core.models.model_selection_config import save_config, load_config

        cfg = load_config("_default")
        cfg["SIMPLE"] = {"selected_model": "claude-opus-5", "provider": None, "alternatives": []}
        save_config("_default", cfg)

        self.mss.shadow_classify_task("list the files here", "_default", chat_key="chat-e")
        self.mss.report_turn_outcome("chat-e", success=True)

        opt = self.mso.get_optimizer()
        stats = opt.get_stats("SIMPLE", "claude-opus-5", "_default")
        self.assertEqual(stats.n_samples, 1)


if __name__ == "__main__":
    unittest.main()
