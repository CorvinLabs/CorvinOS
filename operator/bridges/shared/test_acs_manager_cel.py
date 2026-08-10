"""E2E-wiring — CEL brief in the ACS MANAGER prompt (ADR-0275).

The manager holds the whole-task view; injecting the CEL brief here gives the
decomposition step memory/graph/skill context + constraint blockers WITHOUT
touching the workers' deliberate context isolation (ADR-0217). Proven through the
real _build_manager_prompt composition point.

Cases:
  * iteration 0 + flag on → brief in the manager prompt; build_brief reached.
  * iteration 1 → no brief (injected once).
  * flag off → no brief.

Run: python3 operator/bridges/shared/test_acs_manager_cel.py
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

_HERE = Path(__file__).resolve().parent
for p in (_HERE, _HERE.parent.parent / "forge",
          _HERE.parent.parent.parent / "core" / "console"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

_MARKER = "TEST-ACS-MGR-CEL-88"


def _fake_ctx(iteration=0, tenant="_default"):
    return SimpleNamespace(
        workflow_spec={"workflow": {"name": "Analyse", "description": "big data task"}},
        workflow_id="wf1",
        budget=SimpleNamespace(max_loops=3, loops_used=0, max_total_tokens=0,
                               tokens_used=0, max_total_workers=4, workers_used=0,
                               max_wall_time=600, start_time=time.monotonic()),
        run_dir=Path("/tmp"), state={}, iteration=iteration,
        worker_results=[], prev_output_hash=None, tenant_id=tenant, depth=0,
        dynamic_tools={}, loss_history=[], loss_profile=None,
        m4_base_workers=0, worker_attributions=[])


class AcsManagerCelTests(unittest.TestCase):
    def setUp(self):
        os.environ["CORVIN_TENANT_ID"] = "_default"
        sys.modules.pop("acs_runtime", None)
        import acs_runtime as ar  # noqa: E402
        self.ar = ar

    def tearDown(self):
        os.environ.pop("CORVIN_TENANT_ID", None)

    def _run(self, *, iteration, flag_on, spy):
        def _flag(fid, tid="_default"):
            return flag_on if fid == "vibe_engineering" else False
        with (
            patch.object(self.ar, "_CEL_AVAILABLE", True),
            patch.object(self.ar, "_cel_build_brief", spy),
            patch.object(self.ar, "_cel_render", return_value=f"## brief\n{_MARKER}"),
            patch("corvin_console.feature_flags.is_enabled", side_effect=_flag),
        ):
            return self.ar._build_manager_prompt(_fake_ctx(iteration))

    def test_iter0_flag_on_injects(self):
        spy = MagicMock(return_value=(MagicMock(), {"stages": []}))
        prompt = self._run(iteration=0, flag_on=True, spy=spy)
        self.assertIn(_MARKER, prompt, "manager prompt must carry the CEL brief")
        self.assertTrue(spy.called)
        self.assertIn("big data task", spy.call_args[0][0])

    def test_iter1_no_brief(self):
        spy = MagicMock(return_value=(MagicMock(), {"stages": []}))
        prompt = self._run(iteration=1, flag_on=True, spy=spy)
        self.assertNotIn(_MARKER, prompt, "brief injected once, not every iteration")
        self.assertFalse(spy.called)

    def test_flag_off_no_brief(self):
        spy = MagicMock(return_value=(MagicMock(), {"stages": []}))
        prompt = self._run(iteration=0, flag_on=False, spy=spy)
        self.assertNotIn(_MARKER, prompt)
        self.assertFalse(spy.called)


if __name__ == "__main__":
    unittest.main()
