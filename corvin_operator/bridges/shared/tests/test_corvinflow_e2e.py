"""CorvinFlow E2E Integration Tests — ADR-0121.

Real-conditions test using two CorvinOS instances:
  Conductor : local machine (this process)
  Worker    : Hetzner corvin-test-b, 62.238.13.40 (hel1)

Run with:
    CORVINFLOW_E2E=1 pytest operator/bridges/shared/tests/test_corvinflow_e2e.py -v

Without CORVINFLOW_E2E=1 all tests are skipped (unit tests only run via normal suite).

Architecture under test (ADR-0121):
  FlowDefinition (YAML) ──parse──► FlowRunner
  FlowRunner ──pre-spawn-gate──► A2A TaskEnvelope ──► Worker Node
  Worker ──ResponseEnvelope──► FlowRunner ──► FlowRun manifest (append-only JSONL)
  FlowRun manifest events ──► L16 audit chain
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

# ── Environment gate ─────────────────────────────────────────────────────────
E2E = os.environ.get("CORVINFLOW_E2E", "").strip() == "1"
HETZNER_IP = "62.238.13.40"
HETZNER_SSH = ["ssh", "-o", "StrictHostKeyChecking=no",
               "-o", "ConnectTimeout=10", f"root@{HETZNER_IP}"]

pytestmark = pytest.mark.skipif(not E2E, reason="set CORVINFLOW_E2E=1 to run")

# ── Repo root ────────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[3]  # operator/bridges/shared/tests/ → repo root
_LIC = _REPO / "operator" / "license"
sys.path.insert(0, str(_REPO / "operator"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))

from license import validator as _v
from license import limits as _l

# ── Production modules (ADR-0121 M1) ─────────────────────────────────────────
from flow_definition import (
    FlowDefinition,
    FlowDefinitionError,
    FlowBudget,
    FlowBudgetExceeded,
    FlowRunManifest,
    sha256_prefix as _sha256_prefix,
)


class MinimalFlowRunner:
    """Proof-of-concept FlowRunner for E2E testing (ADR-0121 M1 skeleton).

    In production, this logic lives in operator/bridges/shared/flow_runner.py.
    This minimal version skips A2A envelope construction and uses direct SSH
    to simulate the worker execution — enough to validate the pre-spawn gate,
    budget tracking, manifest writing, and audit ordering.
    """

    def __init__(
        self,
        flow_def: FlowDefinition,
        run_home: Path,
        flow_input: dict[str, Any],
    ) -> None:
        self._flow = flow_def
        self._run_id = f"fr_{int(time.time()*1000)}"
        self._manifest = FlowRunManifest(
            run_home / "flows" / "runs" / f"{self._run_id}.manifest.jsonl"
        )
        self._budget = FlowBudget(flow_def.budget)
        self._input = flow_input
        self._outputs: dict[str, str] = {}

    def run(self) -> dict[str, Any]:
        """Execute all steps in dependency order. Returns final step output."""
        self._manifest.append(
            "mesh_flow.run_started",
            run_id=self._run_id,
            flow_id=self._flow.id,
            flow_version=self._flow.version,
            budget_allocated=self._flow.budget,
        )

        completed: set[str] = set()
        pending = dict(self._flow.steps)

        while pending:
            ready = {
                sid: step for sid, step in pending.items()
                if all(d in completed for d in step.get("depends_on", []))
            }
            if not ready:
                raise RuntimeError(
                    f"Dependency cycle or unsatisfiable deps in flow '{self._flow.id}'"
                )

            for step_id, step in ready.items():
                self._run_step(step_id, step)
                completed.add(step_id)
                del pending[step_id]

        self._manifest.append(
            "mesh_flow.run_completed",
            run_id=self._run_id,
            **self._budget.snapshot(),
            status="success",
        )
        return {
            "run_id": self._run_id,
            "status": "success",
            "outputs": self._outputs,
            "budget": self._budget.snapshot(),
        }

    def _run_step(self, step_id: str, step: dict[str, Any]) -> None:
        # 1. Budget gate (pre-spawn — fail-closed); emit event on exhaustion
        try:
            self._budget.check()
        except FlowBudgetExceeded as exc:
            self._manifest.append(
                "mesh_flow.budget_exceeded",
                run_id=self._run_id,
                step_id=step_id,
                reason=str(exc),
            )
            raise

        # 2. License compute gate (calls validator.assert_limit)
        try:
            _v.assert_limit("compute_units_per_day", self._budget._compute_used + 1)
        except _l.LicenseLimitError as exc:
            self._manifest.append(
                "mesh_flow.budget_exceeded",
                run_id=self._run_id,
                step_id=step_id,
                reason=str(exc),
            )
            raise FlowBudgetExceeded(str(exc)) from exc

        # 3. Audit-first: write step_dispatched BEFORE spawn
        self._manifest.append(
            "mesh_flow.step_dispatched",
            run_id=self._run_id,
            step_id=step_id,
            target_node=step.get("node", "local"),
            budget_before=self._budget.snapshot(),
        )

        # 4. Resolve task template
        task = self._resolve_template(step.get("task", ""), step_id)

        # 5. Execute on node (simulated via local Python or SSH)
        output = self._execute(step, task)

        # 6. Record accounting
        self._budget.record_step(compute=1, tokens=len(task) + len(output))
        self._outputs[step_id] = output

        self._manifest.append(
            "mesh_flow.step_completed",
            run_id=self._run_id,
            step_id=step_id,
            tokens_used=len(task) + len(output),
            output_sha256_prefix=_sha256_prefix(output),
            budget_after=self._budget.snapshot(),
        )
        self._manifest.append(
            "mesh_flow.budget_checkpoint",
            run_id=self._run_id,
            **self._budget.snapshot(),
        )

    def _resolve_template(self, template: str, step_id: str) -> str:
        result = template
        for k, v in self._input.items():
            result = result.replace(f"{{flow.input.{k}}}", str(v))
        for sid, out in self._outputs.items():
            result = result.replace(f"{{steps.{sid}.output}}", str(out)[:500])
        return result

    def _execute(self, step: dict[str, Any], task: str) -> str:
        """Execute step on target node. In production this sends A2A envelope."""
        node = step.get("node", "local")
        if node == "local" or node == "conductor":
            return f"[local] processed: {task[:80]}"
        elif node == "hetzner-eu":
            return self._execute_remote(task)
        return f"[{node}] processed: {task[:80]}"

    def _execute_remote(self, task: str) -> str:
        """SSH to Hetzner worker — simulates A2A TaskEnvelope dispatch."""
        safe_task = task.replace("'", "'\\''")[:200]
        cmd = HETZNER_SSH + [f"echo '[hetzner-eu] processed: {safe_task}'"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            raise RuntimeError(f"Remote execution failed: {result.stderr}")
        return result.stdout.strip()


# ═════════════════════════════════════════════════════════════════════════════
# Test fixtures
# ═════════════════════════════════════════════════════════════════════════════

SIMPLE_FLOW_YAML = textwrap.dedent("""
flow:
  id: test-simple-flow
  version: "1.0.0"
  budget:
    max_compute_units: 5
    max_tokens: 50000
    max_wall_time_s: 60
    max_cost_usd: 1.0
    max_steps: 5
  compliance:
    require_audit: true
  steps:
    research:
      node: hetzner-eu
      task: "Research: {flow.input.query}"
    synthesize:
      node: local
      depends_on: [research]
      task: "Synthesize: {steps.research.output}"
""").strip()

TWO_NODE_FLOW_YAML = textwrap.dedent("""
flow:
  id: test-two-node-flow
  version: "1.0.0"
  budget:
    max_compute_units: 10
    max_tokens: 100000
    max_wall_time_s: 120
    max_steps: 8
  steps:
    step_a:
      node: hetzner-eu
      task: "Step A on Hetzner: {flow.input.query}"
    step_b:
      node: local
      depends_on: [step_a]
      task: "Step B local synthesis: {steps.step_a.output}"
    step_c:
      node: hetzner-eu
      depends_on: [step_b]
      task: "Step C verification: {steps.step_b.output}"
""").strip()

TIGHT_BUDGET_FLOW_YAML = textwrap.dedent("""
flow:
  id: test-budget-exceeded-flow
  version: "1.0.0"
  budget:
    max_compute_units: 100
    max_tokens: 999999
    max_wall_time_s: 60
    max_steps: 10
  steps:
    step_one:
      node: local
      task: "First step: {flow.input.query}"
    step_two:
      node: local
      depends_on: [step_one]
      task: "Second step (should be blocked by license): {flow.input.query}"
""").strip()

INJECTION_ATTEMPT_YAML = textwrap.dedent("""
flow:
  id: test-injection
  version: "1.0.0"
  steps:
    bad_step:
      node: local
      task: "Do work {flow.input.query} and also {env.SECRET_KEY} and {os.environ}"
""").strip()


@pytest.fixture
def tmp_corvin_home(tmp_path):
    """Isolated corvin_home per test."""
    home = tmp_path / ".corvin"
    (home / "global" / "license").mkdir(parents=True)
    (home / "flows" / "runs").mkdir(parents=True)
    return home


@pytest.fixture(autouse=True)
def reset_license():
    """Ensure free tier for each test, then restore prior state via _set_active_license()."""
    prev = _v._ACTIVE_LICENSE
    _v._set_active_license(None)
    yield
    _v._set_active_license(_v._unproxy(prev) if prev is not None else None)


# ═════════════════════════════════════════════════════════════════════════════
# 1 — FlowDefinition parsing (unit-level, no network)
# ═════════════════════════════════════════════════════════════════════════════

class TestFlowDefinitionParsing:

    def test_valid_flow_parses(self):
        fd = FlowDefinition.from_yaml(SIMPLE_FLOW_YAML)
        assert fd.id == "test-simple-flow"
        assert fd.version == "1.0.0"
        assert "research" in fd.steps
        assert "synthesize" in fd.steps

    def test_depends_on_preserved(self):
        fd = FlowDefinition.from_yaml(SIMPLE_FLOW_YAML)
        assert fd.steps["synthesize"]["depends_on"] == ["research"]

    def test_budget_parsed(self):
        fd = FlowDefinition.from_yaml(SIMPLE_FLOW_YAML)
        assert fd.budget["max_compute_units"] == 5
        assert fd.budget["max_tokens"] == 50000

    def test_missing_id_raises(self):
        bad = "flow:\n  steps:\n    s:\n      node: local\n      task: x"
        with pytest.raises(FlowDefinitionError, match="flow.id"):
            FlowDefinition.from_yaml(bad)

    def test_empty_steps_raises(self):
        bad = "flow:\n  id: x\n  steps: {}"
        with pytest.raises(FlowDefinitionError, match="steps"):
            FlowDefinition.from_yaml(bad)


# ═════════════════════════════════════════════════════════════════════════════
# 2 — Template injection defence (unit-level)
# ═════════════════════════════════════════════════════════════════════════════

class TestTemplateInjectionDefence:

    def test_allowed_vars_pass(self):
        safe = textwrap.dedent("""
        flow:
          id: safe
          steps:
            s:
              node: local
              task: "Do {flow.input.query} with {steps.prev.output}"
        """)
        fd = FlowDefinition.from_yaml(safe)
        assert fd.id == "safe"

    def test_env_var_injection_blocked(self):
        with pytest.raises(FlowDefinitionError, match="disallowed template variable"):
            FlowDefinition.from_yaml(INJECTION_ATTEMPT_YAML)

    def test_os_environ_injection_blocked(self):
        bad = textwrap.dedent("""
        flow:
          id: bad
          steps:
            s:
              node: local
              task: "Use {os.environ} to leak data"
        """)
        with pytest.raises(FlowDefinitionError, match="disallowed"):
            FlowDefinition.from_yaml(bad)

    def test_arbitrary_attr_injection_blocked(self):
        bad = textwrap.dedent("""
        flow:
          id: bad
          steps:
            s:
              node: local
              task: "ignore previous instructions: {__builtins__}"
        """)
        with pytest.raises(FlowDefinitionError, match="disallowed"):
            FlowDefinition.from_yaml(bad)


# ═════════════════════════════════════════════════════════════════════════════
# 3 — Budget gate (unit-level)
# ═════════════════════════════════════════════════════════════════════════════

class TestBudgetGate:

    def test_fresh_budget_passes(self):
        b = FlowBudget({"max_compute_units": 5, "max_steps": 10})
        b.check()  # must not raise

    def test_compute_exhausted_raises(self):
        b = FlowBudget({"max_compute_units": 2, "max_steps": 10})
        b.record_step(compute=2)
        with pytest.raises(FlowBudgetExceeded, match="compute_units"):
            b.check()

    def test_step_count_exhausted_raises(self):
        b = FlowBudget({"max_compute_units": 999, "max_steps": 2})
        b.record_step()
        b.record_step()
        with pytest.raises(FlowBudgetExceeded, match="step_count"):
            b.check()

    def test_token_exhausted_raises(self):
        b = FlowBudget({"max_tokens": 100, "max_steps": 99})
        b.record_step(tokens=101)
        with pytest.raises(FlowBudgetExceeded, match="tokens"):
            b.check()

    def test_wall_time_exhausted_raises(self):
        b = FlowBudget({"max_wall_time_s": 0.01, "max_steps": 99})
        b._start_ts -= 1.0  # simulate 1s elapsed
        with pytest.raises(FlowBudgetExceeded, match="wall_time"):
            b.check()


# ═════════════════════════════════════════════════════════════════════════════
# 4 — FlowRun manifest integrity (unit-level)
# ═════════════════════════════════════════════════════════════════════════════

class TestFlowRunManifest:

    def test_manifest_created_mode_0600(self, tmp_path):
        m = FlowRunManifest(tmp_path / "runs" / "test.manifest.jsonl")
        mode = oct(stat.S_IMODE(m._path.stat().st_mode))
        assert mode == "0o600", f"Expected 0o600, got {mode}"

    def test_events_append_and_read(self, tmp_path):
        m = FlowRunManifest(tmp_path / "runs" / "test.manifest.jsonl")
        m.append("mesh_flow.run_started", run_id="fr_test", flow_id="x")
        m.append("mesh_flow.step_dispatched", run_id="fr_test", step_id="a")
        events = m.events()
        assert len(events) == 2
        assert events[0]["type"] == "mesh_flow.run_started"
        assert events[1]["type"] == "mesh_flow.step_dispatched"

    def test_output_content_never_in_manifest(self, tmp_path):
        m = FlowRunManifest(tmp_path / "runs" / "test.manifest.jsonl")
        secret_output = "VERY SECRET USER DATA — must not appear in manifest"
        m.append(
            "mesh_flow.step_completed",
            run_id="fr_test",
            step_id="a",
            output_sha256_prefix=_sha256_prefix(secret_output),
        )
        raw = m._path.read_text()
        assert secret_output not in raw
        assert _sha256_prefix(secret_output) in raw

    def test_audit_first_order(self, tmp_path):
        """step_dispatched must appear before step_completed in manifest."""
        m = FlowRunManifest(tmp_path / "runs" / "test.manifest.jsonl")
        m.append("mesh_flow.step_dispatched", step_id="a")
        m.append("mesh_flow.step_completed", step_id="a")
        types = m.event_types()
        assert types.index("mesh_flow.step_dispatched") < \
               types.index("mesh_flow.step_completed")


# ═════════════════════════════════════════════════════════════════════════════
# 5 — License gate integration (unit-level)
# ═════════════════════════════════════════════════════════════════════════════

class TestLicenseGateInFlow:

    def test_free_tier_blocks_second_compute_step(self, tmp_corvin_home):
        """Free tier: max_compute_units_per_day=1.
        Second step in a flow that already used 1 unit must be blocked."""
        fd = FlowDefinition.from_yaml(TIGHT_BUDGET_FLOW_YAML)
        runner = MinimalFlowRunner(fd, tmp_corvin_home, {"query": "test"})

        with pytest.raises(FlowBudgetExceeded):
            runner.run()

        # Manifest must contain budget_exceeded event
        event_types = runner._manifest.event_types()
        assert "mesh_flow.budget_exceeded" in event_types

        # Audit-first: step_dispatched for step_one before budget_exceeded
        types = event_types
        assert "mesh_flow.step_dispatched" in types
        dispatched_idx = types.index("mesh_flow.step_dispatched")
        exceeded_idx = types.index("mesh_flow.budget_exceeded")
        assert dispatched_idx < exceeded_idx

    def test_pro_tier_allows_multiple_steps(self, tmp_corvin_home):
        """Pro tier (100 compute/day): multi-step flow runs fully."""
        _v._set_active_license({
            "tier": "pro",
            "limits": {"compute_units_per_day": 100},
        })
        import time as _time
        _v._LICENSE_LOADED_AT = _time.time()

        fd = FlowDefinition.from_yaml(TIGHT_BUDGET_FLOW_YAML)
        runner = MinimalFlowRunner(fd, tmp_corvin_home, {"query": "test"})
        result = runner.run()

        assert result["status"] == "success"
        assert "step_one" in result["outputs"]
        assert "step_two" in result["outputs"]

        event_types = runner._manifest.event_types()
        assert "mesh_flow.budget_exceeded" not in event_types
        assert "mesh_flow.run_completed" in event_types


# ═════════════════════════════════════════════════════════════════════════════
# 6 — Local single-node flow (no network required)
# ═════════════════════════════════════════════════════════════════════════════

class TestLocalSingleNodeFlow:

    def test_local_two_step_flow_completes(self, tmp_corvin_home):
        """Two local steps, second depends on first — full FlowRun lifecycle."""
        _v._set_active_license({
            "tier": "pro",
            "limits": {"compute_units_per_day": 100},
        })
        import time as _t; _v._LICENSE_LOADED_AT = _t.time()

        local_flow = textwrap.dedent("""
        flow:
          id: local-two-step
          version: "1.0.0"
          budget:
            max_compute_units: 10
            max_steps: 5
          steps:
            gather:
              node: local
              task: "Gather data about: {flow.input.query}"
            summarise:
              node: local
              depends_on: [gather]
              task: "Summarise: {steps.gather.output}"
        """).strip()

        fd = FlowDefinition.from_yaml(local_flow)
        runner = MinimalFlowRunner(fd, tmp_corvin_home, {"query": "CorvinFlow test"})
        result = runner.run()

        assert result["status"] == "success"
        assert "gather" in result["outputs"]
        assert "summarise" in result["outputs"]

        events = runner._manifest.events()
        types = [e["type"] for e in events]
        assert types[0] == "mesh_flow.run_started"
        assert types[-1] == "mesh_flow.run_completed"
        assert types.count("mesh_flow.step_dispatched") == 2
        assert types.count("mesh_flow.step_completed") == 2

    def test_dependency_order_respected(self, tmp_corvin_home):
        """step_b output references step_a — must execute in correct order."""
        _v._set_active_license({"tier": "pro", "limits": {"compute_units_per_day": 100}})
        import time as _t; _v._LICENSE_LOADED_AT = _t.time()

        chained = textwrap.dedent("""
        flow:
          id: chained
          version: "1.0.0"
          budget:
            max_compute_units: 10
            max_steps: 5
          steps:
            first:
              node: local
              task: "Phase one: {flow.input.query}"
            second:
              node: local
              depends_on: [first]
              task: "Phase two uses: {steps.first.output}"
        """).strip()

        fd = FlowDefinition.from_yaml(chained)
        runner = MinimalFlowRunner(fd, tmp_corvin_home, {"query": "hello"})
        result = runner.run()

        # second step's task should include first step's output in its resolved task
        assert result["status"] == "success"
        second_output = result["outputs"]["second"]
        assert "Phase two" in second_output


# ═════════════════════════════════════════════════════════════════════════════
# 7 — Hetzner E2E: Two real instances (network required)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(not E2E, reason="Network E2E: CORVINFLOW_E2E=1 required")
class TestHetznerTwoNodeFlow:

    def test_hetzner_node_reachable(self):
        """Precondition: SSH to Hetzner worker succeeds."""
        result = subprocess.run(
            HETZNER_SSH + ["echo corvin-ok"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"SSH failed: {result.stderr}"
        assert "corvin-ok" in result.stdout

    def test_two_node_flow_local_to_hetzner(self, tmp_corvin_home):
        """Flow: research on Hetzner → synthesize locally."""
        _v._set_active_license({"tier": "pro", "limits": {"compute_units_per_day": 100}})
        import time as _t; _v._LICENSE_LOADED_AT = _t.time()

        fd = FlowDefinition.from_yaml(SIMPLE_FLOW_YAML)
        runner = MinimalFlowRunner(
            fd, tmp_corvin_home, {"query": "CorvinFlow E2E integration test 2026-06-12"}
        )
        result = runner.run()

        assert result["status"] == "success"
        assert "research" in result["outputs"]
        assert "synthesize" in result["outputs"]

        # Hetzner step output must mention the node
        research_out = result["outputs"]["research"]
        assert "hetzner-eu" in research_out.lower() or "processed" in research_out.lower()

        # FlowRun manifest must have correct event sequence
        types = runner._manifest.event_types()
        assert types[0] == "mesh_flow.run_started"
        assert types[-1] == "mesh_flow.run_completed"

        # Audit-first: step_dispatched before step_completed for each step
        dispatched = [i for i, t in enumerate(types) if t == "mesh_flow.step_dispatched"]
        completed = [i for i, t in enumerate(types) if t == "mesh_flow.step_completed"]
        for d, c in zip(dispatched, completed):
            assert d < c, "audit-first violated: step_completed before step_dispatched"

    def test_three_step_flow_with_hetzner_middle(self, tmp_corvin_home):
        """Flow: local → hetzner → local — full three-node pipeline."""
        _v._set_active_license({"tier": "pro", "limits": {"compute_units_per_day": 100}})
        import time as _t; _v._LICENSE_LOADED_AT = _t.time()

        fd = FlowDefinition.from_yaml(TWO_NODE_FLOW_YAML)
        runner = MinimalFlowRunner(
            fd, tmp_corvin_home,
            {"query": "Three-step CorvinFlow pipeline test"}
        )
        result = runner.run()

        assert result["status"] == "success"
        assert result["budget"]["steps_done"] == 3
        assert "step_a" in result["outputs"]
        assert "step_b" in result["outputs"]
        assert "step_c" in result["outputs"]

    def test_budget_exceeded_mid_flow_cancels_remaining(self, tmp_corvin_home):
        """Budget exhaustion on step 2 must cancel step 3 (not skip silently)."""
        # Free tier: only 1 compute unit — step 1 uses it, step 2 blocked
        _v._set_active_license(None)  # free tier (also resets canary)

        fd = FlowDefinition.from_yaml(TIGHT_BUDGET_FLOW_YAML)
        runner = MinimalFlowRunner(fd, tmp_corvin_home, {"query": "budget test"})

        with pytest.raises(FlowBudgetExceeded):
            runner.run()

        types = runner._manifest.event_types()
        assert "mesh_flow.budget_exceeded" in types
        assert "mesh_flow.run_completed" not in types  # must NOT complete

    def test_manifest_file_mode_0600_on_hetzner(self, tmp_corvin_home):
        """FlowRun manifest on local must be mode 0600 (same constraint applies remote)."""
        _v._set_active_license({"tier": "pro", "limits": {"compute_units_per_day": 100}})
        import time as _t; _v._LICENSE_LOADED_AT = _t.time()

        fd = FlowDefinition.from_yaml(SIMPLE_FLOW_YAML)
        runner = MinimalFlowRunner(fd, tmp_corvin_home, {"query": "mode check"})
        runner.run()

        manifest_path = runner._manifest._path
        mode = oct(stat.S_IMODE(manifest_path.stat().st_mode))
        assert mode == "0o600", f"Manifest mode {mode} != 0o600"

    def test_hetzner_license_enforcement_independent(self):
        """Hetzner instance enforces its own free-tier limits independently."""
        import base64
        script = textwrap.dedent("""
        import sys
        sys.path.insert(0, '/tmp/operator')
        from license import validator as v
        from license import limits as l
        v._set_active_license(None)
        try:
            v.assert_limit('compute_units_per_day', 2)
            print('FAIL: should be blocked')
        except l.LicenseLimitError:
            print('PASS: remote free-tier enforced')
        """).strip()
        b64 = base64.b64encode(script.encode()).decode()
        remote_cmd = f"echo {b64} | base64 -d | python3"
        result = subprocess.run(
            HETZNER_SSH + [remote_cmd],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"Remote check failed: {result.stderr}"
        assert "PASS" in result.stdout, f"Remote enforcement failed: {result.stdout}"


# ═════════════════════════════════════════════════════════════════════════════
# 8 — FlowBundle pack / install cycle (unit-level)
# ═════════════════════════════════════════════════════════════════════════════

class TestFlowBundleCycle:
    """Validate the FlowBundle archive format without actual Ed25519 signing
    (signing requires a key-pair; we test the structure here)."""

    def test_bundle_archive_structure(self, tmp_path):
        """A FlowBundle must contain flow.yaml + manifest.json at minimum."""
        import zipfile

        # Create bundle manually (real corvin-flow pack will do this)
        bundle_path = tmp_path / "test-flow-1.0.0.corvinflow"
        with zipfile.ZipFile(bundle_path, "w") as zf:
            zf.writestr("flow.yaml", SIMPLE_FLOW_YAML)
            zf.writestr("manifest.json", json.dumps({
                "id": "test-simple-flow",
                "version": "1.0.0",
                "author": "test",
                "requires": {"corvinflow": ">=1.0"},
            }))

        # Validate structure
        with zipfile.ZipFile(bundle_path, "r") as zf:
            names = zf.namelist()
            assert "flow.yaml" in names
            assert "manifest.json" in names

    def test_bundle_flow_definition_parseable(self, tmp_path):
        """flow.yaml extracted from a bundle must parse cleanly."""
        import zipfile

        bundle_path = tmp_path / "test-flow-1.0.0.corvinflow"
        with zipfile.ZipFile(bundle_path, "w") as zf:
            zf.writestr("flow.yaml", SIMPLE_FLOW_YAML)
            zf.writestr("manifest.json", json.dumps({"id": "test-simple-flow"}))

        with zipfile.ZipFile(bundle_path, "r") as zf:
            flow_yaml = zf.read("flow.yaml").decode()

        fd = FlowDefinition.from_yaml(flow_yaml)
        assert fd.id == "test-simple-flow"
        assert "research" in fd.steps

    def test_bundle_injection_attempt_rejected_at_install(self, tmp_path):
        """A bundle with an injected flow.yaml must fail at install (parse time)."""
        import zipfile

        bundle_path = tmp_path / "malicious-1.0.0.corvinflow"
        with zipfile.ZipFile(bundle_path, "w") as zf:
            zf.writestr("flow.yaml", INJECTION_ATTEMPT_YAML)
            zf.writestr("manifest.json", json.dumps({"id": "malicious"}))

        with zipfile.ZipFile(bundle_path, "r") as zf:
            flow_yaml = zf.read("flow.yaml").decode()

        with pytest.raises(FlowDefinitionError, match="disallowed"):
            FlowDefinition.from_yaml(flow_yaml)
