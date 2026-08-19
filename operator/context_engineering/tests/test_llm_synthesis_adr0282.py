"""P-C — LLM synthesis stage (ADR-0282).

  * the stage is registered but NOT in the default pipeline (opt-in).
  * an egress stage in the config is DEFERRED by the pre-gate runner (build_context)
    and run by the async build_context_post_gate.
  * a successful synthesis sets synthesised_prompt + scratch['needs'].
  * over budget → skipped, deterministic brief stands (degrade, never block).
  * subprocess failure (timeout/nonzero) → failed, no synthesised_prompt.

Run: python3 operator/context_engineering/tests/test_llm_synthesis_adr0282.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[3]
for p in (_REPO / "operator" / "forge", _REPO / "core" / "console"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _load():
    ce = _REPO / "operator" / "context_engineering"
    spec = importlib.util.spec_from_file_location(
        "context_engineering", str(ce / "__init__.py"),
        submodule_search_locations=[str(ce)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["context_engineering"] = mod
    spec.loader.exec_module(mod)
    return mod


def _fake_completed(brief="a crafted brief", tools=None):
    inner = json.dumps({"brief": brief, "needs": {"tools": tools or ["mcp__forge__x"], "skills": []}})
    outer = json.dumps({"result": inner})
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=outer, stderr="")


class LLMSynthesisTests(unittest.TestCase):
    def setUp(self):
        self.ce = _load()
        self.stages = sys.modules["context_engineering.stages"]
        self.pipe = sys.modules["context_engineering.pipeline"]

    def test_not_in_default_pipeline(self):
        self.assertNotIn("llm_synthesis", self.stages.DEFAULT_PIPELINE)
        self.assertIsNotNone(self.stages.get_stage("llm_synthesis"), "but registered")

    def _pipeline_with_synth(self, egress=True):
        synth = {"stage": "llm_synthesis"}
        if egress:
            synth["config"] = {"egress_ok": True}
        return [{"stage": "memory"}, {"stage": "graph"}, {"stage": "skill"}, synth]

    def test_egress_guard_fail_closed(self):
        # without egress_ok the synthesis call never fires (residency floor)
        with patch.object(self.stages.config, "_read_pipeline_config",
                          return_value=self._pipeline_with_synth(egress=False)):
            bundle, trace = self.pipe.build_context("x", "_default", None, meter=False)
        with patch("context_engineering.license_gate.enforce_ce_llm_quota", return_value=True), \
             patch.object(subprocess, "run", return_value=_fake_completed()) as sr:
            bundle = asyncio.run(self.pipe.build_context_post_gate(bundle, trace))
        self.assertFalse(sr.called, "no egress_ok → no subprocess egress")
        self.assertIsNone(bundle.synthesised_prompt)

    def test_egress_stage_is_deferred_pre_gate(self):
        with patch.object(self.stages.config, "_read_pipeline_config",
                          return_value=self._pipeline_with_synth()):
            bundle, trace = self.pipe.build_context("x", "_default", None, meter=False)
        synth = next(s for s in trace["stages"] if s["stage"] == "llm_synthesis")
        self.assertEqual(synth["status"], "deferred")
        self.assertIsNone(bundle.synthesised_prompt, "not run pre-gate")
        self.assertEqual(len(bundle.scratch["_deferred"]), 1)

    def test_post_gate_runs_synthesis(self):
        with patch.object(self.stages.config, "_read_pipeline_config",
                          return_value=self._pipeline_with_synth()):
            bundle, trace = self.pipe.build_context("x", "_default", None, meter=False)
        with patch("context_engineering.license_gate.enforce_ce_llm_quota", return_value=True), \
             patch.object(subprocess, "run", return_value=_fake_completed()):
            bundle = asyncio.run(self.pipe.build_context_post_gate(bundle, trace))
        self.assertEqual(bundle.synthesised_prompt, "a crafted brief")
        self.assertEqual(bundle.scratch["needs"]["tools"], ["mcp__forge__x"])
        synth = [s for s in trace["stages"] if s.get("stage") == "llm_synthesis"]
        self.assertTrue(any(s.get("status") == "ok" for s in synth))

    def test_over_budget_degrades(self):
        with patch.object(self.stages.config, "_read_pipeline_config",
                          return_value=self._pipeline_with_synth()):
            bundle, trace = self.pipe.build_context("x", "_default", None, meter=False)
        with patch("context_engineering.license_gate.enforce_ce_llm_quota", return_value=False):
            bundle = asyncio.run(self.pipe.build_context_post_gate(bundle, trace))
        self.assertIsNone(bundle.synthesised_prompt, "over budget → deterministic stands")

    def test_subprocess_failure_degrades(self):
        with patch.object(self.stages.config, "_read_pipeline_config",
                          return_value=self._pipeline_with_synth()):
            bundle, trace = self.pipe.build_context("x", "_default", None, meter=False)
        with patch("context_engineering.license_gate.enforce_ce_llm_quota", return_value=True), \
             patch.object(subprocess, "run",
                          side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=45)):
            bundle = asyncio.run(self.pipe.build_context_post_gate(bundle, trace))
        self.assertIsNone(bundle.synthesised_prompt)
        synth = [s for s in trace["stages"] if s.get("stage") == "llm_synthesis"]
        self.assertTrue(any(s.get("status") == "failed" for s in synth))

    def test_neutral_cwd_cleanup_on_process_exit(self):
        """Verify temp directory is cleaned up via atexit handler (ADR-0391)."""
        import atexit
        import shutil
        import tempfile

        synth = sys.modules["context_engineering.stages.llm_synthesis"]

        # Reset global state
        synth._CWD = None

        # Call _neutral_cwd() to create the temp directory
        cwd = synth._neutral_cwd()
        self.assertIsNotNone(cwd, "neutral_cwd should create a directory")
        self.assertTrue(Path(cwd).exists(), "temp directory should exist")

        # Verify atexit was registered (we can't test the actual exit, but we can
        # call _cleanup_cwd directly to verify it works)
        synth._cleanup_cwd()
        self.assertFalse(Path(cwd).exists(), "cleanup should delete the directory")
        self.assertIsNone(synth._CWD, "_CWD should be reset to None after cleanup")

    def test_cleanup_cwd_handles_missing_directory(self):
        """Verify _cleanup_cwd handles non-existent directories gracefully."""
        synth = sys.modules["context_engineering.stages.llm_synthesis"]
        synth._CWD = "/nonexistent/directory"

        # Should not raise an exception
        synth._cleanup_cwd()
        self.assertIsNone(synth._CWD, "_CWD should be reset even if directory doesn't exist")


if __name__ == "__main__":
    unittest.main()
