"""Console ACS route — daily-quota fallback (2026-07-20 maintainer decision).

POST /compute/acs/runs used to hard-402 when compute_units_per_day was spent.
Now a genuine day-limit stop (detail.reason == "quota_exceeded") degrades to
run_acs_quota_fallback (single direct Claude Code turn); the fail-closed
enforcement_unavailable 402 stays a hard error. Companion of
operator/bridges/shared/test_acs_quota_fallback_adapter.py (chokepoint) and
test_acs_quota_fallback.py (web-chat path).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))


def _rec() -> SimpleNamespace:
    return SimpleNamespace(tenant_id="_default", sid_fingerprint="fp" * 8)


def _quota_402(reason: str) -> HTTPException:
    return HTTPException(status_code=402, detail={
        "error": "license_limit", "feature": "compute_units_per_day",
        "reason": reason, "msg": "x",
        "upgrade_url": "https://corvin-labs.com/pricing",
    })


class ACSRouteQuotaFallbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CORVIN_HOME"] = self.tmp.name
        from corvin_console.routes import compute as _compute
        self.compute = _compute
        self.wf = Path(self.tmp.name) / "wf.awp.yaml"
        self.wf.write_text(
            "awp: 1.0.0\n"
            "workflow: {name: t, description: do the thing, version: 1.0.0}\n"
            "orchestration: {engine: delegation_loop, delegation_loop: {budget: {}}}\n"
            "state: {initial: {}}\n",
            encoding="utf-8",
        )
        self.body = self.compute.ACSRunRequest(workflow_path=str(self.wf))

    def tearDown(self) -> None:
        self.tmp.cleanup()
        os.environ.pop("CORVIN_HOME", None)

    def _call(self, enforce_side_effect):
        fb_result = {"run_id": "fb-1", "status": "success", "summary": "s",
                     "final_output": "out", "error": None,
                     "engine": "claude_code", "duration_s": 0.1,
                     "workflow_id": "t", "iterations": 1,
                     "workers_spawned": 0, "budget_breach": "",
                     "quota_fallback": True, "notice": "n"}
        acs_result = {"run_id": "acs-1", "status": "success", "summary": "s",
                      "error": None, "engine": "acs", "duration_s": 0.1}
        with (
            patch.object(self.compute, "_ACS_ENGINE_OK", True),
            patch("corvin_console.routes._compute_license_gate.enforce_compute_quota",
                  side_effect=enforce_side_effect),
            patch.object(self.compute, "_run_acs_quota_fallback",
                         return_value=fb_result, create=True) as fb,
            patch.object(self.compute, "_run_acs_workflow",
                         return_value=acs_result, create=True) as acs,
        ):
            out = self.compute.submit_acs_workflow_run(self.body, _rec())
        return out, fb, acs

    def test_quota_exceeded_degrades_to_fallback(self) -> None:
        out, fb, acs = self._call(_quota_402("quota_exceeded"))
        self.assertTrue(out.get("quota_fallback"))
        fb.assert_called_once()
        acs.assert_not_called()

    def test_enforcement_unavailable_stays_hard_402(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self._call(_quota_402("enforcement_unavailable"))
        self.assertEqual(ctx.exception.status_code, 402)

    def test_quota_ok_runs_acs_normally(self) -> None:
        out, fb, acs = self._call(None)
        self.assertEqual(out["engine"], "acs")
        fb.assert_not_called()
        acs.assert_called_once()
        # the route already charged; the chokepoint must not double-count
        self.assertFalse(acs.call_args.kwargs.get("charge_quota", True))


if __name__ == "__main__":
    unittest.main()
