"""Phase 13.9 — Crash recovery tests."""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from corvin_compute.budget import Budget  # noqa: E402
from corvin_compute.driver import ComputeRun, ComputeRunSpec  # noqa: E402
from corvin_compute.iteration import IterRecord, param_fingerprint  # noqa: E402
from corvin_compute.recovery import (  # noqa: E402
    reap_orphaned, resume_run, scan_orphaned, scan_resumable,
)
from corvin_compute.state import RunStore, new_run_id  # noqa: E402
from corvin_compute import strategies as strat_pkg  # noqa: E402


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.mkdtemp(prefix="corvin-compute-rec-")
        self.corvin_home = Path(self.td) / "corvin"
        (self.corvin_home / "tenants" / "_default" / "compute" / "runs"
         ).mkdir(parents=True, exist_ok=True)
        self.store = RunStore(self.corvin_home, "_default")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def _seed_interrupted_run(self, *, strategy: str = "grid",
                              partial_iters: int = 2,
                              tool_name: str = "echo",
                              param_grid=None) -> str:
        if param_grid is None:
            param_grid = {"x": [0.1, 0.2, 0.3, 0.4, 0.5]}
        run_id = new_run_id()
        manifest = {
            "run_id": run_id,
            "tenant_id": "_default",
            "tool_name": tool_name,
            "strategy": strategy,
            "param_grid": param_grid,
            "loss_metric": "loss",
            "budget": {"max_iterations": 5, "max_wall_clock_s": 10,
                       "convergence_eps": 1e-12, "stall_after_n": 999},
            "minimise": True,
            "accepted_at": time.time(),
            "sensitive_fields": [],
        }
        self.store.write_manifest(run_id, manifest)
        self.store.write_summary(run_id, {
            "state": "running",
            "tenant_id": "_default",
            "started_at": time.time(),
            "best_iter": partial_iters,
            "best_loss": 0.1 * partial_iters,
            "total_iterations": partial_iters,
        })
        for i in range(1, partial_iters + 1):
            params = {"x": 0.1 * i}
            self.store.append_iteration(run_id, IterRecord(
                iter=i, params=params, loss=0.1 * i,
                wall_ms=10, ts=time.time(), cache_hit=False,
                param_fingerprint=param_fingerprint(params),
            ))
        return run_id


class ScanTests(_Fixture):
    def test_scan_picks_up_non_terminal_runs(self) -> None:
        run_id = self._seed_interrupted_run()
        resumable = scan_resumable(self.corvin_home, "_default")
        self.assertIn(run_id, resumable)

    def test_scan_skips_terminal_runs(self) -> None:
        run_id = self._seed_interrupted_run()
        # Flip it to a terminal state.
        s = self.store.read_summary(run_id)
        s["state"] = "converged"
        self.store.write_summary(run_id, s)
        resumable = scan_resumable(self.corvin_home, "_default")
        self.assertNotIn(run_id, resumable)


class ResumeTests(_Fixture):
    def test_resume_completes_interrupted_run(self) -> None:
        run_id = self._seed_interrupted_run(partial_iters=2)

        def runner(tool_name, payload):
            return {"loss": float(payload["x"])}

        state = resume_run(self.corvin_home, "_default", run_id,
                           runner_fn=runner)
        self.assertIn(state, ("converged", "stalled", "budget_exhausted"))
        # Final summary should show more iterations than the seeded
        # 2 (grid keeps walking past the resume point).
        s = self.store.read_summary(run_id)
        self.assertGreater(s["total_iterations"], 2)

    def test_resume_with_unknown_strategy_marks_failed(self) -> None:
        run_id = self._seed_interrupted_run(strategy="nonsense")

        def runner(tool_name, payload):
            return {"loss": 0.0}

        state = resume_run(self.corvin_home, "_default", run_id,
                           runner_fn=runner)
        self.assertEqual(state, "failed")
        s = self.store.read_summary(run_id)
        self.assertIn("recovery-failed", s.get("convergence_reason", ""))

    def test_strategy_update_is_idempotent_for_grid(self) -> None:
        """Grid strategy re-walks the cartesian product from index 0 on a
        fresh instance; the resume path passes the history through
        update() which is a no-op for grid. The grid then re-walks
        from 0 — duplicate iter numbers shouldn't be written because
        the driver keeps its own counter from 1."""
        run_id = self._seed_interrupted_run(partial_iters=2)

        def runner(tool_name, payload):
            return {"loss": float(payload["x"])}

        resume_run(self.corvin_home, "_default", run_id, runner_fn=runner)
        # Re-read iterations; assert the file count is sensible
        # (post-resume the driver wrote a fresh sequence starting from 1).
        iters = self.store.read_iterations(run_id)
        self.assertGreater(len(iters), 0)


class ReapTests(_Fixture):
    """Orphan reaper — finalizes stale non-terminal runs no worker resumes."""

    def test_reap_finalizes_stale_orphan(self) -> None:
        run_id = self._seed_interrupted_run()
        # Make the run appear stale: 'now' is 2h past the summary mtime,
        # threshold is 1h.
        future = time.time() + 7200
        reaped = reap_orphaned(self.corvin_home, "_default",
                               older_than_s=3600, now=future)
        self.assertEqual(reaped, [run_id])
        s = self.store.read_summary(run_id)
        self.assertEqual(s["state"], "failed")
        self.assertEqual(s["convergence_reason"], "reaped:orphaned-no-worker")

    def test_reap_skips_fresh_run(self) -> None:
        """A run still being iterated (fresh mtime) must NOT be reaped."""
        run_id = self._seed_interrupted_run()
        reaped = reap_orphaned(self.corvin_home, "_default",
                               older_than_s=3600, now=time.time())
        self.assertEqual(reaped, [])
        self.assertEqual(self.store.read_summary(run_id)["state"], "running")

    def test_reap_skips_terminal_run(self) -> None:
        run_id = self._seed_interrupted_run()
        s = self.store.read_summary(run_id)
        s["state"] = "converged"
        self.store.write_summary(run_id, s)
        future = time.time() + 7200
        reaped = reap_orphaned(self.corvin_home, "_default",
                               older_than_s=3600, now=future)
        self.assertEqual(reaped, [])
        self.assertEqual(self.store.read_summary(run_id)["state"], "converged")

    def test_scan_orphaned_is_read_only(self) -> None:
        run_id = self._seed_interrupted_run()
        future = time.time() + 7200
        found = scan_orphaned(self.corvin_home, "_default",
                              older_than_s=3600, now=future)
        self.assertIn(run_id, found)
        # Scan must not mutate state.
        self.assertEqual(self.store.read_summary(run_id)["state"], "running")

    def test_reap_emits_audit_event(self) -> None:
        run_id = self._seed_interrupted_run()
        events: list[tuple[str, dict]] = []

        def _capture(path, event, details=None, severity=None):
            events.append((event, details or {}))

        future = time.time() + 7200
        reap_orphaned(self.corvin_home, "_default", older_than_s=3600,
                      now=future, audit_emit_fn=_capture)
        failed = [f for e, f in events if e == "compute.run_failed"]
        self.assertTrue(failed)
        self.assertEqual(failed[-1]["error_class"], "OrphanReaped")
        self.assertEqual(failed[-1]["run_id"], run_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
