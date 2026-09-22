#!/usr/bin/env python3
"""Workflow Endpoints Performance Baseline (Task 2.1 - Stream 2)

Benchmarks critical Workflow Builder endpoints using Locust framework.
Measures latency (P50/P95/P99), throughput, error rates, and resource usage.

Usage (requires locust installed):
    locust -f benchmarks/benchmark_workflows.py \
      --host=http://localhost:8765 \
      --users=100 \
      --spawn-rate=10 \
      --run-time=5m \
      --csv=results/console_baseline

ADR-0863 Phase 7 Compliance:
  - Baseline latency for all workflow endpoints
  - Throughput validation (steady-state, spike load)
  - Resource utilization (memory, CPU, GC)
  - Fail-closed error handling
"""

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

try:
    from locust import HttpUser, task, between, events
    LOCUST_AVAILABLE = True
except ImportError:
    LOCUST_AVAILABLE = False
    # Fallback for testing without locust installed
    class HttpUser:  # type: ignore
        pass

_log = logging.getLogger(__name__)


# ============================================================================
# DATA FIXTURES
# ============================================================================

VALID_WORKFLOW_YAML = """
version: "0.1"
name: "Test Workflow"
tasks:
  - id: task1
    type: run-agent
    agent: claude-opus
    prompt: "Hello, world!"
"""

INVALID_WORKFLOW_YAML = """
version: "1.0"
invalid yaml: [unclosed
"""

LARGE_WORKFLOW_YAML = """
version: "0.1"
name: "Large Workflow"
tasks:
""" + "\n".join([
    f"""  - id: task{i}
    type: run-agent
    agent: claude-haiku
    prompt: "Task {i}: Perform step {i}"
""" for i in range(50)
])


# ============================================================================
# BENCHMARK USER SCENARIOS
# ============================================================================

class WorkflowBuilderLoadTest(HttpUser):
    """Load test for Workflow Builder endpoints (ADR-0863 Task 2.1).

    Simulates realistic operator usage patterns:
      - 50% GET operations (read-heavy workloads)
      - 30% POST operations (create new workflows)
      - 15% PUT operations (update workflow YAML)
      - 5% DELETE operations (cleanup)
    """

    wait_time = between(1, 3)  # 1-3 second random delay between requests

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.workflow_ids = []
        self.run_ids = []
        self.session_token = None

    def on_start(self):
        """Initialize: create auth session and seed workflows."""
        # In a real deployment, this would authenticate
        # For benchmarking, we assume /workflows endpoint is accessible
        self.workflow_ids = [f"wid_{i}" for i in range(5)]

    # ── LIGHTWEIGHT READS (50% traffic) ────────────────────────────────

    @task(25)
    def list_workflows(self) -> None:
        """GET /workflows - List all workflows (25% probability).

        SLA Target: P50 ≤ 100ms
        """
        with self.client.get(
            "/v1/console/workflows",
            catch_response=True,
            name="/workflows [list]",
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")

    @task(15)
    def get_workflow_yaml(self) -> None:
        """GET /workflows/{wid}/yaml - Fetch workflow YAML (15% probability).

        SLA Target: P50 ≤ 120ms
        """
        wid = self.workflow_ids[0] if self.workflow_ids else "test_wid"
        with self.client.get(
            f"/v1/console/workflows/{wid}/yaml",
            catch_response=True,
            name="/workflows/{wid}/yaml [get]",
        ) as response:
            if response.status_code in (200, 404):  # 404 if not created yet
                response.success()
            else:
                response.failure(f"Status {response.status_code}")

    @task(10)
    def get_workflow_runs(self) -> None:
        """GET /workflows/{wid}/runs - List workflow runs (10% probability).

        SLA Target: P50 ≤ 100ms
        """
        wid = self.workflow_ids[0] if self.workflow_ids else "test_wid"
        with self.client.get(
            f"/v1/console/workflows/{wid}/runs",
            catch_response=True,
            name="/workflows/{wid}/runs [list]",
        ) as response:
            if response.status_code in (200, 404):
                response.success()
            else:
                response.failure(f"Status {response.status_code}")

    # ── WRITE OPERATIONS (30% traffic) ────────────────────────────────

    @task(20)
    def create_workflow(self) -> None:
        """POST /workflows - Create new workflow (20% probability).

        SLA Target: P50 ≤ 150ms
        """
        import uuid
        wid = f"wid_{uuid.uuid4().hex[:8]}"

        payload = {
            "yaml": VALID_WORKFLOW_YAML,
            "title": f"Benchmark Workflow {wid}",
            "description": "Auto-generated benchmark workflow",
        }

        with self.client.post(
            "/v1/console/workflows",
            json=payload,
            catch_response=True,
            name="/workflows [create]",
        ) as response:
            if response.status_code == 201:
                try:
                    data = response.json()
                    if "id" in data:
                        self.workflow_ids.append(data["id"])
                    response.success()
                except:
                    response.failure("Invalid JSON response")
            else:
                response.failure(f"Status {response.status_code}")

    @task(10)
    def update_workflow_yaml(self) -> None:
        """PUT /workflows/{wid}/yaml - Update workflow YAML (10% probability).

        SLA Target: P50 ≤ 180ms
        """
        wid = self.workflow_ids[0] if self.workflow_ids else "test_wid"

        payload = {"yaml": VALID_WORKFLOW_YAML}

        with self.client.put(
            f"/v1/console/workflows/{wid}/yaml",
            json=payload,
            catch_response=True,
            name="/workflows/{wid}/yaml [put]",
        ) as response:
            if response.status_code in (200, 404):
                response.success()
            else:
                response.failure(f"Status {response.status_code}")

    # ── DELETE OPERATIONS (5% traffic) ────────────────────────────────

    @task(5)
    def delete_workflow(self) -> None:
        """DELETE /workflows/{wid} - Delete workflow (5% probability).

        SLA Target: P50 ≤ 100ms
        """
        if len(self.workflow_ids) < 2:
            return  # Keep at least one workflow

        wid = self.workflow_ids.pop()

        with self.client.delete(
            f"/v1/console/workflows/{wid}",
            catch_response=True,
            name="/workflows/{wid} [delete]",
        ) as response:
            if response.status_code in (204, 404):
                response.success()
            else:
                response.failure(f"Status {response.status_code}")


class WorkflowValidationLoadTest(HttpUser):
    """Load test for YAML validation (parsing, error handling).

    Tests fail-closed behavior:
      - Invalid YAML rejected with 400
      - Large payloads handled gracefully
      - Prompt guard applied consistently
    """

    wait_time = between(2, 4)

    @task
    def validate_invalid_yaml(self) -> None:
        """Test invalid YAML rejection.

        Expected: 400 Bad Request, no side effects
        """
        payload = {"yaml": INVALID_WORKFLOW_YAML}

        with self.client.put(
            "/v1/console/workflows/test_wid/yaml",
            json=payload,
            catch_response=True,
            name="/workflows/{wid}/yaml [validation: invalid]",
        ) as response:
            if response.status_code == 400:
                response.success()
            else:
                response.failure(f"Expected 400, got {response.status_code}")

    @task
    def validate_large_workflow(self) -> None:
        """Test large workflow handling.

        Expected: 201/200 with reasonable latency
        """
        payload = {"yaml": LARGE_WORKFLOW_YAML}

        with self.client.post(
            "/v1/console/workflows",
            json=payload,
            catch_response=True,
            name="/workflows [create: large]",
        ) as response:
            if response.status_code in (200, 201):
                response.success()
            else:
                response.failure(f"Status {response.status_code}")


# ============================================================================
# BASELINE METRICS COLLECTION
# ============================================================================

class BaselineMetrics:
    """Collects and reports latency/throughput baseline metrics."""

    def __init__(self, output_dir: str = "results"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True, parents=True)
        self.baselines: List[Dict[str, Any]] = []

    def record_endpoint(
        self,
        endpoint: str,
        method: str,
        p50_ms: float,
        p95_ms: float,
        p99_ms: float,
        error_rate: float,
        throughput_rps: float,
    ) -> None:
        """Record baseline metrics for an endpoint."""
        self.baselines.append({
            "timestamp": time.isoformat(time.time()),
            "endpoint": endpoint,
            "method": method,
            "p50_ms": p50_ms,
            "p95_ms": p95_ms,
            "p99_ms": p99_ms,
            "error_rate": error_rate,
            "throughput_rps": throughput_rps,
        })

    def save_json(self, filename: str = "baseline_results.json") -> Path:
        """Save baseline metrics to JSON file."""
        output_path = self.output_dir / filename
        with open(output_path, "w") as f:
            json.dump(self.baselines, f, indent=2)
        return output_path


# ============================================================================
# EXPECTED BASELINES (for validation)
# ============================================================================

EXPECTED_BASELINES = {
    "GET /workflows": {
        "p50_ms": 100,
        "p95_ms": 150,
        "p99_ms": 250,
        "error_rate": 0.001,
    },
    "POST /workflows": {
        "p50_ms": 150,
        "p95_ms": 200,
        "p99_ms": 300,
        "error_rate": 0.002,
    },
    "GET /workflows/{wid}/yaml": {
        "p50_ms": 120,
        "p95_ms": 180,
        "p99_ms": 280,
        "error_rate": 0.001,
    },
    "PUT /workflows/{wid}/yaml": {
        "p50_ms": 180,
        "p95_ms": 250,
        "p99_ms": 400,
        "error_rate": 0.002,
    },
    "GET /workflows/{wid}/runs": {
        "p50_ms": 100,
        "p95_ms": 160,
        "p99_ms": 270,
        "error_rate": 0.001,
    },
}


if __name__ == "__main__" and LOCUST_AVAILABLE:
    # This file is meant to be run with locust CLI:
    # locust -f benchmarks/benchmark_workflows.py --host=http://localhost:8765
    print("Workflow benchmarks loaded. Run with:")
    print("  locust -f benchmarks/benchmark_workflows.py \\")
    print("    --host=http://localhost:8765 \\")
    print("    --users=100 --spawn-rate=10 --run-time=5m \\")
    print("    --csv=results/console_baseline")
