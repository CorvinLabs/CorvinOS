"""Phase 13.4 — Worker daemon + Unix-socket protocol tests.

Six core acceptance cases per the implementation plan:

1. test_protocol_roundtrip
2. test_concurrent_run_limit
3. test_unknown_handle_returns_typed_error
4. test_abort_terminates_run
5. test_worker_serves_only_its_tenant
6. test_socket_mode_0600
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from corvin_compute.client import WorkerClient, WorkerClientError, is_socket_reachable  # noqa: E402
from corvin_compute.transport import (  # noqa: E402
    MAX_FRAME_BYTES, TransportError, recv_frame_sync, send_frame_sync,
)
from corvin_compute.worker import WorkerServer  # noqa: E402


def _identity_runner(slow_ms: int = 0):
    """Stub runner: returns ``{"loss": params["x"]}`` after optional sleep."""

    def runner(tool_name, payload):
        if slow_ms > 0:
            time.sleep(slow_ms / 1000.0)
        return {"loss": float(payload.get("x", 0))}

    return runner


class _WorkerHarness:
    """Spin up a worker in a background asyncio thread for tests."""

    def __init__(self, tenant_id="_default", runner=None,
                 max_concurrent_runs=2, strategies_allowed=None) -> None:
        self.td = tempfile.mkdtemp(prefix="corvin-compute-worker-")
        self.corvin_home = Path(self.td) / "corvin"
        (self.corvin_home / "tenants" / tenant_id / "compute").mkdir(
            parents=True, exist_ok=True)
        self.socket_path = (self.corvin_home / "tenants" / tenant_id
                            / "compute" / "worker.sock")
        self.tenant_id = tenant_id
        self.runner = runner or _identity_runner()
        self.max_concurrent_runs = max_concurrent_runs
        self.strategies_allowed = strategies_allowed
        self.loop: asyncio.AbstractEventLoop | None = None
        self.server: WorkerServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> WorkerClient:
        ready = threading.Event()

        def _runner():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.server = WorkerServer(
                tenant_id=self.tenant_id,
                corvin_home=self.corvin_home,
                socket_path=self.socket_path,
                max_concurrent_runs=self.max_concurrent_runs,
                runner_fn=self.runner,
                strategies_allowed=self.strategies_allowed,
            )

            async def _serve():
                ready_task = asyncio.create_task(self.server.serve_forever())
                # Wait for socket to exist
                while not self.socket_path.exists():
                    await asyncio.sleep(0.01)
                ready.set()
                await ready_task

            try:
                self.loop.run_until_complete(_serve())
            except Exception:  # noqa: BLE001
                pass
            finally:
                self.loop.close()

        self.thread = threading.Thread(target=_runner, daemon=True)
        self.thread.start()
        if not ready.wait(timeout=5.0):
            raise RuntimeError("worker failed to start")
        return WorkerClient(self.socket_path, timeout_s=10.0)

    def stop(self) -> None:
        if self.server is not None and self.loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(self.server.stop(),
                                                 self.loop).result(timeout=5.0)
            except Exception:  # noqa: BLE001
                pass
            try:
                self.loop.call_soon_threadsafe(self.loop.stop)
            except Exception:  # noqa: BLE001
                pass
        if self.thread is not None:
            self.thread.join(timeout=5.0)
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)


class ProtocolRoundtripTests(unittest.TestCase):
    def test_protocol_roundtrip(self) -> None:
        h = _WorkerHarness()
        client = h.start()
        try:
            ping = client.ping()
            self.assertTrue(ping.get("pong"))
            self.assertEqual(ping.get("tenant_id"), "_default")

            sub = client.submit_run(
                tenant_id="_default",
                tool_name="echo",
                param_grid={"x": [0.1, 0.2, 0.3]},
                loss_metric="loss",
                strategy="grid",
                budget={"max_iterations": 10, "max_wall_clock_s": 5},
            )
            handle = sub["compute_handle"]
            self.assertTrue(handle.startswith("compute_"))

            # Poll until terminal
            for _ in range(50):
                st = client.get_status(handle)
                if st["state"] in ("converged", "stalled", "budget_exhausted",
                                   "failed", "aborted"):
                    break
                time.sleep(0.1)
            res = client.get_result(handle, wait_s=5.0)
            self.assertIn(res["state"],
                          ("converged", "stalled", "budget_exhausted"))
            self.assertIsNotNone(res["best_loss"])
        finally:
            h.stop()


class ConcurrencyTests(unittest.TestCase):
    def test_concurrent_run_limit(self) -> None:
        h = _WorkerHarness(runner=_identity_runner(slow_ms=200),
                            max_concurrent_runs=2)
        client = h.start()
        try:
            handles: list[str] = []
            for _ in range(3):
                sub = client.submit_run(
                    tenant_id="_default",
                    tool_name="echo",
                    param_grid={"x": [0.1, 0.2, 0.3, 0.4, 0.5]},
                    loss_metric="loss",
                    strategy="grid",
                    budget={"max_iterations": 5, "max_wall_clock_s": 10},
                )
                handles.append(sub["compute_handle"])
            # Allow the asyncio scheduler a moment to assign states.
            time.sleep(0.1)
            # Third submission must be queued.
            states = [client.get_status(h_)["state"] for h_ in handles]
            queued_count = sum(1 for s in states if s == "queued")
            running_count = sum(1 for s in states if s == "running")
            self.assertGreaterEqual(queued_count, 1)
            self.assertLessEqual(running_count, 2)

            # Eventually all three should reach a terminal state.
            for handle in handles:
                for _ in range(80):
                    st = client.get_status(handle)
                    if st["state"] in ("converged", "stalled",
                                       "budget_exhausted", "aborted"):
                        break
                    time.sleep(0.1)
        finally:
            h.stop()

    def test_non_flat_jobs_do_not_starve_flat_runs(self) -> None:
        """Adversarial-review finding (HIGH): a non-flat engine job (pipeline/HAC)
        creates a placeholder _RunEntry stuck in RUNNING with no worker task and no
        terminal transition. If those entries counted against the flat
        max_concurrent_runs budget, submitting max_concurrent_runs non-flat jobs
        would permanently starve every subsequent FLAT run. Flat-only slot
        accounting must keep flat runs executing regardless of non-flat jobs."""
        from unittest.mock import MagicMock
        from corvin_compute.engine_protocol import ComputeStatus

        mock_engine = MagicMock()
        mock_engine.engine_id = "pipeline"
        mock_engine.job_id_prefix = "pipe_"
        mock_engine.supports_gates = False
        _n = {"i": 0}

        def _submit(spec):
            _n["i"] += 1
            return f"pipe_stuck{_n['i']:04d}"

        mock_engine.submit.side_effect = _submit
        mock_engine.status.return_value = ComputeStatus(
            job_id="pipe_stuck0001", engine_id="pipeline",
            state="running", progress={}, detail={},  # never terminal
        )

        h = _WorkerHarness(max_concurrent_runs=2)
        client = h.start()
        h.server.register_engine(mock_engine)
        try:
            # Occupy >= max_concurrent_runs with perpetually-running non-flat jobs.
            for _ in range(3):
                client.submit_run(engine="pipeline", tenant_id="_default",
                                  param_grid={"x": [0.1]},
                                  budget={"max_iterations": 1})
            # A flat run submitted now must still execute and reach terminal —
            # before the fix it would sit "queued" forever.
            sub = client.submit_run(
                tenant_id="_default", tool_name="echo",
                param_grid={"x": [0.1, 0.2, 0.3]}, loss_metric="loss",
                strategy="grid", budget={"max_iterations": 5, "max_wall_clock_s": 5},
            )
            handle = sub["compute_handle"]
            terminal = False
            for _ in range(80):
                st = client.get_status(handle)
                if st["state"] in ("converged", "stalled", "budget_exhausted"):
                    terminal = True
                    break
                time.sleep(0.1)
            self.assertTrue(
                terminal,
                "flat run was starved by non-flat jobs holding the slot budget",
            )
        finally:
            h.stop()


class ErrorMappingTests(unittest.TestCase):
    def test_unknown_handle_returns_typed_error(self) -> None:
        h = _WorkerHarness()
        client = h.start()
        try:
            with self.assertRaises(WorkerClientError) as ctx:
                client.get_status("compute_doesnotexistxxxxxxxxxxxxxxxx")
            self.assertEqual(ctx.exception.error_class, "UnknownHandle")
        finally:
            h.stop()

    def test_invalid_handle_shape(self) -> None:
        h = _WorkerHarness()
        client = h.start()
        try:
            with self.assertRaises(WorkerClientError) as ctx:
                client.get_status("not-a-handle")
            self.assertEqual(ctx.exception.error_class, "UnknownHandle")
        finally:
            h.stop()


class AbortTests(unittest.TestCase):
    def test_abort_terminates_run(self) -> None:
        # Slow runner: 200 ms per iter, budget large, many iters.
        h = _WorkerHarness(runner=_identity_runner(slow_ms=200))
        client = h.start()
        try:
            sub = client.submit_run(
                tenant_id="_default",
                tool_name="echo",
                param_grid={"x": list(range(50))},
                loss_metric="loss",
                strategy="grid",
                budget={"max_iterations": 50, "max_wall_clock_s": 20},
            )
            handle = sub["compute_handle"]
            time.sleep(0.3)  # let it run a few iters
            client.abort_run(handle)
            res = client.get_result(handle, wait_s=5.0)
            self.assertEqual(res["state"], "aborted")
        finally:
            h.stop()


class TenantBindingTests(unittest.TestCase):
    def test_worker_serves_only_its_tenant(self) -> None:
        h = _WorkerHarness(tenant_id="acme")
        client = h.start()
        try:
            with self.assertRaises(WorkerClientError) as ctx:
                client.submit_run(
                    tenant_id="globex",
                    tool_name="echo",
                    param_grid={"x": [0.1]},
                    strategy="grid",
                )
            self.assertEqual(ctx.exception.error_class, "TenantMismatch")
        finally:
            h.stop()


class SocketSecurityTests(unittest.TestCase):
    def test_socket_mode_0600(self) -> None:
        h = _WorkerHarness()
        h.start()
        try:
            st = os.stat(h.socket_path)
            mode = st.st_mode & 0o777
            self.assertEqual(mode, 0o600,
                             f"socket must be 0600, got {oct(mode)}")
        finally:
            h.stop()

    def test_is_socket_reachable_works(self) -> None:
        h = _WorkerHarness()
        h.start()
        try:
            self.assertTrue(is_socket_reachable(h.socket_path))
        finally:
            h.stop()
        # After stop, the socket file should be gone (best-effort cleanup).
        self.assertFalse(is_socket_reachable(h.socket_path))


class StrategyAllowlistTests(unittest.TestCase):
    def test_strategy_not_allowed_rejected(self) -> None:
        h = _WorkerHarness(strategies_allowed=["grid"])
        client = h.start()
        try:
            with self.assertRaises(WorkerClientError) as ctx:
                client.submit_run(
                    tenant_id="_default",
                    tool_name="echo",
                    param_grid={"x": [0.1]},
                    strategy="random",
                )
            self.assertEqual(ctx.exception.error_class, "StrategyNotAllowed")
        finally:
            h.stop()


class TransportTests(unittest.TestCase):
    def test_frame_size_cap(self) -> None:
        big = {"junk": "x" * (MAX_FRAME_BYTES + 1)}
        s1, s2 = socket.socketpair()
        try:
            with self.assertRaises(TransportError):
                send_frame_sync(s1, big)
        finally:
            s1.close()
            s2.close()

    def test_frame_roundtrip(self) -> None:
        s1, s2 = socket.socketpair()
        try:
            payload = {"a": 1, "b": [2, 3], "c": {"x": "y"}}
            send_frame_sync(s1, payload)
            received = recv_frame_sync(s2)
            self.assertEqual(received, payload)
        finally:
            s1.close()
            s2.close()


class AnthropicBatchEngineWorkerTests(unittest.TestCase):
    """ADR-0099 — verify _require_handle accepts abatch_ prefix.

    The bug: _MULTI_ENGINE_PREFIXES did not include "abatch_", so
    get_status / abort_run on AnthropicBatchEngine jobs raised UnknownHandle
    before the engine could be consulted.
    """

    def _make_mock_batch_engine(self):
        from unittest.mock import MagicMock
        from corvin_compute.engine_protocol import ComputeStatus, ComputeResult
        engine = MagicMock()
        engine.engine_id = "anthropic_batch"
        engine.job_id_prefix = "abatch_"
        engine.supports_gates = False
        engine.submit.return_value = "abatch_test0001"
        engine.status.return_value = ComputeStatus(
            job_id="abatch_test0001",
            engine_id="anthropic_batch",
            state="running",
            progress={},
            detail={},
        )
        engine.result.return_value = ComputeResult(
            job_id="abatch_test0001",
            engine_id="anthropic_batch",
            state="succeeded",
            result={"best_loss": 0.1},
        )
        return engine

    def test_abatch_prefix_accepted_by_require_handle(self):
        """get_status on an abatch_ job must not raise UnknownHandle."""
        mock_engine = self._make_mock_batch_engine()
        h = _WorkerHarness()
        client = h.start()
        h.server.register_engine(mock_engine)
        try:
            sub = client.submit_run(engine="anthropic_batch", tenant_id="_default",
                                    param_grid={"lr": [0.01]},
                                    budget={"max_iterations": 1})
            handle = sub["compute_handle"]
            self.assertTrue(handle.startswith("abatch_"), handle)
            st = client.get_status(handle)
            self.assertEqual(st["state"], "running")
        finally:
            h.stop()

    def test_abatch_abort_accepted(self):
        """abort_run on an abatch_ job must route to the engine."""
        mock_engine = self._make_mock_batch_engine()
        h = _WorkerHarness()
        client = h.start()
        h.server.register_engine(mock_engine)
        try:
            client.submit_run(engine="anthropic_batch", tenant_id="_default",
                              param_grid={"lr": [0.01]},
                              budget={"max_iterations": 1})
            client.abort_run("abatch_test0001")
            mock_engine.abort.assert_called_once_with("abatch_test0001")
        finally:
            h.stop()

    def test_abatch_param_grid_forwarded_to_spec(self):
        """param_grid from submit_run must reach ComputeSpec (not silently dropped)."""
        received_specs: list = []

        def capturing_submit(spec):
            received_specs.append(spec)
            return "abatch_captured001"

        from unittest.mock import MagicMock
        from corvin_compute.engine_protocol import ComputeStatus
        mock_engine = MagicMock()
        mock_engine.engine_id = "anthropic_batch"
        mock_engine.job_id_prefix = "abatch_"
        mock_engine.supports_gates = False
        mock_engine.submit.side_effect = capturing_submit
        mock_engine.status.return_value = ComputeStatus(
            job_id="abatch_captured001", engine_id="anthropic_batch",
            state="running", progress={}, detail={},
        )

        h = _WorkerHarness()
        client = h.start()
        h.server.register_engine(mock_engine)
        try:
            client.submit_run(engine="anthropic_batch", tenant_id="_default",
                              param_grid={"lr": [0.01, 0.1], "depth": [2, 4]},
                              budget={"max_iterations": 4})
            self.assertEqual(len(received_specs), 1)
            spec = received_specs[0]
            self.assertEqual(spec.param_grid, {"lr": [0.01, 0.1], "depth": [2, 4]})
        finally:
            h.stop()

    def test_abatch_minimise_false_forwarded_to_spec(self):
        """minimise=False from submit_run must reach ComputeSpec."""
        received_specs: list = []

        def capturing_submit(spec):
            received_specs.append(spec)
            return "abatch_min001"

        from unittest.mock import MagicMock
        from corvin_compute.engine_protocol import ComputeStatus
        mock_engine = MagicMock()
        mock_engine.engine_id = "anthropic_batch"
        mock_engine.job_id_prefix = "abatch_"
        mock_engine.supports_gates = False
        mock_engine.submit.side_effect = capturing_submit
        mock_engine.status.return_value = ComputeStatus(
            job_id="abatch_min001", engine_id="anthropic_batch",
            state="running", progress={}, detail={},
        )

        h = _WorkerHarness()
        client = h.start()
        h.server.register_engine(mock_engine)
        try:
            client.submit_run(engine="anthropic_batch", tenant_id="_default",
                              param_grid={"x": [1]},
                              minimise=False,
                              budget={"max_iterations": 1})
            self.assertEqual(len(received_specs), 1)
            self.assertFalse(received_specs[0].minimise)
        finally:
            h.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
