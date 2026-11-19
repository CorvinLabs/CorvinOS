"""Review 2026-11-19, PLS-1 (reporting half) and PLS-6, through the real MCP stdio loop.

* a ``workflow_run`` whose delegation_loop manager says FAIL answers ``status: failed`` with
  ``isError: true`` (it used to answer ``complete``);
* a loop that ran out of budget answers ``status: partial`` and names the node - in the tool
  envelope and in the completion-notify text;
* ``acs_delegate`` marks every status as an error except the ones on its positive list
  (``budget_exhausted`` used to pass as a clean result).

The DAG runs for real (real ``DAGRunner``, real validation, real audit sink); only the model
behind the engine is scripted.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_mcp_server import TestOrchestrationServerBase, _call, _drive  # noqa: E402  (sets sys.path)

import corvin_orchestration.mcp_server as m  # noqa: E402

_LOOP_WORKFLOW = """
awp: "1.0.0"
workflow:
  name: %s
  description: delegation loop followed by a consumer
orchestration:
  engine: dag
  graph:
    - id: loop
      type: delegation_loop
      agent: a
      depends_on: []
      config:
        manager: m
        budget: {max_loops: 2, max_total_workers: 5}
    - id: consumer
      type: agent
      agent: consumer
      depends_on: [loop]
      instructions: use the loop's result
"""


class _ScriptedEngine:
    """Stands in for ``ClaudeCliEngine``: same constructor shape, scripted manager answers."""

    name = "scripted"
    manager_answer: object = None
    calls: list = []

    def __init__(self, *, tenant_id=None, **_kw):
        self.tenant_id = tenant_id

    def spawn(self, call):
        type(self).calls.append((call.agent, call.metadata.get("role")))
        if call.metadata.get("role") == "manager":
            return type(self).manager_answer
        return {"ok": True}


class TestWorkflowRunReportsALoopThatDidNotFinish(TestOrchestrationServerBase):
    def _run(self, manager_answer, wid="pls1_loop"):
        (self.home / "tenants" / "_default" / "workflows" / f"{wid}.awp.yaml").write_text(
            _LOOP_WORKFLOW % wid, encoding="utf-8")
        _ScriptedEngine.manager_answer = manager_answer
        _ScriptedEngine.calls = []
        with mock.patch.object(m, "_ClaudeCliEngine", _ScriptedEngine):
            resp = _drive(self._server(), [_call("workflow_run", {"workflow_id": wid})])
        result = resp[0]["result"]
        return result, json.loads(result["content"][0]["text"])

    def test_manager_fail_is_a_failed_run_and_the_consumer_never_runs(self):
        result, payload = self._run({"decision": "FAIL", "reason": "nothing to work with"})
        self.assertTrue(result["isError"], payload)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["partial_nodes"], [])
        self.assertIn("reported FAIL", payload["error"])
        self.assertIn("nothing to work with", payload["error"])
        self.assertEqual(payload["nodes"]["loop"]["status"], "failed")
        self.assertNotIn("consumer", payload["nodes"])
        self.assertEqual(_ScriptedEngine.calls, [("m", "manager")])

    def test_an_answer_that_is_no_decision_is_a_failed_run(self):
        for answer in ({"decision": "SHRUG"}, "sorry, no", None):
            with self.subTest(answer=answer):
                result, payload = self._run(answer)
                self.assertTrue(result["isError"], payload)
                self.assertEqual(payload["status"], "failed")
                self.assertNotIn("consumer", payload["nodes"])

    def test_budget_exhaustion_is_partial_not_complete(self):
        result, payload = self._run(
            {"decision": "DELEGATE", "workers": [{"agent": "w", "instructions": "do"}]})
        self.assertEqual(payload["status"], "partial", payload)
        self.assertEqual(payload["partial_nodes"], ["loop"])
        self.assertFalse(result["isError"], "a partial run is degraded, not an error")
        self.assertEqual(payload["nodes"]["loop"]["status"], "success")
        self.assertEqual(
            payload["final_state"]["loop"]["_terminal"]["state"], "partial")

    def test_a_completed_loop_stays_complete(self):
        result, payload = self._run({"decision": "COMPLETE", "result": {"score": 9}})
        self.assertFalse(result["isError"], payload)
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(payload["partial_nodes"], [])
        self.assertEqual(payload["nodes"]["consumer"]["status"], "success")


class TestNotifyTextForAPartialRun(TestOrchestrationServerBase):
    """``_notify_result_text`` is what a messenger user reads for a run that outlived its call."""

    def _result(self, manager_answer):
        from corvin_workflows import DAGRunner, WorkflowDoc, validate

        doc = WorkflowDoc.from_dict({
            "awp": "1.0.0", "workflow": {"name": "n", "description": "d"},
            "orchestration": {"engine": "dag", "graph": [{
                "id": "loop", "type": "delegation_loop", "agent": "a", "depends_on": [],
                "config": {"manager": "m", "budget": {"max_loops": 1, "max_total_workers": 3}}}]}})
        validate(doc)
        _ScriptedEngine.manager_answer = manager_answer
        return DAGRunner(doc, engine=_ScriptedEngine(), tenant_id="_default").run(inputs={})

    def test_partial_is_named_and_not_called_completed(self):
        text, ok = m._notify_result_text(
            "workflow 'w'",
            self._result({"decision": "DELEGATE", "workers": [{"agent": "w"}]}))
        self.assertTrue(ok)
        self.assertIn("PARTIAL", text)
        self.assertIn("loop", text)
        self.assertNotIn("completed.", text)

    def test_complete_and_failed_keep_their_text(self):
        text, ok = m._notify_result_text(
            "workflow 'w'", self._result({"decision": "COMPLETE", "result": {}}))
        self.assertEqual((text, ok), ("workflow 'w' completed.", True))
        text, ok = m._notify_result_text(
            "workflow 'w'", self._result({"decision": "FAIL", "reason": "no input"}))
        self.assertFalse(ok)
        self.assertIn("failed", text)
        self.assertIn("no input", text)

    def test_an_unknown_state_is_never_a_success(self):
        class _R:
            state = "cancelled"
            nodes: dict = {}
        text, ok = m._notify_result_text("workflow 'w'", _R())
        self.assertFalse(ok)
        self.assertIn("cancelled", text)

    def test_a_result_without_partial_nodes_is_read_as_not_partial(self):
        class _Old:
            state = "complete"
        self.assertEqual(m._partial_node_ids(_Old()), [])
        self.assertEqual(m._notify_result_text("x", _Old()), ("x completed.", True))


class TestAcsDelegateErrorIsAPositiveList(TestOrchestrationServerBase):
    def _delegate(self, result):
        with mock.patch.object(m, "_run_acs_workflow", lambda *a, **k: result):
            resp = _drive(self._server(), [_call("acs_delegate", {"task": "x"})])
        return resp[0]["result"]["isError"]

    def test_only_success_is_not_an_error(self):
        self.assertFalse(self._delegate({"run_id": "r", "status": "success", "summary": "ok"}))

    def test_every_other_status_is_an_error(self):
        # "failed" / "budget_exhausted": acs_runtime.ACSResult; "timeout": the budget watchdog;
        # the rest: statuses nothing emits today - a new one must not pass as clean.
        for status in ("failed", "budget_exhausted", "timeout", "partial", "completed",
                       "refused", "", None, 1):
            with self.subTest(status=status):
                self.assertTrue(self._delegate({"run_id": "r", "status": status}))

    def test_a_result_without_a_status_or_that_is_no_object_is_an_error(self):
        for result in ({}, {"summary": "looks fine"}, None, "ok", ["success"]):
            with self.subTest(result=result):
                self.assertTrue(m._acs_result_is_error(result))

    def test_the_real_dry_run_reports_a_status_on_the_list(self):
        """Positive control against the real runtime, not a mock: the status a real
        (dry) ACS run reports is on the list - the list is not an invention."""
        resp = _drive(self._server(), [_call("acs_delegate", {"task": "say hello", "dry_run": True})])
        payload = json.loads(resp[0]["result"]["content"][0]["text"])
        self.assertIn(payload["status"], m._ACS_OK_STATUSES)
        self.assertFalse(resp[0]["result"]["isError"], payload)

    def test_the_runtime_still_emits_budget_exhausted(self):
        """The status the old deny list let through is still a real one."""
        import acs_runtime
        import inspect
        self.assertIn('status="budget_exhausted"', inspect.getsource(acs_runtime))
