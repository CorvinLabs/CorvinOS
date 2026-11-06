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
    ResumePlan, prepare_resume, reap_orphaned, resume_run, scan_orphaned, scan_resumable,
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

    def test_resume_continues_after_the_recorded_iterations(self) -> None:
        """A restarted worker used to walk the grid from point 0 and overwrite the
        iteration files it had already written (adversarial review 2026-11-06).
        Now the recorded iterations seed the run: nothing is re-run or rewritten."""
        run_id = self._seed_interrupted_run(partial_iters=2)
        before = {p.name: p.read_bytes() for p in
                  (self.store.root / run_id / "iterations").glob("*.json")}
        seen: list[float] = []

        def runner(tool_name, payload):
            seen.append(float(payload["x"]))
            return {"loss": float(payload["x"])}

        events: list[str] = []
        resume_run(self.corvin_home, "_default", run_id, runner_fn=runner,
                   run_audit_emit=lambda ev, **kw: events.append(ev))
        self.assertEqual(seen, [0.3, 0.4, 0.5], "only the points after the recorded ones run")
        after = {p.name: p.read_bytes() for p in
                 (self.store.root / run_id / "iterations").glob("*.json")}
        self.assertEqual(sorted(after), ["0001.json", "0002.json", "0003.json", "0004.json", "0005.json"])
        for name, raw in before.items():
            self.assertEqual(after[name], raw, f"{name} was overwritten by the resume")
        s = self.store.read_summary(run_id)
        self.assertEqual(s["total_iterations"], 5)
        self.assertEqual(s["best_loss"], 0.1)
        self.assertEqual(events.count("compute.iteration_completed"), 3,
                         "a recovered run's own events reach the audit emitter")

    def test_a_run_resumed_again_and_again_without_progress_is_failed(self) -> None:
        """A run that crashes the worker on every start must not loop forever under
        Restart=on-failure: after 3 consecutive resumes with no new iteration it is failed."""
        run_id = self._seed_interrupted_run(partial_iters=2)

        def runner(t, p):
            return {"loss": 1.0}

        for attempt in (1, 2, 3):
            plan = prepare_resume(self.corvin_home, "_default", run_id, runner)
            self.assertIsInstance(plan, ResumePlan, f"attempt {attempt}")
        self.assertEqual(prepare_resume(self.corvin_home, "_default", run_id, runner), "failed")
        self.assertIn("resume-attempts-exceeded",
                      self.store.read_summary(run_id)["convergence_reason"])

    def test_progress_between_resumes_resets_the_attempt_counter(self) -> None:
        run_id = self._seed_interrupted_run(partial_iters=2)
        runner = lambda t, p: {"loss": 1.0}  # noqa: E731
        for _ in range(3):
            self.assertIsInstance(prepare_resume(self.corvin_home, "_default", run_id, runner), ResumePlan)
        params = {"x": 0.3}
        self.store.append_iteration(run_id, IterRecord(
            iter=3, params=params, loss=0.3, wall_ms=1, ts=time.time(),
            param_fingerprint=param_fingerprint(params)))
        self.assertIsInstance(prepare_resume(self.corvin_home, "_default", run_id, runner), ResumePlan)

    def test_the_resume_is_on_the_audit_chain_and_an_empty_history_keeps_the_manifest(self) -> None:
        """``compute.run_recovering`` was never emitted by the worker (no emitter was passed);
        a run interrupted before its first iteration rewrote its manifest and emitted a second
        ``compute.run_started``."""
        run_id = self._seed_interrupted_run(partial_iters=0)
        accepted = self.store.read_manifest(run_id)["accepted_at"]
        events: list[tuple] = []
        resume_run(self.corvin_home, "_default", run_id, runner_fn=lambda t, p: {"loss": float(p["x"])},
                   run_audit_emit=lambda ev, **kw: events.append((ev, kw)))
        names = [e for e, _ in events]
        self.assertEqual(names.count("compute.run_recovering"), 1, names)
        self.assertNotIn("compute.run_started", names)
        rec = next(kw for e, kw in events if e == "compute.run_recovering")
        self.assertEqual((rec["resume_from_iter"], rec["history_size"]), (1, 0))
        self.assertEqual(self.store.read_manifest(run_id)["accepted_at"], accepted)

    def test_the_iteration_budget_counts_the_recorded_iterations(self) -> None:
        run_id = self._seed_interrupted_run(partial_iters=4)
        m = self.store.read_manifest(run_id)
        m["budget"]["max_iterations"] = 4
        self.store.write_manifest(run_id, m)
        calls: list = []
        state = resume_run(self.corvin_home, "_default", run_id,
                           runner_fn=lambda t, p: calls.append(p) or {"loss": 1.0})
        self.assertEqual(calls, [], "the budget was already spent before the restart")
        self.assertEqual(state, "budget_exhausted")

    def test_the_wall_clock_budget_is_cumulative_across_the_restart(self) -> None:
        run_id = self._seed_interrupted_run(partial_iters=2)
        s = self.store.read_summary(run_id)
        s["total_wall_s"] = 9999.0          # budget is max_wall_clock_s=10
        self.store.write_summary(run_id, s)
        calls: list = []
        state = resume_run(self.corvin_home, "_default", run_id,
                           runner_fn=lambda t, p: calls.append(p) or {"loss": 1.0})
        self.assertEqual((state, calls), ("budget_exhausted", []))
        self.assertEqual(self.store.read_summary(run_id)["convergence_reason"], "max-wall-clock-reached")

    def test_a_seeded_random_run_continues_its_sequence(self) -> None:
        grid = {"x": {"type": "float_uniform", "low": 0.0, "high": 1.0}}

        def run_to(n: int, seed: int = 7) -> list[float]:
            rid = new_run_id()
            spec = ComputeRunSpec(tenant_id="_default", tool_name="t", param_grid=grid, loss_metric="loss",
                                  strategy_name="random", seed=seed,
                                  budget=Budget.from_dict({"max_iterations": n, "max_wall_clock_s": 30,
                                                           "stall_after_n": 999, "convergence_eps": 0}))
            ComputeRun(spec, corvin_home=self.corvin_home, runner_fn=lambda t, p: {"loss": p["x"]},
                       strategy_factory=strat_pkg.load_strategy, run_id=rid).run()
            return rid

        full = run_to(8)
        part = run_to(4)
        s = self.store.read_summary(part)
        s["state"] = "running"
        self.store.write_summary(part, s)
        m = self.store.read_manifest(part)
        m["budget"]["max_iterations"] = 8
        self.store.write_manifest(part, m)
        calls: list = []
        resume_run(self.corvin_home, "_default", part,
                   runner_fn=lambda t, p: calls.append(p["x"]) or {"loss": p["x"]})
        self.assertEqual(len(calls), 4, "only the 4 missing points run (the old code re-ran all 8)")
        xs = lambda rid: [round(r.params["x"], 12) for r in self.store.read_iterations(rid)]  # noqa: E731
        self.assertEqual(xs(part), xs(full), "resume must continue the seeded sequence, not repeat it")

    def test_the_datasource_binding_survives_a_restart(self) -> None:
        rid = new_run_id()
        spec = ComputeRunSpec(tenant_id="_default", tool_name="t", param_grid={"x": [1, 2, 3]}, loss_metric="loss",
                              strategy_name="grid", datasources=["conn-a"],
                              budget=Budget.from_dict({"max_iterations": 1, "max_wall_clock_s": 30}))
        ComputeRun(spec, corvin_home=self.corvin_home, runner_fn=lambda t, p: {"loss": 1.0},
                   strategy_factory=strat_pkg.load_strategy, run_id=rid).run()
        self.assertEqual(self.store.read_manifest(rid)["datasources"], ["conn-a"])
        s = self.store.read_summary(rid)
        s["state"] = "running"
        self.store.write_summary(rid, s)
        m = self.store.read_manifest(rid)
        m["budget"]["max_iterations"] = 3
        self.store.write_manifest(rid, m)
        got: list = []

        def runner(tool_name, payload, datasources=None):
            got.append(datasources)
            return {"loss": 1.0}

        resume_run(self.corvin_home, "_default", rid, runner_fn=runner)
        self.assertEqual(got, [["conn-a"], ["conn-a"]])


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
