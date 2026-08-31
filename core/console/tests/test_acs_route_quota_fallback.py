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
        # NO create=True (adversarial test-audit F2): create=True would
        # manufacture _run_acs_quota_fallback / _run_acs_workflow even if the
        # production import were deleted, masking a NameError-at-runtime. Patch
        # only what the module genuinely binds — a missing name now fails here.
        with (
            patch.object(self.compute, "_ACS_ENGINE_OK", True),
            patch("corvin_console.routes._compute_license_gate.enforce_compute_quota",
                  side_effect=enforce_side_effect),
            patch.object(self.compute, "_run_acs_quota_fallback",
                         return_value=fb_result) as fb,
            patch.object(self.compute, "_run_acs_workflow",
                         return_value=acs_result) as acs,
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

    # ── D4 (adversarial review): per-tenant fallback concurrency cap ─────────
    # submit_acs_workflow_run is synchronous; after quota exhaustion every
    # request degrades to a long-running fallback turn on an anyio worker
    # thread — without a cap, ~40 parallel requests from one free-tier tenant
    # occupy the whole threadpool. Excess requests must get the typed 429
    # immediately instead of blocking.

    def test_fallback_concurrency_limit_returns_typed_429(self) -> None:
        limit = self.compute._ACS_FB_MAX_CONCURRENT
        with self.compute._acs_fb_active_lock:
            self.compute._acs_fb_active["_default"] = limit
        self.addCleanup(self.compute._acs_fb_active.pop, "_default", None)

        with self.assertRaises(HTTPException) as ctx:
            self._call(_quota_402("quota_exceeded"))
        self.assertEqual(ctx.exception.status_code, 429)
        detail = ctx.exception.detail
        self.assertIsInstance(detail, dict)
        self.assertEqual(detail.get("reason"), "fallback_concurrency_limit")

    def test_fallback_slot_released_after_run(self) -> None:
        self._call(_quota_402("quota_exceeded"))
        self.assertEqual(self.compute._acs_fb_active.get("_default", 0), 0,
                         "fallback slot must be released after the run")

    def test_fallback_slot_released_on_failure(self) -> None:
        with (
            patch.object(self.compute, "_ACS_ENGINE_OK", True),
            patch("corvin_console.routes._compute_license_gate.enforce_compute_quota",
                  side_effect=_quota_402("quota_exceeded")),
            patch.object(self.compute, "_run_acs_quota_fallback",
                         side_effect=RuntimeError("boom")),
        ):
            with self.assertRaises(HTTPException) as ctx:
                self.compute.submit_acs_workflow_run(self.body, _rec())
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(self.compute._acs_fb_active.get("_default", 0), 0,
                         "slot must be released even when the fallback raises")

    def test_fallback_slot_released_when_submit_audit_write_raises(self) -> None:
        # D4-RESIDUAL (2026-07-20): the acs.run_submit audit write used to sit
        # BETWEEN the slot acquire and the try/finally. A hash-chain I/O error
        # there leaked the slot permanently (two leaks pinned the tenant at
        # 429). The write now lives inside the try, so the finally releases the
        # slot on this path too.
        self.assertEqual(self.compute._acs_fb_active.get("_default", 0), 0)
        with (
            patch.object(self.compute, "_ACS_ENGINE_OK", True),
            patch("corvin_console.routes._compute_license_gate.enforce_compute_quota",
                  side_effect=_quota_402("quota_exceeded")),
            patch.object(self.compute, "_run_acs_quota_fallback",
                         return_value={"run_id": "x", "status": "success"}),
            patch.object(self.compute.console_audit, "action_performed",
                         side_effect=RuntimeError("audit chain I/O error")),
            patch.object(self.compute.console_audit, "action_failed"),
        ):
            with self.assertRaises(HTTPException) as ctx:
                self.compute.submit_acs_workflow_run(self.body, _rec())
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(self.compute._acs_fb_active.get("_default", 0), 0,
                         "slot must be released even when the submit audit write raises")

    def test_concurrent_fallbacks_beyond_limit_get_429_immediately(self) -> None:
        import threading

        limit = self.compute._ACS_FB_MAX_CONCURRENT
        release = threading.Event()
        inside = threading.Semaphore(0)

        def _blocking_fb(*a, **kw):
            inside.release()
            release.wait(timeout=10)
            return {"run_id": "fb", "status": "success", "quota_fallback": True}

        results: list[object] = []

        def _submit():
            try:
                with (
                    patch.object(self.compute, "_ACS_ENGINE_OK", True),
                    patch("corvin_console.routes._compute_license_gate."
                          "enforce_compute_quota",
                          side_effect=_quota_402("quota_exceeded")),
                    patch.object(self.compute, "_run_acs_quota_fallback",
                                 side_effect=_blocking_fb),
                ):
                    results.append(
                        self.compute.submit_acs_workflow_run(self.body, _rec()))
            except HTTPException as exc:
                results.append(exc)

        threads = [threading.Thread(target=_submit) for _ in range(limit)]
        for t in threads:
            t.start()
        try:
            for _ in range(limit):
                self.assertTrue(inside.acquire(timeout=10),
                                "in-flight fallbacks did not start")
            # limit runs are in flight and BLOCKED — the next request must not
            # queue behind them but fail fast with the typed 429.
            with self.assertRaises(HTTPException) as ctx:
                self._submit_expect_raise()
            self.assertEqual(ctx.exception.status_code, 429)
        finally:
            release.set()
            for t in threads:
                t.join(timeout=10)
        ok = [r for r in results if isinstance(r, dict)]
        self.assertEqual(len(ok), limit,
                         "the in-flight fallbacks must complete normally")
        self.assertEqual(self.compute._acs_fb_active.get("_default", 0), 0)

    def _submit_expect_raise(self):
        with (
            patch.object(self.compute, "_ACS_ENGINE_OK", True),
            patch("corvin_console.routes._compute_license_gate.enforce_compute_quota",
                  side_effect=_quota_402("quota_exceeded")),
            patch.object(self.compute, "_run_acs_quota_fallback",
                         return_value={"run_id": "x", "status": "success"}),
        ):
            return self.compute.submit_acs_workflow_run(self.body, _rec())


if __name__ == "__main__":
    unittest.main()
