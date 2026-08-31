"""Security-hardening tests for the AWP workflow runtime.

Covers the pre-release hardening pass:
  WF-A1(a) — code node fails closed on a host without the bwrap sandbox unless
             the operator explicitly opts in (CORVIN_ALLOW_UNSANDBOXED_CODE).
  WF-A2    — a paused run's checkpoint is stored under ITS tenant, not _default.
  WF-A3    — only the intended approver (or a privileged owner) may answer a
             paused ask_human on resume.
  WF-A4    — retry / fan_out / delegation budgets are clamped to hard caps, and
             side-effecting nodes (deliver/answer) are never retried.
  CON-F2   — an atomic checkpoint claim makes a second concurrent resume fail
             fast instead of double-executing side effects.

Run directly:  python3 core/workflows/tests/test_workflow_hardening.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_PKG_ROOT = _HERE.parent
sys.path.insert(0, str(_PKG_ROOT))
# Forge's sandbox primitives live under operator/forge — make it importable so
# the code-node tests can patch forge.sandbox.have_bwrap directly.
_FORGE_ROOT = _PKG_ROOT.parents[1] / "operator" / "forge"
sys.path.insert(0, str(_FORGE_ROOT))

from corvin_workflows import DAGRunner, StubEngine, WorkflowDoc, validate  # noqa: E402
from corvin_workflows import checkpoint as _checkpoint  # noqa: E402
from corvin_workflows.code_exec import CodeExecutionError, run_sandboxed_python  # noqa: E402
from corvin_workflows.runner import (  # noqa: E402
    UnauthorizedReplier,
    _retry_config,
    resume_workflow,
)


class _TmpHomeMixin:
    def setUp(self) -> None:  # type: ignore[override]
        self._tmp_home = tempfile.mkdtemp(prefix="corvin_home_hard_")
        self._prev_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp_home

    def tearDown(self) -> None:  # type: ignore[override]
        if self._prev_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._prev_home
        shutil.rmtree(self._tmp_home, ignore_errors=True)


# ── WF-A1(a): code node fail-closed without bwrap ───────────────────────────

class CodeNodeSandboxOptInTests(unittest.TestCase):
    _SRC = "def main(a: int) -> dict:\n    return {'r': a + 1}\n"

    def test_no_bwrap_without_optin_fails_closed(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("forge.sandbox.have_bwrap", return_value=False):
            # Env cleared so the opt-in flag is absent.
            with self.assertRaises(CodeExecutionError) as ctx:
                run_sandboxed_python(self._SRC, {"a": 1})
        self.assertIn("CORVIN_ALLOW_UNSANDBOXED_CODE", str(ctx.exception))

    def test_no_bwrap_with_operator_optin_runs(self) -> None:
        with mock.patch.dict(os.environ, {"CORVIN_ALLOW_UNSANDBOXED_CODE": "1"}), \
             mock.patch("forge.sandbox.have_bwrap", return_value=False):
            result = run_sandboxed_python(self._SRC, {"a": 41})
        self.assertEqual(result, {"r": 42})


# ── WF-A2: checkpoint stored under the run's tenant ─────────────────────────

class CheckpointTenantScopingTests(_TmpHomeMixin, unittest.TestCase):
    def _paused_runner(self, tenant_id):
        graph = [{
            "id": "confirm", "type": "ask_human", "depends_on": [],
            "channel": "discord", "chat_id": "42", "prompt": "Confirm?",
            "expect": {"field": "confirmed", "type": "boolean"},
        }]
        doc = WorkflowDoc(
            awp_version="1.1.0", name="tenant_flow", description="t",
            inputs={}, orchestration={"engine": "dag", "graph": graph}, raw={},
        )
        validate(doc)
        runner = DAGRunner(doc, engine=StubEngine(), tenant_id=tenant_id)
        return runner.run(inputs={"chat_id": "42"})

    def test_non_default_tenant_checkpoint_is_isolated(self) -> None:
        paused = self._paused_runner("tenant-acme")
        self.assertEqual(paused.state, "paused")
        # Present under the run's tenant …
        self.assertIsNotNone(_checkpoint.load(paused.run_id, tenant_id="tenant-acme"))
        # … and absent from _default (the pre-fix bug always wrote _default).
        self.assertIsNone(_checkpoint.load(paused.run_id, tenant_id="_default"))


# ── WF-A3: only the intended approver may answer ────────────────────────────

class ApproverBindingTests(_TmpHomeMixin, unittest.TestCase):
    def _write_wf(self):
        import yaml

        tmpdir = tempfile.mkdtemp(prefix="awp_wf_hard_")
        self.addCleanup(lambda: shutil.rmtree(tmpdir, ignore_errors=True))
        path = Path(tmpdir) / "wf.awp.yaml"
        graph = [{
            "id": "confirm", "type": "ask_human", "depends_on": [],
            "channel": "discord", "chat_id": "owner-chat", "prompt": "Confirm?",
            "expect": {"field": "confirmed", "type": "boolean"},
        }]
        path.write_text(yaml.dump({
            "awp": "1.1.0",
            "workflow": {"name": "approver_flow", "description": "t"},
            "inputs": {}, "orchestration": {"engine": "dag", "graph": graph},
        }))
        return str(path)

    def _pause(self, tenant_id=None):
        from corvin_workflows.storage import load_workflow
        doc = load_workflow(self._write_wf())
        validate(doc)
        runner = DAGRunner(doc, engine=StubEngine(), tenant_id=tenant_id)
        return runner.run(inputs={})

    def test_checkpoint_records_approver(self) -> None:
        paused = self._pause()
        ckpt = _checkpoint.load(paused.run_id)
        self.assertEqual(ckpt["approver"], "owner-chat")

    def test_wrong_replier_is_denied(self) -> None:
        paused = self._pause()
        with self.assertRaises(UnauthorizedReplier):
            resume_workflow(paused.run_id, "ja", engine=StubEngine(), replier="attacker-chat")
        # Denied resume must leave the checkpoint intact & re-resumable.
        self.assertIsNotNone(_checkpoint.load(paused.run_id))

    def test_matching_replier_allowed(self) -> None:
        paused = self._pause()
        resumed = resume_workflow(paused.run_id, "ja", engine=StubEngine(), replier="owner-chat")
        self.assertEqual(resumed.state, "complete")

    def test_owner_replier_none_allowed(self) -> None:
        paused = self._pause()
        resumed = resume_workflow(paused.run_id, "ja", engine=StubEngine(), replier=None)
        self.assertEqual(resumed.state, "complete")


# ── WF-A4: bounds ───────────────────────────────────────────────────────────

class RetryBoundTests(unittest.TestCase):
    def test_max_retries_clamped(self) -> None:
        n, interval, strat = _retry_config(
            {"id": "x", "type": "agent", "retry": {"max_retries": 10_000, "retry_interval_s": 1e9}}
        )
        self.assertEqual(n, 10)          # _MAX_RETRIES_CAP
        self.assertEqual(interval, 60.0)  # _MAX_RETRY_INTERVAL_S

    def test_side_effecting_nodes_not_retried(self) -> None:
        for ntype in ("deliver", "answer"):
            n, _i, _s = _retry_config({"id": "d", "type": ntype, "retry": {"max_retries": 5}})
            self.assertEqual(n, 0, f"{ntype} must never retry (double-delivery guard)")


class FanOutBoundTests(unittest.TestCase):
    def test_fan_out_items_capped(self) -> None:
        # fan_out reads its list from `state` (an upstream node's output), so
        # seed state with a `code` node that emits 5000 items, then fan out.
        graph = [
            {
                "id": "seed", "type": "code", "language": "python3", "depends_on": [],
                "source": "def main() -> dict:\n    return {'items': list(range(5000))}\n",
                "outputs": ["items"], "inputs": {},
            },
            {
                "id": "fan", "type": "fan_out", "agent": "w",
                "items_from": "seed.items", "depends_on": ["seed"],
            },
        ]
        doc = WorkflowDoc(
            awp_version="1.1.0", name="fan_flow", description="t",
            inputs={}, orchestration={"engine": "dag", "graph": graph}, raw={},
        )
        engine = StubEngine(default=lambda call: {"ok": True})
        runner = DAGRunner(doc, engine=engine)
        result = runner.run(inputs={})
        self.assertEqual(result.state, "complete", result.error)
        # 5000 items >> cap of 500; the worker agent runs at most 500 times.
        worker_calls = [c for c in engine.history if c.agent == "w"]
        self.assertEqual(len(worker_calls), 500, "fan_out must clamp to _MAX_FAN_OUT_ITEMS")
        self.assertEqual(result.nodes["fan"].output["count"], 500)


class DelegationBudgetBoundTests(unittest.TestCase):
    def test_delegation_loops_capped(self) -> None:
        graph = [{
            "id": "deleg", "type": "delegation_loop", "depends_on": [],
            "config": {
                "manager": "mgr",
                "budget": {"max_loops": 10_000, "max_total_workers": 10_000},
            },
        }]
        doc = WorkflowDoc(
            awp_version="1.1.0", name="deleg_flow", description="t",
            inputs={}, orchestration={"engine": "dag", "graph": graph}, raw={},
        )
        # Manager always DELEGATEs a single worker; without the cap this would
        # run 10_000 loops. With the cap it stops at 100 loops.
        engine = StubEngine(default=lambda call: (
            {"decision": "DELEGATE", "workers": [{"agent": "wkr"}]}
            if call.metadata.get("role") == "manager" else {"ok": True}
        ))
        runner = DAGRunner(doc, engine=engine)
        result = runner.run(inputs={})
        self.assertEqual(result.state, "complete", result.error)
        manager_calls = [c for c in engine.history if c.metadata.get("role") == "manager"]
        self.assertLessEqual(len(manager_calls), 100, "delegation loops must clamp to cap")


# ── CON-F2: atomic checkpoint claim ─────────────────────────────────────────

class CheckpointClaimTests(_TmpHomeMixin, unittest.TestCase):
    def _save(self, run_id="deadbeef", tenant_id=None):
        _checkpoint.save(
            run_id, workflow_path="/x", workflow_name="w", inputs={}, state={},
            completed_ids=[], skipped_ids=[], paused_at_node="n", prompt="p",
            channel="discord", chat_id="c", expect=None, tenant_id=tenant_id,
        )

    def test_second_claim_fails_fast(self) -> None:
        self._save()
        self._checkpoint_claim = _checkpoint.claim("deadbeef")
        with self.assertRaises(_checkpoint.AlreadyClaimedError):
            _checkpoint.claim("deadbeef")

    def test_release_restores_resumability(self) -> None:
        self._save()
        _checkpoint.claim("deadbeef")
        # While claimed the canonical file is gone but load() still finds it.
        self.assertIsNotNone(_checkpoint.load("deadbeef"))
        _checkpoint.release("deadbeef")
        # After release a fresh claim succeeds again.
        self.assertIsNotNone(_checkpoint.claim("deadbeef"))

    def test_claim_missing_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            _checkpoint.claim("nope-not-here")


if __name__ == "__main__":
    unittest.main(verbosity=2)
