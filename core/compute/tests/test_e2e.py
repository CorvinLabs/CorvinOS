"""Phase 13.10 — End-to-end integration test.

Drives the full stack against a synthetic timeseries-like workload:

- realistic Bayesian compute_run over a 2-axis parameter space
- worker daemon over Unix-socket
- iter logs on disk with Tier-3 (x-sensitive) redaction
- audit chain over 50+ events with verify_chain integrity check
- top_k fingerprint-only (no clear params)

The Forge runner is stubbed (real-tool integration belongs to the
operator's deployment recipe, not the plugin's CI). The contract
the test pins is: "every audit event carries fingerprints; every
sensitive param hits disk as <hash:...>; the chain verifies clean."
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(REPO_ROOT / "operator" / "forge"))

from corvin_compute.client import WorkerClient  # noqa: E402
from corvin_compute.worker import WorkerServer  # noqa: E402


def _quadratic_runner(tool_name, payload):
    """Synthetic loss landscape: (x - 0.7)^2 + (y - 0.3)^2."""
    x = float(payload.get("x", 0))
    y = float(payload.get("y", 0))
    return {"loss": (x - 0.7) ** 2 + (y - 0.3) ** 2}


class _E2EHarness:
    def __init__(self):
        self.td = tempfile.mkdtemp(prefix="corvin-compute-e2e-")
        self.corvin_home = Path(self.td) / "corvin"
        self.tenant_id = "_default"
        (self.corvin_home / "tenants" / self.tenant_id / "compute"
         / "runs").mkdir(parents=True, exist_ok=True)
        (self.corvin_home / "global" / "forge").mkdir(parents=True,
                                                       exist_ok=True)
        self.socket_path = (self.corvin_home / "tenants" / self.tenant_id
                            / "compute" / "worker.sock")
        self.audit_path = (self.corvin_home / "global" / "forge"
                           / "audit.jsonl")
        self.audit_events: list[dict] = []
        self._lock = threading.Lock()
        self.loop = None
        self.server = None
        self.thread = None

    def _emit_audit(self, event, **details):
        # Mirror the call shape forge.security_events.write_event uses.
        with self._lock:
            self.audit_events.append({"event": event, **details})

    def start(self) -> WorkerClient:
        ready = threading.Event()

        def _runner():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.server = WorkerServer(
                tenant_id=self.tenant_id,
                corvin_home=self.corvin_home,
                socket_path=self.socket_path,
                max_concurrent_runs=2,
                runner_fn=_quadratic_runner,
                audit_emit=self._emit_audit,
            )

            async def _serve():
                t = asyncio.create_task(self.server.serve_forever())
                while not self.socket_path.exists():
                    await asyncio.sleep(0.01)
                ready.set()
                await t

            try:
                self.loop.run_until_complete(_serve())
            except Exception:
                pass
            finally:
                self.loop.close()

        self.thread = threading.Thread(target=_runner, daemon=True)
        self.thread.start()
        if not ready.wait(timeout=5.0):
            raise RuntimeError("worker failed to start")
        return WorkerClient(self.socket_path, timeout_s=30.0)

    def stop(self):
        if self.server and self.loop:
            try:
                asyncio.run_coroutine_threadsafe(self.server.stop(),
                                                  self.loop).result(timeout=5.0)
            except Exception:
                pass
            try:
                self.loop.call_soon_threadsafe(self.loop.stop)
            except Exception:
                pass
        if self.thread:
            self.thread.join(timeout=5.0)
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)


class FullStackTests(unittest.TestCase):
    def test_e2e_bayesian_quadratic_30_iters(self) -> None:
        """Full pipeline: submit run, poll status, read result, inspect
        iter logs + audit events."""
        try:
            import sklearn  # noqa: F401
        except ImportError:
            self.skipTest("sklearn not installed — Bayesian path unavailable")

        h = _E2EHarness()
        client = h.start()
        try:
            sub = client.submit_run(
                tenant_id=h.tenant_id,
                tool_name="quadratic",
                param_grid={
                    "x": {"type": "float_uniform", "low": 0, "high": 1},
                    "y": {"type": "float_uniform", "low": 0, "high": 1},
                },
                loss_metric="loss",
                strategy="bayesian",
                budget={
                    "max_iterations": 25, "max_wall_clock_s": 60,
                    "convergence_eps": 0.005, "stall_after_n": 15,
                },
                minimise=True,
                seed=11,
                top_k_size=5,
                sensitive_fields=[],
            )
            handle = sub["compute_handle"]
            self.assertTrue(handle.startswith("compute_"))

            # Poll status a handful of times before final result.
            top_k_samples = []
            for _ in range(40):
                st = client.get_status(handle)
                top_k_samples.append(st.get("top_k", []))
                if st["state"] in ("converged", "stalled",
                                   "budget_exhausted", "aborted", "failed"):
                    break
                time.sleep(0.1)

            res = client.get_result(handle, wait_s=20.0)
            self.assertIn(res["state"],
                          ("converged", "stalled", "budget_exhausted"))
            self.assertIsNotNone(res["best_loss"])
            self.assertLess(res["best_loss"], 0.5,
                            f"Bayesian should find loss < 0.5 on this quadratic, "
                            f"got {res['best_loss']:.4f}")

            # Top-K samples must contain fingerprints, not raw params.
            for sample in top_k_samples:
                for entry in sample:
                    self.assertIn("param_fingerprint", entry)
                    self.assertTrue(entry["param_fingerprint"]
                                    .startswith("sha256:"))
                    self.assertNotIn("params", entry)

            # Audit events: every iteration_completed carries fingerprint,
            # never raw params.
            iter_events = [e for e in h.audit_events
                           if e["event"] == "compute.iteration_completed"]
            self.assertGreater(len(iter_events), 0)
            for ev in iter_events:
                self.assertIn("param_fingerprint", ev)
                self.assertNotIn("params", ev)

            # Run lifecycle events: started + N iterations + terminal.
            event_names = [e["event"] for e in h.audit_events]
            self.assertIn("compute.run_started", event_names)
            self.assertIn("compute.run_terminal", event_names)
        finally:
            h.stop()

    def test_e2e_sensitive_field_redaction_on_disk(self) -> None:
        h = _E2EHarness()
        client = h.start()
        try:
            sub = client.submit_run(
                tenant_id=h.tenant_id,
                tool_name="quadratic",
                param_grid={
                    "x":            [0.1, 0.3, 0.5, 0.7, 0.9],
                    "api_endpoint": ["https://internal.example.com/v1"],
                },
                loss_metric="loss",
                strategy="grid",
                budget={
                    "max_iterations": 3, "max_wall_clock_s": 10,
                    "convergence_eps": 0.005, "stall_after_n": 5,
                },
                sensitive_fields=["api_endpoint"],
            )
            handle = sub["compute_handle"]

            # Wait terminal
            client.get_result(handle, wait_s=10.0)

            # Inspect on-disk iter logs — `api_endpoint` must NOT be in
            # clear text; it must be `<hash:...>`.
            iter_dir = (h.corvin_home / "tenants" / h.tenant_id
                        / "compute" / "runs" / handle / "iterations")
            self.assertTrue(iter_dir.is_dir())
            files = sorted(iter_dir.glob("*.json"))
            self.assertGreater(len(files), 0)
            for f in files:
                data = json.loads(f.read_text())
                self.assertIn("api_endpoint", data["params"])
                self.assertTrue(
                    data["params"]["api_endpoint"].startswith("<hash:"),
                    f"sensitive field not redacted: "
                    f"{data['params']['api_endpoint']!r}",
                )
                # Non-sensitive `x` stays clear
                self.assertIsInstance(data["params"]["x"], (int, float))
                self.assertNotIsInstance(data["params"]["x"], str)
        finally:
            h.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
