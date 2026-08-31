"""Unit tests for acs_classify.py (ADR-0155 M1+M3).

Tests cover:
  - Signal table: representative DE/EN keyword hits
  - heuristic_classify: argmax, confidence bounds, DIRECT fallback
  - classify: force_heuristic gate, fail-open on exception
  - render_directive_block: DIRECT → empty, non-DIRECT → well-formed XML
  - ACSBlueprint.ldd_skills: correct mapping for each primitive
  - LLM fallback: mocked subprocess path (M3)
"""
from __future__ import annotations

import subprocess
import sys
import types
import unittest
import unittest.mock as mock
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import acs_classify as ac


# ── Signal-table / heuristic tests ───────────────────────────────────────────

class TestHeuristicLoop(unittest.TestCase):
    def _check(self, text: str) -> None:
        bp = ac.heuristic_classify(text)
        self.assertEqual(bp.primitive, ac.PRIMITIVE_LOOP, msg=f"text={text!r}")
        self.assertGreaterEqual(bp.confidence, ac.HEURISTIC_THRESHOLD)

    def test_de_temporal(self):
        self._check("Jede Stunde prüfen, ob neue Logs da sind.")

    def test_en_temporal(self):
        self._check("Run every 5 minutes to check for new events.")

    def test_de_watch(self):
        self._check("Überwache den Server und melde Fehler.")

    def test_en_monitor(self):
        self._check("Monitor the disk usage and alert when over 90%.")

    def test_retry_until_green(self):
        self._check("Führe Tests aus und iterate until green.")

    def test_schedule(self):
        self._check("on a schedule, check for new commits hourly")


class TestHeuristicWorkflow(unittest.TestCase):
    def _check(self, text: str) -> None:
        bp = ac.heuristic_classify(text)
        self.assertEqual(bp.primitive, ac.PRIMITIVE_WORKFLOW, msg=f"text={text!r}")
        self.assertGreaterEqual(bp.confidence, ac.HEURISTIC_THRESHOLD)

    def test_comprehensive_review(self):
        self._check("Comprehensive review of the entire codebase.")

    def test_security_audit(self):
        self._check("Security audit all subsystems in parallel.")

    def test_iterativer_code_review(self):
        self._check("Iterativer Code-Review von CorvinOS via ACS.")

    def test_multi_agent(self):
        self._check("Use multi-agent scan across every layer.")


class TestHeuristicGoal(unittest.TestCase):
    def _check(self, text: str) -> None:
        bp = ac.heuristic_classify(text)
        self.assertEqual(bp.primitive, ac.PRIMITIVE_GOAL, msg=f"text={text!r}")
        self.assertGreaterEqual(bp.confidence, ac.HEURISTIC_THRESHOLD)

    def test_de_langfristig(self):
        self._check("Langfristiges Ziel: Compliance-Framework vollständig implementieren.")

    def test_en_persistent(self):
        self._check("Set as persistent goal: keep tests green across all sessions.")

    def test_multi_session(self):
        self._check("This is a multi-session objective — remember across restarts.")


class TestHeuristicCompute(unittest.TestCase):
    def _check(self, text: str) -> None:
        bp = ac.heuristic_classify(text)
        self.assertEqual(bp.primitive, ac.PRIMITIVE_COMPUTE, msg=f"text={text!r}")
        self.assertGreaterEqual(bp.confidence, ac.HEURISTIC_THRESHOLD)

    def test_plot(self):
        self._check("Plot a histogram of the monthly revenue data.")

    def test_statistics(self):
        self._check("Calculate mean and median of the dataset.")

    def test_csv(self):
        self._check("Process this CSV and compute a summary report.")

    def test_ml(self):
        self._check("Train a regression model on the sales data.")


class TestHeuristicDelegate(unittest.TestCase):
    def _check(self, text: str) -> None:
        bp = ac.heuristic_classify(text)
        self.assertEqual(bp.primitive, ac.PRIMITIVE_DELEGATE, msg=f"text={text!r}")
        self.assertGreaterEqual(bp.confidence, ac.HEURISTIC_THRESHOLD)

    def test_ask_hermes(self):
        self._check("Ask Hermes to summarize this text locally.")

    def test_de_delegiere(self):
        self._check("Delegiere diese Aufgabe an Hermes.")

    def test_via_hermes(self):
        self._check("Bitte verarbeite via Hermes, ohne Cloud-Egress.")

    def test_hermes_fast(self):
        self._check("Use hermes-fast for this translation.")


class TestHeuristicDirect(unittest.TestCase):
    def test_simple_question(self):
        bp = ac.heuristic_classify("What is the capital of France?")
        self.assertEqual(bp.primitive, ac.PRIMITIVE_DIRECT)
        self.assertGreaterEqual(bp.confidence, 0.90)

    def test_empty_string(self):
        bp = ac.heuristic_classify("")
        self.assertEqual(bp.primitive, ac.PRIMITIVE_DIRECT)

    def test_random_text(self):
        bp = ac.heuristic_classify("Hello! Can you help me write a poem?")
        self.assertEqual(bp.primitive, ac.PRIMITIVE_DIRECT)


# ── classify() entry-point tests ─────────────────────────────────────────────

class TestClassifyEntryPoint(unittest.TestCase):
    def test_force_heuristic_skips_llm(self):
        """force_heuristic=True must never call _llm_classify."""
        with mock.patch.object(ac, "_llm_classify") as mocked:
            bp = ac.classify("Monitor the CPU every minute.", force_heuristic=True)
        mocked.assert_not_called()
        self.assertEqual(bp.primitive, ac.PRIMITIVE_LOOP)

    def test_high_confidence_skips_llm(self):
        """Heuristic confidence >= threshold must not call _llm_classify."""
        with mock.patch.object(ac, "_llm_classify") as mocked:
            bp = ac.classify("Run a comprehensive security audit of all layers.")
        mocked.assert_not_called()
        self.assertEqual(bp.primitive, ac.PRIMITIVE_WORKFLOW)

    def test_low_confidence_calls_llm_fallback(self):
        """Heuristic confidence < threshold should call _llm_classify."""
        fallback = ac.ACSBlueprint(primitive=ac.PRIMITIVE_GOAL, confidence=0.75, path="llm")
        with mock.patch.object(ac, "heuristic_classify",
                               return_value=ac.ACSBlueprint(
                                   primitive=ac.PRIMITIVE_LOOP, confidence=0.50, path="heuristic"
                               )):
            with mock.patch.object(ac, "_llm_classify", return_value=fallback) as mocked_llm:
                bp = ac.classify("some ambiguous task")
        mocked_llm.assert_called_once()
        self.assertEqual(bp.primitive, ac.PRIMITIVE_GOAL)
        self.assertEqual(bp.path, "llm")

    def test_fail_open_on_exception(self):
        """Any exception in classify() must produce DIRECT, never propagate."""
        with mock.patch.object(ac, "heuristic_classify", side_effect=RuntimeError("boom")):
            bp = ac.classify("any task")
        self.assertEqual(bp.primitive, ac.PRIMITIVE_DIRECT)
        self.assertEqual(bp.path, "error")

    def test_empty_task(self):
        bp = ac.classify("  ")
        self.assertEqual(bp.primitive, ac.PRIMITIVE_DIRECT)
        self.assertEqual(bp.path, "heuristic")

    def test_channel_chat_key_passed_through(self):
        """channel + chat_key don't affect classification but must not raise."""
        bp = ac.classify("Monitor every hour.", channel="discord", chat_key="123:456")
        self.assertEqual(bp.primitive, ac.PRIMITIVE_LOOP)


# ── render_directive_block tests ─────────────────────────────────────────────

class TestRenderDirectiveBlock(unittest.TestCase):
    def test_direct_renders_empty(self):
        bp = ac.ACSBlueprint(primitive=ac.PRIMITIVE_DIRECT, confidence=0.95)
        self.assertEqual(ac.render_directive_block(bp), "")

    def test_low_confidence_renders_empty(self):
        bp = ac.ACSBlueprint(primitive=ac.PRIMITIVE_LOOP, confidence=0.40)
        self.assertEqual(ac.render_directive_block(bp), "")

    def test_loop_renders_acs_directive_block(self):
        bp = ac.ACSBlueprint(primitive=ac.PRIMITIVE_LOOP, confidence=0.90, path="heuristic")
        block = ac.render_directive_block(bp)
        self.assertIn("<acs_directive", block)
        self.assertIn('primitive="LOOP"', block)
        self.assertIn("</acs_directive>", block)
        self.assertIn("dry_streak=2", block)
        self.assertIn("e2e-driven-iteration", block)

    def test_workflow_renders_convergence(self):
        bp = ac.ACSBlueprint(primitive=ac.PRIMITIVE_WORKFLOW, confidence=0.88, path="heuristic")
        block = ac.render_directive_block(bp)
        self.assertIn("WORKFLOW", block)
        self.assertIn("dry_streak=2", block)
        self.assertIn("dialectical-reasoning", block)

    def test_goal_renders_session_goal_hint(self):
        bp = ac.ACSBlueprint(primitive=ac.PRIMITIVE_GOAL, confidence=0.90, path="heuristic")
        block = ac.render_directive_block(bp)
        self.assertIn("GOAL", block)
        self.assertIn("session_goal", block)

    def test_compute_renders_l25_hint(self):
        bp = ac.ACSBlueprint(primitive=ac.PRIMITIVE_COMPUTE, confidence=0.80, path="heuristic")
        block = ac.render_directive_block(bp)
        self.assertIn("COMPUTE", block)
        self.assertIn("compute_run", block)

    def test_delegate_renders_engine_hint(self):
        bp = ac.ACSBlueprint(primitive=ac.PRIMITIVE_DELEGATE, confidence=0.95, path="heuristic")
        block = ac.render_directive_block(bp)
        self.assertIn("DELEGATE", block)
        self.assertIn("root-cause-by-layer", block)

    def test_block_is_xml_like(self):
        """Block must open and close with matching <acs_directive> tags."""
        for prim in [ac.PRIMITIVE_LOOP, ac.PRIMITIVE_WORKFLOW, ac.PRIMITIVE_GOAL]:
            bp = ac.ACSBlueprint(primitive=prim, confidence=0.85)
            block = ac.render_directive_block(bp)
            self.assertTrue(block.startswith("<acs_directive"),
                            msg=f"Block for {prim} does not start with <acs_directive>")
            self.assertTrue(block.rstrip().endswith("</acs_directive>"),
                            msg=f"Block for {prim} does not end with </acs_directive>")


# ── ACSBlueprint.ldd_skills tests ────────────────────────────────────────────

class TestBlueprintLddSkills(unittest.TestCase):
    _expected = {
        ac.PRIMITIVE_GOAL:     {"loop-driven-engineering", "drift-detection"},
        ac.PRIMITIVE_LOOP:     {"e2e-driven-iteration", "reproducibility-first"},
        ac.PRIMITIVE_WORKFLOW: {"dialectical-reasoning", "docs-as-definition-of-done"},
        ac.PRIMITIVE_COMPUTE:  {"docs-as-definition-of-done"},
        ac.PRIMITIVE_DELEGATE: {"root-cause-by-layer"},
        ac.PRIMITIVE_DIRECT:   set(),
    }

    def test_all_primitives_have_expected_skills(self):
        for prim, expected_skills in self._expected.items():
            bp = ac.ACSBlueprint(primitive=prim)
            self.assertEqual(set(bp.ldd_skills), expected_skills,
                             msg=f"wrong ldd_skills for {prim}")


# ── LLM fallback (M3) — mocked subprocess path ───────────────────────────────

class TestLlmFallback(unittest.TestCase):
    def _run_with_mock_helper(self, stdout: str, returncode: int = 0) -> ac.ACSBlueprint:
        """Call _llm_classify with a mocked helper_model + subprocess."""
        import types as _types
        fake_hm = _types.ModuleType("helper_model")
        fake_hm.SITE_ACS_CLASSIFY = "acs_classify"
        fake_hm.claude_args = mock.MagicMock(return_value=["--model", "fake-haiku"])
        fake_hm.resolve_claude_bin = mock.MagicMock(return_value="/usr/bin/claude")

        # Use a SimpleNamespace so returncode and stdout are plain attributes,
        # not MagicMock children (MagicMock child attributes compare truthy,
        # which would make `proc.returncode != 0` evaluate to True).
        import types as _t
        proc_stub = _t.SimpleNamespace(returncode=returncode, stdout=stdout)

        with mock.patch.dict("sys.modules", {"helper_model": fake_hm}):
            # Patch acs_classify.subprocess.run directly — acs_classify imports
            # subprocess at module level, so we target that module reference
            # to guarantee the patch lands on the right object.
            with mock.patch.object(ac.subprocess, "run", return_value=proc_stub):
                return ac._llm_classify("some ambiguous task")

    def test_llm_returns_loop(self):
        out = '{"primitive":"LOOP","confidence":0.82,"reason":"recurring pattern"}'
        bp = self._run_with_mock_helper(out)
        self.assertEqual(bp.primitive, ac.PRIMITIVE_LOOP)
        self.assertAlmostEqual(bp.confidence, 0.82, places=2)
        self.assertEqual(bp.path, "llm")

    def test_llm_unknown_primitive_becomes_direct(self):
        out = '{"primitive":"BOGUS","confidence":0.9,"reason":"unknown"}'
        bp = self._run_with_mock_helper(out)
        self.assertEqual(bp.primitive, ac.PRIMITIVE_DIRECT)

    def test_llm_nonzero_rc_becomes_error(self):
        bp = self._run_with_mock_helper("", returncode=1)
        self.assertEqual(bp.primitive, ac.PRIMITIVE_DIRECT)
        self.assertEqual(bp.path, "llm_error")

    def test_llm_no_json_becomes_error(self):
        bp = self._run_with_mock_helper("some plain text without JSON")
        self.assertEqual(bp.primitive, ac.PRIMITIVE_DIRECT)
        self.assertEqual(bp.path, "llm_error")

    def test_llm_opt_out_when_empty_model_args(self):
        import types as _t
        fake_hm = _t.ModuleType("helper_model")
        fake_hm.SITE_ACS_CLASSIFY = "acs_classify"
        fake_hm.claude_args = mock.MagicMock(return_value=[])  # opt-out
        fake_hm.resolve_claude_bin = mock.MagicMock(return_value="/usr/bin/claude")

        with mock.patch.dict("sys.modules", {"helper_model": fake_hm}):
            bp = ac._llm_classify("some task")
        self.assertEqual(bp.path, "llm_opt_out")
        self.assertEqual(bp.primitive, ac.PRIMITIVE_DIRECT)

    def test_llm_confidence_clamped(self):
        out = '{"primitive":"WORKFLOW","confidence":2.5,"reason":"way over"}'
        bp = self._run_with_mock_helper(out)
        self.assertLessEqual(bp.confidence, 1.0)

    def test_llm_json_in_text_wrapper(self):
        """Model may prefix JSON with explanation text."""
        out = 'Here is my classification: {"primitive":"COMPUTE","confidence":0.78,"reason":"dataset"}'
        bp = self._run_with_mock_helper(out)
        self.assertEqual(bp.primitive, ac.PRIMITIVE_COMPUTE)

    def test_llm_rfind_fallback_bad_slice_returns_error(self):
        """If the rfind-extracted slice is not valid JSON, reason is 'bad_json_in_fallback'."""
        # Simulate output where rfind anchors to } inside the reason field:
        # direct-parse fails (leading text), rfind slice is truncated and invalid.
        out = 'Result: {"primitive":"DIRECT","confidence":0.9,"reason":"uses {x} pattern"} see above'
        # The direct parse of the full string fails; rfind hits the trailing }
        # after "above" — but since there is no { after the JSON, rfind lands
        # on the outer { correctly. This test verifies the bad-slice path by
        # injecting a deliberately broken partial-JSON string.
        broken = 'note: {"primitive":"DIRECT"'  # no closing }
        bp = self._run_with_mock_helper(broken)
        self.assertEqual(bp.path, "llm_error")
        self.assertIn(bp.reason, ("no JSON in output", "bad_json_in_fallback"))


class TestFallbackLlmAuditAllowlist(unittest.TestCase):
    """Guard against accidental rename of audit fields for acs_x.fallback_llm.

    The adapter emits ``llm_confidence`` in ``acs_x.fallback_llm`` events.
    If this drifts back to ``heuristic_confidence``, security_events.py drops
    the field silently. This test ensures the allowlist matches the emit site.
    """

    def test_acs_fallback_llm_allowlist_has_llm_confidence(self):
        import sys
        import os
        forge_path = os.path.join(os.path.dirname(__file__),
                                  "..", "..", "forge", "forge")
        sys.path.insert(0, forge_path)
        import security_events as se
        allowlist = se._EVENT_ALLOWLIST.get("acs_x.fallback_llm", frozenset())
        self.assertIn("llm_confidence", allowlist,
                      "acs_x.fallback_llm allowlist must contain 'llm_confidence'")
        self.assertNotIn("heuristic_confidence", allowlist,
                         "old field name 'heuristic_confidence' must not be in allowlist")
        self.assertIn("primitive", allowlist)
        self.assertIn("model", allowlist)


# ── ADR-0160 M4a: Persona-Awareness ─────────────────────────────────────────

class TestPersonaAwareness(unittest.TestCase):
    """render_directive_block() suppresses WORKFLOW/DELEGATE for worker personas."""

    def _bp(self, primitive: str) -> ac.ACSBlueprint:
        return ac.ACSBlueprint(primitive=primitive, confidence=0.90, path="heuristic")

    def test_hermes_worker_suppresses_workflow(self):
        block = ac.render_directive_block(self._bp(ac.PRIMITIVE_WORKFLOW),
                                          persona="hermes-worker")
        self.assertEqual(block, "", "hermes-worker must not receive WORKFLOW directive")

    def test_hermes_worker_suppresses_delegate(self):
        block = ac.render_directive_block(self._bp(ac.PRIMITIVE_DELEGATE),
                                          persona="hermes-worker")
        self.assertEqual(block, "", "hermes-worker must not receive DELEGATE directive")

    def test_copilot_worker_suppresses_workflow(self):
        block = ac.render_directive_block(self._bp(ac.PRIMITIVE_WORKFLOW),
                                          persona="copilot-worker")
        self.assertEqual(block, "", "copilot-worker must not receive WORKFLOW directive")

    def test_copilot_worker_suppresses_delegate(self):
        block = ac.render_directive_block(self._bp(ac.PRIMITIVE_DELEGATE),
                                          persona="copilot-worker")
        self.assertEqual(block, "", "copilot-worker must not receive DELEGATE directive")

    def test_hermes_worker_allows_loop(self):
        """LOOP is not suppressed — hermes-worker can run iterative tasks."""
        block = ac.render_directive_block(self._bp(ac.PRIMITIVE_LOOP),
                                          persona="hermes-worker")
        self.assertIn("<acs_directive", block)
        self.assertIn("LOOP", block)

    def test_hermes_worker_allows_compute(self):
        """COMPUTE is not suppressed — hermes-worker can run local data tasks."""
        block = ac.render_directive_block(self._bp(ac.PRIMITIVE_COMPUTE),
                                          persona="hermes-worker")
        self.assertIn("<acs_directive", block)

    def test_assistant_persona_allows_workflow(self):
        """Non-worker personas are not suppressed."""
        block = ac.render_directive_block(self._bp(ac.PRIMITIVE_WORKFLOW),
                                          persona="assistant")
        self.assertIn("<acs_directive", block)
        self.assertIn("WORKFLOW", block)

    def test_empty_persona_allows_workflow(self):
        """Default empty persona (no persona set) is not suppressed."""
        block = ac.render_directive_block(self._bp(ac.PRIMITIVE_WORKFLOW))
        self.assertIn("<acs_directive", block)

    def test_classify_still_returns_true_primitive_for_worker(self):
        """Suppression is render-only — classify() still returns the true primitive."""
        bp = ac.heuristic_classify("Comprehensive review of the entire codebase.")
        self.assertEqual(bp.primitive, ac.PRIMITIVE_WORKFLOW,
                         "classify must return WORKFLOW regardless of active persona")


# ── ADR-0160 M4b: Convergence Override ──────────────────────────────────────

class TestConvergenceOverride(unittest.TestCase):
    """render_directive_block() respects convergence_override dict."""

    def _bp(self, primitive: str) -> ac.ACSBlueprint:
        return ac.ACSBlueprint(primitive=primitive, confidence=0.90, path="heuristic")

    def test_override_loop_convergence(self):
        override = {"LOOP": "dry_streak=3 OR max_rounds=10"}
        block = ac.render_directive_block(self._bp(ac.PRIMITIVE_LOOP),
                                          convergence_override=override)
        self.assertIn("dry_streak=3 OR max_rounds=10", block)
        self.assertNotIn("max_rounds=3", block)

    def test_override_workflow_convergence(self):
        override = {"WORKFLOW": "dry_streak=4 OR max_rounds=6"}
        block = ac.render_directive_block(self._bp(ac.PRIMITIVE_WORKFLOW),
                                          convergence_override=override)
        self.assertIn("dry_streak=4 OR max_rounds=6", block)

    def test_override_does_not_affect_unrelated_primitive(self):
        """Override for LOOP must not change WORKFLOW output."""
        override = {"LOOP": "dry_streak=5 OR max_rounds=20"}
        block = ac.render_directive_block(self._bp(ac.PRIMITIVE_WORKFLOW),
                                          convergence_override=override)
        self.assertIn("dry_streak=2 OR max_rounds=3", block)  # default unchanged

    def test_none_override_uses_default(self):
        block = ac.render_directive_block(self._bp(ac.PRIMITIVE_LOOP),
                                          convergence_override=None)
        self.assertIn("dry_streak=2 OR max_rounds=3", block)

    def test_empty_dict_override_uses_default(self):
        block = ac.render_directive_block(self._bp(ac.PRIMITIVE_LOOP),
                                          convergence_override={})
        self.assertIn("dry_streak=2 OR max_rounds=3", block)


# ── ADR-0160 M4a: Audit allowlist guard ─────────────────────────────────────

class TestPersonaSuppressedAuditAllowlist(unittest.TestCase):
    """acs_x.persona_suppressed must be in security_events allowlist."""

    def test_allowlist_has_persona_suppressed(self):
        import os
        forge_path = os.path.join(os.path.dirname(__file__),
                                  "..", "..", "forge", "forge")
        import sys
        sys.path.insert(0, forge_path)
        import security_events as se
        self.assertIn("acs_x.persona_suppressed", se._EVENT_ALLOWLIST,
                      "acs_x.persona_suppressed must be in _EVENT_ALLOWLIST")
        fields = se._EVENT_ALLOWLIST["acs_x.persona_suppressed"]
        self.assertIn("primitive", fields)
        self.assertIn("persona", fields)
        self.assertIn("channel", fields)
        self.assertIn("chat_key", fields)
        self.assertIn("acs_x.persona_suppressed", se.EVENT_SEVERITY,
                      "acs_x.persona_suppressed must be in EVENT_SEVERITY")


# ── ADR-0164 M2 — loop/workflow engineering invariants ──────────────────────

class TestADR0164EngineeringInvariants(unittest.TestCase):
    """render_directive_block() includes ADR-0164 loop and workflow engineering invariants."""

    def _block(self, primitive: str) -> str:
        bp = ac.ACSBlueprint(primitive=primitive, confidence=0.80, path="heuristic")
        return ac.render_directive_block(bp)

    def test_loop_block_contains_loss_signal_invariant(self):
        block = self._block(ac.PRIMITIVE_LOOP)
        self.assertIn("loss signal BEFORE first edit", block,
                      "LOOP directive must demand a loss signal before iteration 1")

    def test_loop_block_contains_k_max_invariant(self):
        block = self._block(ac.PRIMITIVE_LOOP)
        self.assertIn("K_MAX", block,
                      "LOOP directive must state K_MAX enforcement rule")

    def test_loop_block_contains_convergence_invariant(self):
        block = self._block(ac.PRIMITIVE_LOOP)
        self.assertIn("convergence criterion", block,
                      "LOOP directive must demand an explicit convergence criterion")

    def test_loop_block_contains_dedup_invariant(self):
        block = self._block(ac.PRIMITIVE_LOOP)
        self.assertIn("Deduplicate", block,
                      "LOOP directive must require central dedup before fixing")

    def test_workflow_block_contains_output_schema_invariant(self):
        block = self._block(ac.PRIMITIVE_WORKFLOW)
        self.assertIn("output schema", block,
                      "WORKFLOW directive must require structured output schema")

    def test_workflow_block_contains_fan_out_invariant(self):
        block = self._block(ac.PRIMITIVE_WORKFLOW)
        self.assertIn("Fan-out", block,
                      "WORKFLOW directive must enforce fan-out before synthesis")

    def test_workflow_block_contains_verifier_invariant(self):
        block = self._block(ac.PRIMITIVE_WORKFLOW)
        self.assertIn("verifier", block,
                      "WORKFLOW directive must require adversarial verify pass")

    def test_workflow_block_contains_dry_streak_invariant(self):
        block = self._block(ac.PRIMITIVE_WORKFLOW)
        self.assertIn("Dry-streak", block,
                      "WORKFLOW directive must state dry-streak convergence rule")

    def test_loop_block_still_has_original_content(self):
        """Regression: original LOOP directive text must not be dropped."""
        block = self._block(ac.PRIMITIVE_LOOP)
        self.assertIn("/loop", block)
        self.assertIn("convergence", block)

    def test_workflow_block_still_has_original_content(self):
        """Regression: original WORKFLOW directive text must not be dropped."""
        block = self._block(ac.PRIMITIVE_WORKFLOW)
        self.assertIn("Workflow tool", block)
        self.assertIn("adversarial verification", block)


# ── ADR-0164 M3/M4 — audit allowlist ────────────────────────────────────────

class TestATO164AuditAllowlist(unittest.TestCase):
    """task_orchestrator.* events must be in security_events allowlist."""

    def _se(self):
        import os, sys
        forge_path = os.path.join(os.path.dirname(__file__),
                                  "..", "..", "forge", "forge")
        sys.path.insert(0, forge_path)
        import importlib
        return importlib.import_module("security_events")

    def test_plan_generated_in_allowlist(self):
        se = self._se()
        self.assertIn("task_orchestrator.plan_generated", se._EVENT_ALLOWLIST)
        self.assertIn("task_orchestrator.plan_generated", se.EVENT_SEVERITY)

    def test_convergence_low_in_allowlist(self):
        se = self._se()
        self.assertIn("task_orchestrator.convergence_low", se._EVENT_ALLOWLIST)
        self.assertEqual(se.EVENT_SEVERITY["task_orchestrator.convergence_low"], "WARNING")

    def test_goal_template_weak_in_allowlist(self):
        se = self._se()
        self.assertIn("task_orchestrator.goal_template_weak", se._EVENT_ALLOWLIST)
        self.assertEqual(se.EVENT_SEVERITY["task_orchestrator.goal_template_weak"], "WARNING")

    def test_strategy_drift_in_allowlist(self):
        se = self._se()
        self.assertIn("task_orchestrator.strategy_drift", se._EVENT_ALLOWLIST)
        self.assertEqual(se.EVENT_SEVERITY["task_orchestrator.strategy_drift"], "WARNING")

    def test_plan_generated_allowlist_fields(self):
        se = self._se()
        fields = se._EVENT_ALLOWLIST["task_orchestrator.plan_generated"]
        for f in ("task_type", "execution_strategy", "k_max", "channel", "chat_key", "tenant_id"):
            self.assertIn(f, fields)

    def test_task_text_not_in_allowlist_fields(self):
        """task text / goal text must never appear in audit details."""
        se = self._se()
        for event in ("task_orchestrator.plan_generated",
                       "task_orchestrator.convergence_low",
                       "task_orchestrator.goal_template_weak",
                       "task_orchestrator.strategy_drift"):
            fields = se._EVENT_ALLOWLIST[event]
            self.assertNotIn("task_text", fields, f"{event} must not allow task_text")
            self.assertNotIn("goal_text", fields, f"{event} must not allow goal_text")


if __name__ == "__main__":
    unittest.main(verbosity=2)
