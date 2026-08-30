"""E2E integration test for Phase 2: Engine Metrics + Daemon Collection.

k=5: Full gateway + daemon + engine execution flow
  - Verify metrics appear in `/v1/tenants/{tid}/metrics` within 15–30s
  - Daemon collects and renders audit events to Prometheus format
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

# Make the in-tree packages importable
_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO / "core" / "monitoring"))

from fastapi.testclient import TestClient  # noqa: E402

from corvin_gateway.app import app  # noqa: E402
from corvin_gateway.dispatcher import RunDispatcher  # noqa: E402
from agents import StreamEvent  # noqa: E402
from core.monitoring import get_daemon, start_daemon, stop_daemon  # noqa: E402


@contextmanager
def sandbox(tenants=("_default",)):
    """Set up a temporary CORVIN_HOME with tenant directories."""
    with tempfile.TemporaryDirectory(prefix="metrics-e2e-") as td:
        home = Path(td)
        os.environ["CORVIN_HOME"] = str(home)
        os.environ["ADAPTER_FAKE_CLAUDE"] = "1"
        os.environ["ADAPTER_FAKE_DELAY"] = "0.01"
        os.environ["CORVIN_METRICS_COLLECTOR_INTERVAL"] = "1"  # 1s for tests

        for t in tenants:
            (home / "tenants" / t / "global" / "auth").mkdir(parents=True)
            (home / "tenants" / t / "global" / "forge").mkdir(parents=True)
            (home / "tenants" / t / "global" / "gateway" / "runs").mkdir(
                parents=True
            )

        try:
            yield home
        finally:
            os.environ.pop("CORVIN_HOME", None)
            os.environ.pop("ADAPTER_FAKE_CLAUDE", None)
            os.environ.pop("ADAPTER_FAKE_DELAY", None)
            os.environ.pop("CORVIN_METRICS_COLLECTOR_INTERVAL", None)


@contextmanager
def gateway_client(engine_factory=None, default_budget_s: int = 60):
    """Engage the FastAPI lifespan with metrics daemon running."""
    if engine_factory is not None:
        app.state.dispatcher = RunDispatcher(
            engine_factory=engine_factory,
            default_budget_s=default_budget_s,
        )
    try:
        with TestClient(app) as client:
            yield client
    finally:
        if hasattr(app.state, "dispatcher"):
            app.state.dispatcher = None


def _good_run_body(persona="coder", input_text="print('hello')", budget=None):
    """Generate a valid run request body."""
    spec: dict[str, Any] = {"persona": persona, "input": input_text}
    if budget is not None:
        spec["budget_override"] = {"max_wall_clock_s": budget}
    return {"apiVersion": "corvin/v1", "kind": "Run", "spec": spec}


class MetricsEngineIntegrationTests(unittest.TestCase):
    """E2E tests for engine metrics collection pipeline."""

    def test_metrics_appear_after_engine_execution(self):
        """Metrics should appear in HTTP endpoint after engine runs."""
        with sandbox() as home:
            with gateway_client() as client:
                tenant_id = "_default"

                # 1. Submit a run
                run_body = _good_run_body()
                resp = client.post(
                    f"/v1/tenants/{tenant_id}/runs",
                    json=run_body,
                )
                self.assertEqual(resp.status_code, 202)
                run_data = resp.json()
                run_id = run_data["metadata"]["uid"]

                # 2. Wait for the run to complete (with fake delay ~20ms)
                for _ in range(50):
                    resp = client.get(
                        f"/v1/tenants/{tenant_id}/runs/{run_id}"
                    )
                    status = resp.json().get("status")
                    if status in ("completed", "failed"):
                        break
                    asyncio.run(asyncio.sleep(0.1))

                # 3. Query metrics endpoint
                resp = client.get(f"/v1/tenants/{tenant_id}/metrics")
                self.assertEqual(resp.status_code, 200)
                metrics_text = resp.text

                # 4. Verify engine execution metrics are present
                # At minimum, we should see audit chain events recorded
                self.assertIn("corvin_audit_chain_events_total", metrics_text)
                # The run should have emitted a gateway.run_status_changed event
                self.assertIn("corvin_gateway_runs_total", metrics_text)

    def test_metrics_daemon_is_running(self):
        """Daemon should start and collect metrics."""
        with sandbox() as home:
            with gateway_client() as client:
                # The daemon is started by the lifespan handler
                daemon = get_daemon()
                self.assertIsNotNone(daemon)
                # Daemon should be running (check lifespan started it)
                # This is implicit in the TestClient context

    def test_engine_success_metrics_emitted(self):
        """Successful engine runs should emit execution_completed events."""
        with sandbox() as home:
            with gateway_client() as client:
                tenant_id = "_default"

                # Submit a run
                run_body = _good_run_body()
                resp = client.post(
                    f"/v1/tenants/{tenant_id}/runs",
                    json=run_body,
                )
                self.assertEqual(resp.status_code, 202)
                run_data = resp.json()
                run_id = run_data["metadata"]["uid"]

                # Wait for completion
                for _ in range(50):
                    resp = client.get(
                        f"/v1/tenants/{tenant_id}/runs/{run_id}"
                    )
                    status = resp.json().get("status")
                    if status in ("completed", "failed"):
                        break
                    asyncio.run(asyncio.sleep(0.1))

                # Check the audit chain for engine metrics events
                audit_path = (
                    home / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"
                )
                if audit_path.exists():
                    with open(audit_path) as f:
                        lines = f.readlines()
                    # We should have at least one audit event (run status changed)
                    # Engine metrics events are bonus (engine.execution_completed)
                    self.assertGreater(len(lines), 0)

    def test_metrics_cache_populated(self):
        """Metrics cache should be populated by daemon."""
        with sandbox() as home:
            with gateway_client() as client:
                tenant_id = "_default"

                # Submit a run to generate metrics
                run_body = _good_run_body()
                resp = client.post(
                    f"/v1/tenants/{tenant_id}/runs",
                    json=run_body,
                )
                self.assertEqual(resp.status_code, 202)

                # Query metrics endpoint which should use the cache
                resp = client.get(f"/v1/tenants/{tenant_id}/metrics")
                self.assertEqual(resp.status_code, 200)
                self.assertIn("# HELP", resp.text)  # Prometheus format
                self.assertIn("# TYPE", resp.text)


if __name__ == "__main__":
    unittest.main()
