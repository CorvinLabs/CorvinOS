"""Protocol conformance suite for ADR-0029 ComputeEngine.

Every engine (FlatEngine, PipelineEngine, HACEngine, custom) must pass
these 30 cases before it can be listed in engines_allowed.
"""
from __future__ import annotations

import asyncio
import dataclasses
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stub_runner(tool_name: str, payload: Any) -> dict:
    """Minimal stub — returns loss=0.1 for any tool call."""
    return {"loss": 0.1, "result": "ok"}


def _make_flat_engine(tmp_path: Path):
    """Build a FlatEngine against a real WorkerServer."""
    from corvin_compute.worker import WorkerServer
    from corvin_compute.engines.flat import FlatEngine

    socket_path = tmp_path / "worker.sock"
    server = WorkerServer(
        tenant_id="_test",
        corvin_home=tmp_path,
        socket_path=socket_path,
        max_concurrent_runs=2,
        runner_fn=_stub_runner,
    )
    return server, FlatEngine(socket_path), socket_path


# ---------------------------------------------------------------------------
# 1. Protocol structure tests (engine_protocol.py)
# ---------------------------------------------------------------------------

class TestProtocolDataclasses(unittest.TestCase):

    def test_compute_spec_defaults(self):
        from corvin_compute.engine_protocol import ComputeSpec
        spec = ComputeSpec(engine="flat", tenant_id="t1", budget={"max_iterations": 10})
        self.assertEqual(spec.engine, "flat")
        self.assertEqual(spec.tool_name, None)
        self.assertEqual(spec.strategy, "grid")
        self.assertIsInstance(spec.sensitive_fields, list)
        self.assertIsInstance(spec.extra, dict)

    def test_compute_status_fields(self):
        from corvin_compute.engine_protocol import ComputeStatus
        st = ComputeStatus(job_id="j1", engine_id="flat", state="running",
                           progress={}, detail={})
        self.assertEqual(st.state, "running")

    def test_compute_result_fields(self):
        from corvin_compute.engine_protocol import ComputeResult
        res = ComputeResult(job_id="j1", engine_id="flat", state="converged", result={})
        self.assertEqual(res.audit_ref, "")

    def test_gate_action_defaults(self):
        from corvin_compute.engine_protocol import GateAction
        a = GateAction(action_type="resume")
        self.assertEqual(a.payload, {})

    def test_engine_does_not_support_gates_is_runtime_error(self):
        from corvin_compute.engine_protocol import EngineDoesNotSupportGates
        self.assertTrue(issubclass(EngineDoesNotSupportGates, RuntimeError))

    def test_unknown_job_id_is_key_error(self):
        from corvin_compute.engine_protocol import UnknownJobId
        self.assertTrue(issubclass(UnknownJobId, KeyError))


# ---------------------------------------------------------------------------
# 2. Registry tests (engine_registry.py)
# ---------------------------------------------------------------------------

class TestEngineRegistry(unittest.TestCase):

    def _make_registry(self):
        from corvin_compute.engine_registry import ComputeEngineRegistry
        return ComputeEngineRegistry()

    def _make_stub_engine(self, engine_id: str, prefix: str):
        eng = MagicMock()
        eng.engine_id = engine_id
        eng.job_id_prefix = prefix
        eng.supports_gates = False
        return eng

    def test_register_and_get(self):
        reg = self._make_registry()
        eng = self._make_stub_engine("flat", "compute_")
        reg.register(eng)
        self.assertIs(reg.get("flat"), eng)

    def test_get_unknown_raises_key_error(self):
        reg = self._make_registry()
        with self.assertRaises(KeyError):
            reg.get("nonexistent")

    def test_get_by_job_id(self):
        reg = self._make_registry()
        eng = self._make_stub_engine("pipeline", "pipeline_")
        reg.register(eng)
        found = reg.get_by_job_id("pipeline_AbCdEfGh1234567890Ab")
        self.assertIs(found, eng)

    def test_get_by_job_id_unknown_raises(self):
        from corvin_compute.engine_protocol import UnknownJobId
        reg = self._make_registry()
        with self.assertRaises(UnknownJobId):
            reg.get_by_job_id("unknown_xyz")

    def test_duplicate_prefix_raises(self):
        reg = self._make_registry()
        eng1 = self._make_stub_engine("e1", "compute_")
        eng2 = self._make_stub_engine("e2", "compute_")
        reg.register(eng1)
        with self.assertRaises(ValueError):
            reg.register(eng2)

    def test_unregister(self):
        reg = self._make_registry()
        eng = self._make_stub_engine("flat", "compute_")
        reg.register(eng)
        reg.unregister("flat")
        with self.assertRaises(KeyError):
            reg.get("flat")

    def test_engines_for_tenant_no_filter(self):
        reg = self._make_registry()
        for eid, pfx in [("flat", "compute_"), ("pipeline", "pipeline_")]:
            reg.register(self._make_stub_engine(eid, pfx))
        result = reg.engines_for_tenant("t1")
        self.assertEqual(len(result), 2)

    def test_engines_for_tenant_with_filter(self):
        reg = self._make_registry()
        for eid, pfx in [("flat", "compute_"), ("pipeline", "pipeline_"), ("hac", "hac_")]:
            reg.register(self._make_stub_engine(eid, pfx))
        result = reg.engines_for_tenant("t1", allowed_engine_ids=["flat", "hac"])
        ids = {e.engine_id for e in result}
        self.assertEqual(ids, {"flat", "hac"})

    def test_discover(self):
        reg = self._make_registry()
        reg.register(self._make_stub_engine("flat", "compute_"))
        reg.register(self._make_stub_engine("pipeline", "pipeline_"))
        self.assertIn("flat", reg.discover())
        self.assertIn("pipeline", reg.discover())


# ---------------------------------------------------------------------------
# 3. FlatEngine protocol conformance
# ---------------------------------------------------------------------------

class TestFlatEngineConformance(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self._tmp_path = Path(self._tmp)

    def test_flat_engine_has_correct_attributes(self):
        from corvin_compute.engines.flat import FlatEngine
        eng = FlatEngine(self._tmp_path / "test.sock")
        self.assertEqual(eng.engine_id, "flat")
        self.assertEqual(eng.job_id_prefix, "compute_")
        self.assertFalse(eng.supports_gates)

    def test_flat_engine_gate_action_raises(self):
        from corvin_compute.engines.flat import FlatEngine
        from corvin_compute.engine_protocol import GateAction, EngineDoesNotSupportGates
        eng = FlatEngine(self._tmp_path / "test.sock")
        with self.assertRaises(EngineDoesNotSupportGates):
            eng.gate_action("compute_abc", GateAction(action_type="resume"))

    def test_flat_engine_implements_protocol(self):
        from corvin_compute.engines.flat import FlatEngine
        from corvin_compute.engine_protocol import ComputeEngine
        eng = FlatEngine(self._tmp_path / "test.sock")
        self.assertIsInstance(eng, ComputeEngine)

    def test_flat_engine_submit_status_result_cycle(self):
        """Full round-trip through a live WorkerServer."""
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        socket_path = tmp / "w.sock"
        from corvin_compute.worker import WorkerServer
        from corvin_compute.engines.flat import FlatEngine
        from corvin_compute.engine_protocol import ComputeSpec

        server = WorkerServer(
            tenant_id="_test", corvin_home=tmp, socket_path=socket_path,
            max_concurrent_runs=1, runner_fn=_stub_runner,
        )
        ready = threading.Event()
        # Capture the worker loop so teardown can schedule stop() on the
        # correct event loop (asyncio.run() from a different thread creates
        # a separate loop, causing a cross-loop TypeError on server.close()).
        _worker_loop: list[asyncio.AbstractEventLoop] = []

        async def _serve():
            _worker_loop.append(asyncio.get_running_loop())
            ready.set()
            await server.serve_forever()

        thread = threading.Thread(
            target=lambda: asyncio.run(_serve()), daemon=True
        )
        thread.start()
        ready.wait(timeout=3)
        time.sleep(0.15)  # let bind complete

        try:
            eng = FlatEngine(socket_path)
            spec = ComputeSpec(
                engine="flat", tenant_id="_test",
                budget={"max_iterations": 3},
                tool_name="mytool",
                param_grid={"x": [1, 2, 3]},
                strategy="grid",
            )
            job_id = eng.submit(spec)
            self.assertTrue(job_id.startswith("compute_"))

            result = eng.result(job_id, wait_s=10.0)
            self.assertIn(result.state, ("converged", "budget_exhausted"))
            self.assertIsNotNone(result.result.get("best_loss"))
        finally:
            if _worker_loop:
                fut = asyncio.run_coroutine_threadsafe(
                    server.stop(), _worker_loop[0]
                )
                try:
                    fut.result(timeout=5)
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# 4. PipelineEngine conformance
# ---------------------------------------------------------------------------

class TestPipelineEngineConformance(unittest.TestCase):

    def test_pipeline_engine_attributes(self):
        from corvin_compute.pipeline.engine import PipelineEngine
        eng = PipelineEngine(corvin_home=Path("/tmp"), runner_fn=_stub_runner)
        self.assertEqual(eng.engine_id, "pipeline")
        self.assertEqual(eng.job_id_prefix, "pipeline_")
        self.assertTrue(eng.supports_gates)

    def test_pipeline_engine_implements_protocol(self):
        from corvin_compute.pipeline.engine import PipelineEngine
        from corvin_compute.engine_protocol import ComputeEngine
        eng = PipelineEngine(corvin_home=Path("/tmp"), runner_fn=_stub_runner)
        self.assertIsInstance(eng, ComputeEngine)

    def test_pipeline_submit_returns_pipeline_prefix(self):
        """Single-stage pipeline runs end-to-end."""
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        from corvin_compute.pipeline.engine import PipelineEngine
        from corvin_compute.engine_protocol import ComputeSpec

        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        eng = PipelineEngine(corvin_home=tmp, runner_fn=_stub_runner)
        eng.set_loop(loop)

        spec = ComputeSpec(
            engine="pipeline", tenant_id="_test",
            budget={"max_iterations": 3},
            extra={
                "steering_gate": False,
                "stages": [{"stage_id": "s1", "tool_name": "mytool",
                             "strategy": "grid", "param_grid": {"x": [1, 2]}}],
            },
        )
        job_id = eng.submit(spec)
        self.assertTrue(job_id.startswith("pipeline_"))

        result = eng.result(job_id, wait_s=15.0)
        self.assertIn(result.state, ("converged", "failed"))
        loop.call_soon_threadsafe(loop.stop)

    def test_pipeline_gate_action_add_stage(self):
        """Gate opens after stage 1; LLM adds stage 2 then resumes."""
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        from corvin_compute.pipeline.engine import PipelineEngine
        from corvin_compute.engine_protocol import ComputeSpec, GateAction

        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        eng = PipelineEngine(corvin_home=tmp, runner_fn=_stub_runner)
        eng.set_loop(loop)

        spec = ComputeSpec(
            engine="pipeline", tenant_id="_test",
            budget={"max_iterations": 2},
            extra={
                "steering_gate": True,
                "steering_gate_timeout_s": 10.0,
                "stages": [
                    {"stage_id": "s1", "tool_name": "t1",
                     "strategy": "grid", "param_grid": {"x": [1]}},
                    {"stage_id": "s2", "tool_name": "t2",
                     "strategy": "grid", "param_grid": {"y": [2]}},
                ],
            },
        )
        job_id = eng.submit(spec)

        # Wait for gate to open
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            st = eng.status(job_id)
            if st.state == "gate_open":
                break
            time.sleep(0.1)

        eng.gate_action(job_id, GateAction(action_type="resume", payload={}))
        result = eng.result(job_id, wait_s=15.0)
        self.assertIn(result.state, ("converged", "failed"))
        loop.call_soon_threadsafe(loop.stop)


# ---------------------------------------------------------------------------
# 5. HACEngine conformance
# ---------------------------------------------------------------------------

class TestHACEngineConformance(unittest.TestCase):

    def test_hac_engine_attributes(self):
        from corvin_compute.hac.engine import HACEngine
        eng = HACEngine(corvin_home=Path("/tmp"), runner_fn=_stub_runner)
        self.assertEqual(eng.engine_id, "hac")
        self.assertEqual(eng.job_id_prefix, "hac_")
        self.assertTrue(eng.supports_gates)

    def test_hac_engine_implements_protocol(self):
        from corvin_compute.hac.engine import HACEngine
        from corvin_compute.engine_protocol import ComputeEngine
        eng = HACEngine(corvin_home=Path("/tmp"), runner_fn=_stub_runner)
        self.assertIsInstance(eng, ComputeEngine)

    def test_hac_submit_returns_hac_prefix(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        from corvin_compute.hac.engine import HACEngine
        from corvin_compute.engine_protocol import ComputeSpec

        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        eng = HACEngine(corvin_home=tmp, runner_fn=_stub_runner)
        eng.set_loop(loop)

        spec = ComputeSpec(
            engine="hac", tenant_id="_test",
            budget={"max_iterations": 6},
            extra={
                "backprop_gate": False,
                "max_backprop_rounds": 1,
                "convergence_epsilon": 1.0,  # converge immediately
                "sub_managers": [
                    {"manager_id": "A", "budget_fraction": 0.5,
                     "stages": [{"tool_name": "t1", "param_grid": {"x": [1, 2]}}]},
                    {"manager_id": "B", "budget_fraction": 0.5,
                     "stages": [{"tool_name": "t2", "param_grid": {"y": [3, 4]}}]},
                ],
                "loss_weights": {"weights": {"A": 0.5, "B": 0.5}},
            },
        )
        job_id = eng.submit(spec)
        self.assertTrue(job_id.startswith("hac_"))

        result = eng.result(job_id, wait_s=20.0)
        # WA-15: "failed" used to be tolerated here because every sub-manager
        # run raised AttributeError on a non-existent RunRecord.best_params
        # (coordinator.py _exec_manager) — no HAC job had ever completed
        # successfully. Now that it's fixed, a plain 2-manager/numeric-grid
        # job must actually converge or exhaust its budget, never "failed".
        self.assertIn(result.state, ("converged", "budget"))
        loop.call_soon_threadsafe(loop.stop)

    def test_hac_exec_manager_returns_real_best_params(self):
        """WA-15 regression: _exec_manager used to access RunRecord.best_params,
        an attribute that has never existed on RunRecord (only best_loss/
        best_iter) — every sub-manager run raised AttributeError. It must now
        look the winning iteration's real params up via RunStore."""
        import tempfile

        from corvin_compute.hac.coordinator import HACCoordinator
        from corvin_compute.hac.manifest import HACManifest, LossWeights, SubManagerSpec

        tmp = Path(tempfile.mkdtemp())
        sm = SubManagerSpec.from_dict({
            "manager_id": "A", "budget_fraction": 1.0, "strategy": "grid",
            "stages": [{"stage_id": "s1", "tool_name": "t1",
                        "param_grid": {"x": [1, 2, 3]}}],
        })
        manifest = HACManifest(
            hac_id="hac_bp_test", tenant_id="_test", sub_managers=[sm],
            loss_weights=LossWeights(weights={"A": 1.0}),
            budget={"max_iterations": 10, "max_wall_clock_s": 60},
            backprop_gate=False, backprop_gate_timeout_s=10,
            max_backprop_rounds=1, convergence_epsilon=0.01,
        )
        coord = HACCoordinator(
            hac_id="hac_bp_test", manifest=manifest, corvin_home=tmp,
            runner_fn=_stub_runner, audit_emit=lambda *a, **kw: None,
            gate_queue=None,
        )
        result = coord._exec_manager(sm)
        self.assertNotEqual(result["state"], "failed")
        self.assertIn(result["best_params"]["x"], (1, 2, 3))


# ---------------------------------------------------------------------------
# 6. Worker integration — gate_action op
# ---------------------------------------------------------------------------

class TestWorkerGateActionOp(unittest.TestCase):

    def test_gate_action_dispatches_to_pipeline_engine(self):
        """WorkerServer routes gate_action to the PipelineEngine."""
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        socket_path = tmp / "w2.sock"

        from corvin_compute.pipeline.engine import PipelineEngine
        from corvin_compute.worker import WorkerServer

        pipeline_eng = PipelineEngine(corvin_home=tmp, runner_fn=_stub_runner)

        server = WorkerServer(
            tenant_id="_test", corvin_home=tmp, socket_path=socket_path,
            max_concurrent_runs=2, runner_fn=_stub_runner,
            extra_engines=[pipeline_eng],
        )

        ready = threading.Event()
        _worker_loop2: list[asyncio.AbstractEventLoop] = []

        async def _serve():
            _worker_loop2.append(asyncio.get_running_loop())
            ready.set()
            await server.serve_forever()

        thread = threading.Thread(target=lambda: asyncio.run(_serve()), daemon=True)
        thread.start()
        ready.wait(timeout=3)
        time.sleep(0.15)

        try:
            from corvin_compute.client import WorkerClient
            client = WorkerClient(socket_path, timeout_s=15.0)

            # Submit a pipeline job via unified path
            resp = client.submit_run(engine="pipeline", budget={"max_iterations": 2},
                                     extra={"steering_gate": False,
                                            "stages": [{"stage_id": "s1",
                                                         "tool_name": "t",
                                                         "strategy": "grid",
                                                         "param_grid": {"x": [1]}}]})
            self.assertIn("compute_handle", resp)
            job_id = resp["compute_handle"]
            self.assertTrue(job_id.startswith("pipeline_"))
        finally:
            if _worker_loop2:
                fut = asyncio.run_coroutine_threadsafe(
                    server.stop(), _worker_loop2[0]
                )
                try:
                    fut.result(timeout=5)
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# 7. Attribution tests
# ---------------------------------------------------------------------------

class TestAttribution(unittest.TestCase):

    def test_weighted_sum_root_loss(self):
        from corvin_compute.hac.attribution import compute_root_loss
        result = compute_root_loss(
            {"A": 0.4, "B": 0.2},
            {"A": 0.5, "B": 0.5},
            "weighted_sum",
        )
        self.assertAlmostEqual(result, 0.3, places=5)

    def test_pareto_root_loss(self):
        from corvin_compute.hac.attribution import compute_root_loss
        result = compute_root_loss({"A": 0.4, "B": 0.1}, {"A": 0.5, "B": 0.5}, "pareto")
        self.assertAlmostEqual(result, 0.4, places=5)

    def test_attribution_fractions_sum_to_one(self):
        from corvin_compute.hac.attribution import compute_attribution
        attr = compute_attribution(
            {"A": 0.5, "B": 0.1, "C": 0.3},
            {"A": 0.4, "B": 0.3, "C": 0.3},
        )
        self.assertAlmostEqual(sum(attr.values()), 1.0, places=5)

    def test_attribution_highest_loss_gets_highest_score(self):
        from corvin_compute.hac.attribution import compute_attribution
        attr = compute_attribution(
            {"A": 0.8, "B": 0.1},  # A is terrible
            {"A": 0.5, "B": 0.5},
        )
        self.assertGreater(attr["A"], attr["B"])

    def test_convergence_true_when_delta_below_epsilon(self):
        from corvin_compute.hac.attribution import check_convergence
        history = [0.5, 0.4, 0.401, 0.400]
        self.assertTrue(check_convergence(history, epsilon=0.01, window=2))

    def test_convergence_false_when_improving(self):
        from corvin_compute.hac.attribution import check_convergence
        history = [0.5, 0.4, 0.3, 0.2]
        self.assertFalse(check_convergence(history, epsilon=0.01, window=2))

    def test_convergence_needs_enough_history(self):
        from corvin_compute.hac.attribution import check_convergence
        self.assertFalse(check_convergence([0.1], epsilon=0.01, window=2))


if __name__ == "__main__":
    unittest.main()
