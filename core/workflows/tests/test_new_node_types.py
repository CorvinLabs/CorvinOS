"""Unit + integration tests for the ADR-0188 node types: code, merge, route,
plus per-node retry. Each test class covers one milestone.

Run directly:  python3 core/workflows/tests/test_new_node_types.py
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PKG_ROOT = _HERE.parent
sys.path.insert(0, str(_PKG_ROOT))

from corvin_workflows import DAGRunner, StubEngine, WorkflowDoc, validate  # noqa: E402
from corvin_workflows.code_exec import CodeExecutionError, run_sandboxed_python  # noqa: E402
from corvin_workflows.validator import WorkflowInvalid  # noqa: E402


def _doc(graph: list[dict], **kw) -> WorkflowDoc:
    return WorkflowDoc(
        awp_version=kw.get("awp_version", "1.1.0"),
        name=kw.get("name", "test_workflow"),
        description=kw.get("description", "test"),
        inputs=kw.get("inputs", {}),
        orchestration={"engine": kw.get("engine", "dag"), "graph": graph},
        raw={},
    )


class CodeNodeSandboxTests(unittest.TestCase):
    """Tier-2: exercises the real bwrap sandbox subprocess, not a mock."""

    def test_returns_declared_outputs(self) -> None:
        src = "def main(a: int, b: int) -> dict:\n    return {'sum': a + b}\n"
        result = run_sandboxed_python(src, {"a": 3, "b": 4})
        self.assertEqual(result, {"sum": 7})

    def test_braces_in_source_do_not_break_templating(self) -> None:
        """Regression: an earlier implementation used str.format() on the
        whole source, which broke on any dict/set/f-string literal."""
        src = (
            "def main(x: str) -> dict:\n"
            "    d = {'a': 1, 'b': {'nested': True}}\n"
            "    return {'echo': x, 'dict': d}\n"
        )
        result = run_sandboxed_python(src, {"x": "hello"})
        self.assertEqual(result["echo"], "hello")
        self.assertEqual(result["dict"], {"a": 1, "b": {"nested": True}})

    def test_network_is_denied(self) -> None:
        src = (
            "import socket\n"
            "def main() -> dict:\n"
            "    try:\n"
            "        socket.create_connection(('1.1.1.1', 80), timeout=2)\n"
            "        return {'reachable': True}\n"
            "    except OSError:\n"
            "        return {'reachable': False}\n"
        )
        result = run_sandboxed_python(src, {}, timeout_s=5)
        self.assertFalse(result["reachable"], "code node must not have network access")

    def test_timeout_enforced(self) -> None:
        src = "def main() -> dict:\n    while True:\n        pass\n"
        t0 = time.time()
        with self.assertRaises(CodeExecutionError):
            run_sandboxed_python(src, {}, timeout_s=2)
        self.assertLess(time.time() - t0, 5, "timeout should fire close to the configured bound")

    def test_missing_main_raises(self) -> None:
        with self.assertRaises(CodeExecutionError):
            run_sandboxed_python("x = 1\n", {})

    def test_non_dict_return_raises(self) -> None:
        with self.assertRaises(CodeExecutionError):
            run_sandboxed_python("def main() -> dict:\n    return 'not a dict'\n", {})


class CodeNodeGraphTests(unittest.TestCase):
    """Tier-3: through DAGRunner, node-envelope validation + state wiring."""

    def test_validator_rejects_missing_main(self) -> None:
        doc = _doc([{
            "id": "build", "type": "code", "depends_on": [],
            "language": "python3", "source": "x = 1", "outputs": ["y"],
        }])
        with self.assertRaises(WorkflowInvalid) as ctx:
            validate(doc)
        self.assertEqual(ctx.exception.code, "R10")

    def test_validator_rejects_bad_language(self) -> None:
        doc = _doc([{
            "id": "build", "type": "code", "depends_on": [],
            "language": "ruby", "source": "def main(): pass", "outputs": ["y"],
        }])
        with self.assertRaises(WorkflowInvalid):
            validate(doc)

    def test_code_node_resolves_selector_from_upstream_state(self) -> None:
        doc = _doc([
            {"id": "extract", "type": "agent", "agent": "extractor", "depends_on": []},
            {
                "id": "build_payload", "type": "code", "depends_on": ["extract"],
                "language": "python3",
                "source": (
                    "def main(title: str, priority: str) -> dict:\n"
                    "    weight = {'low': 1, 'medium': 2, 'high': 3}.get(priority, 2)\n"
                    "    return {'payload': {'subject': title, 'weight': weight}}\n"
                ),
                "inputs": {"title": "extract.title", "priority": "extract.priority"},
                "outputs": ["payload"],
            },
        ])
        validate(doc)
        engine = StubEngine(responses={"extractor": {"title": "VPN down", "priority": "high"}})
        runner = DAGRunner(doc, engine=engine)
        result = runner.run()
        self.assertEqual(result.state, "complete", result.error)
        self.assertEqual(
            result.nodes["build_payload"].output["payload"],
            {"subject": "VPN down", "weight": 3},
        )
        # The code node must NOT have touched the engine.
        self.assertEqual(len(engine.history), 1)  # only the extract agent call

    def test_code_node_missing_declared_output_fails_run(self) -> None:
        doc = _doc([{
            "id": "build", "type": "code", "depends_on": [],
            "language": "python3",
            "source": "def main() -> dict:\n    return {'wrong_key': 1}\n",
            "outputs": ["expected_key"],
        }])
        validate(doc)
        runner = DAGRunner(doc, engine=StubEngine())
        result = runner.run()
        self.assertEqual(result.state, "failed")
        self.assertIn("expected_key", result.error)


class MergeNodeTests(unittest.TestCase):
    def test_concat_list_strategy(self) -> None:
        doc = _doc([
            {"id": "a", "type": "agent", "agent": "a_agent", "depends_on": []},
            {"id": "b", "type": "agent", "agent": "b_agent", "depends_on": []},
            {
                "id": "combined", "type": "merge", "depends_on": ["a", "b"],
                "strategy": "concat_list", "inputs": ["a.items", "b.items"], "output": "all_items",
            },
        ])
        validate(doc)
        engine = StubEngine(responses={
            "a_agent": {"items": [1, 2]},
            "b_agent": {"items": [3, 4]},
        })
        result = DAGRunner(doc, engine=engine).run()
        self.assertEqual(result.state, "complete", result.error)
        self.assertEqual(result.nodes["combined"].output["all_items"], [1, 2, 3, 4])
        self.assertEqual(len(engine.history), 2, "merge node must not call the engine")

    def test_dict_union_strategy(self) -> None:
        doc = _doc([
            {"id": "a", "type": "agent", "agent": "a_agent", "depends_on": []},
            {"id": "b", "type": "agent", "agent": "b_agent", "depends_on": []},
            {
                "id": "combined", "type": "merge", "depends_on": ["a", "b"],
                "strategy": "dict_union", "inputs": ["a", "b"], "output": "merged",
            },
        ])
        validate(doc)
        engine = StubEngine(responses={"a_agent": {"x": 1}, "b_agent": {"y": 2}})
        result = DAGRunner(doc, engine=engine).run()
        self.assertEqual(result.nodes["combined"].output["merged"], {"x": 1, "y": 2})

    def test_first_non_empty_strategy(self) -> None:
        doc = _doc([
            {"id": "a", "type": "agent", "agent": "a_agent", "depends_on": []},
            {"id": "b", "type": "agent", "agent": "b_agent", "depends_on": []},
            {
                "id": "combined", "type": "merge", "depends_on": ["a", "b"],
                "strategy": "first_non_empty", "inputs": ["a.val", "b.val"], "output": "picked",
            },
        ])
        validate(doc)
        engine = StubEngine(responses={"a_agent": {"val": None}, "b_agent": {"val": "fallback"}})
        result = DAGRunner(doc, engine=engine).run()
        self.assertEqual(result.nodes["combined"].output["picked"], "fallback")

    def test_first_non_empty_keeps_a_real_zero(self) -> None:
        """Regression: an earlier implementation used truthiness (`if v`),
        which treated a legitimate 0 as 'empty' and skipped it in favor of
        a later, non-zero fallback — 0 must win here since it is present
        (`is not None`), not falsy-therefore-missing."""
        doc = _doc([
            {"id": "a", "type": "agent", "agent": "a_agent", "depends_on": []},
            {"id": "b", "type": "agent", "agent": "b_agent", "depends_on": []},
            {
                "id": "combined", "type": "merge", "depends_on": ["a", "b"],
                "strategy": "first_non_empty", "inputs": ["a.val", "b.val"], "output": "picked",
            },
        ])
        validate(doc)
        engine = StubEngine(responses={"a_agent": {"val": 0}, "b_agent": {"val": 99}})
        result = DAGRunner(doc, engine=engine).run()
        self.assertEqual(result.nodes["combined"].output["picked"], 0)

    def test_validator_rejects_bad_strategy(self) -> None:
        doc = _doc([{
            "id": "combined", "type": "merge", "depends_on": [],
            "strategy": "average", "inputs": ["a.x"], "output": "y",
        }])
        with self.assertRaises(WorkflowInvalid):
            validate(doc)


class RouteConditionTests(unittest.TestCase):
    def _workflow(self, confidence: float):
        return _doc([
            {"id": "draft", "type": "agent", "agent": "drafter", "depends_on": []},
            {
                "id": "gate", "type": "route", "mode": "condition", "depends_on": ["draft"],
                "cases": [
                    {"id": "resolved", "when": {"selector": "draft.confidence", "op": ">=", "value": 0.7}},
                    {"id": "needs_action", "when": "default"},
                ],
            },
            {"id": "answer_resolved", "type": "agent", "agent": "resolver",
             "depends_on": ["gate"], "branch": "resolved"},
            {"id": "escalate", "type": "agent", "agent": "escalator",
             "depends_on": ["gate"], "branch": "needs_action"},
        ], engine="dag")

    def test_high_confidence_takes_resolved_branch(self) -> None:
        doc = self._workflow(0.9)
        validate(doc)
        engine = StubEngine(responses={
            "drafter": {"confidence": 0.9}, "resolver": {"ok": True}, "escalator": {"ok": True},
        })
        result = DAGRunner(doc, engine=engine).run()
        self.assertEqual(result.state, "complete", result.error)
        self.assertEqual(result.nodes["answer_resolved"].status, "success")
        self.assertEqual(result.nodes["escalate"].status, "skipped")
        # The skipped branch must never touch the engine.
        called_agents = {c.agent for c in engine.history}
        self.assertNotIn("escalator", called_agents)

    def test_low_confidence_takes_default_branch(self) -> None:
        doc = self._workflow(0.2)
        validate(doc)
        engine = StubEngine(responses={
            "drafter": {"confidence": 0.2}, "resolver": {"ok": True}, "escalator": {"ok": True},
        })
        result = DAGRunner(doc, engine=engine).run()
        self.assertEqual(result.nodes["escalate"].status, "success")
        self.assertEqual(result.nodes["answer_resolved"].status, "skipped")

    def test_validator_requires_default_case(self) -> None:
        doc = _doc([{
            "id": "gate", "type": "route", "mode": "condition", "depends_on": [],
            "cases": [{"id": "a", "when": {"selector": "x.y", "op": "==", "value": 1}}],
        }])
        with self.assertRaises(WorkflowInvalid):
            validate(doc)

    def test_validator_rejects_duplicate_default_cases(self) -> None:
        """Regression: an earlier implementation only checked 'at least one
        default', contradicting its own error message ('requires exactly
        one'). Two default cases must be rejected, not silently accepted
        with the last one winning at execution time."""
        doc = _doc([{
            "id": "gate", "type": "route", "mode": "condition", "depends_on": [],
            "cases": [
                {"id": "a", "when": "default"},
                {"id": "b", "when": "default"},
            ],
        }])
        with self.assertRaises(WorkflowInvalid):
            validate(doc)

    def test_validator_rejects_free_form_eval_string(self) -> None:
        """Structured {selector, op, value} only — no eval() of arbitrary strings."""
        doc = _doc([{
            "id": "gate", "type": "route", "mode": "condition", "depends_on": [],
            "cases": [
                {"id": "a", "when": "{{state.x.y}} >= 0.7"},
                {"id": "b", "when": "default"},
            ],
        }])
        with self.assertRaises(WorkflowInvalid):
            validate(doc)


class RouteClassifyTests(unittest.TestCase):
    def test_classify_routes_to_matching_branch(self) -> None:
        doc = _doc([
            {
                "id": "triage", "type": "route", "mode": "classify", "depends_on": [],
                "agent": "classifier", "classes": ["network", "hardware", "other"],
                "input": "inputs.ticket_text",
            },
            {"id": "network_path", "type": "agent", "agent": "net_fixer",
             "depends_on": ["triage"], "branch": "network"},
            {"id": "hardware_path", "type": "agent", "agent": "hw_fixer",
             "depends_on": ["triage"], "branch": "hardware"},
            {"id": "other_path", "type": "agent", "agent": "generic_fixer",
             "depends_on": ["triage"], "branch": "other"},
        ], inputs={"ticket_text": {"type": "string"}})
        validate(doc)
        engine = StubEngine(responses={
            "classifier": {"class": "network"},
            "net_fixer": {"ok": True}, "hw_fixer": {"ok": True}, "generic_fixer": {"ok": True},
        })
        result = DAGRunner(doc, engine=engine).run(inputs={"ticket_text": "VPN won't connect"})
        self.assertEqual(result.nodes["network_path"].status, "success")
        self.assertEqual(result.nodes["hardware_path"].status, "skipped")
        self.assertEqual(result.nodes["other_path"].status, "skipped")

    def test_classify_rejects_out_of_band_class(self) -> None:
        doc = _doc([{
            "id": "triage", "type": "route", "mode": "classify", "depends_on": [],
            "agent": "classifier", "classes": ["a", "b"], "input": "inputs.x",
        }], inputs={"x": {"type": "string"}})
        validate(doc)
        engine = StubEngine(responses={"classifier": {"class": "not_a_real_class"}})
        result = DAGRunner(doc, engine=engine).run(inputs={"x": "hi"})
        self.assertEqual(result.state, "failed")
        self.assertIn("not_a_real_class", result.error)


class RetryTests(unittest.TestCase):
    def test_retries_then_succeeds(self) -> None:
        calls = {"n": 0}

        def flaky(call):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("transient failure")
            return {"ok": True}

        doc = _doc([{
            "id": "flaky_step", "type": "agent", "agent": "flaky_agent", "depends_on": [],
            "retry": {"max_retries": 3, "retry_interval_s": 0},
        }])
        validate(doc)
        engine = StubEngine(default=flaky)
        result = DAGRunner(doc, engine=engine).run()
        self.assertEqual(result.state, "complete", result.error)
        self.assertEqual(result.nodes["flaky_step"].attempts, 3)
        self.assertEqual(calls["n"], 3)

    def test_exhausted_retries_default_strategy_aborts_run(self) -> None:
        doc = _doc([{
            "id": "always_fails", "type": "agent", "agent": "bad_agent", "depends_on": [],
            "retry": {"max_retries": 2, "retry_interval_s": 0},
        }])
        validate(doc)
        engine = StubEngine(default=lambda call: (_ for _ in ()).throw(RuntimeError("nope")))
        result = DAGRunner(doc, engine=engine).run()
        self.assertEqual(result.state, "failed")
        self.assertEqual(result.nodes["always_fails"].attempts, 3)  # 1 + 2 retries

    def test_exhausted_retries_fail_branch_strategy_continues_run(self) -> None:
        doc = _doc([
            {
                "id": "risky", "type": "agent", "agent": "bad_agent", "depends_on": [],
                "retry": {"max_retries": 1, "retry_interval_s": 0, "error_strategy": "fail_branch"},
            },
            {"id": "downstream_of_risky", "type": "agent", "agent": "never_called",
             "depends_on": ["risky"]},
            {"id": "independent", "type": "agent", "agent": "independent_agent", "depends_on": []},
        ])
        validate(doc)
        engine = StubEngine(responses={"independent_agent": {"ok": True}})
        engine.default = lambda call: (
            (_ for _ in ()).throw(RuntimeError("boom"))
            if call.agent == "bad_agent" else {"ok": True}
        )
        result = DAGRunner(doc, engine=engine).run()
        self.assertEqual(result.state, "complete", result.error)
        self.assertEqual(result.nodes["risky"].status, "failed")
        self.assertEqual(result.nodes["downstream_of_risky"].status, "skipped")
        self.assertEqual(result.nodes["independent"].status, "success")
        called_agents = {c.agent for c in engine.history}
        self.assertNotIn("never_called", called_agents)

    def test_no_retry_key_is_unchanged_legacy_behavior(self) -> None:
        doc = _doc([{"id": "n", "type": "agent", "agent": "bad_agent", "depends_on": []}])
        validate(doc)
        engine = StubEngine(default=lambda call: (_ for _ in ()).throw(RuntimeError("boom")))
        result = DAGRunner(doc, engine=engine).run()
        self.assertEqual(result.state, "failed")
        self.assertEqual(result.nodes["n"].attempts, 1)


class AskHumanConsentCoercionTests(unittest.TestCase):
    """HITL consent must be fail-closed: a negated reply is never consent."""

    def _coerce(self, raw: str):
        from corvin_workflows.node_types import _coerce_reply
        return _coerce_reply(raw, "boolean")

    def test_plain_affirmatives_are_true(self) -> None:
        for r in ("ja", "yes", "y", "ok", "okay", "sure", "confirm", "1"):
            self.assertTrue(self._coerce(r), f"{r!r} should be consent")

    def test_plain_negatives_are_false(self) -> None:
        for r in ("nein", "no", "n", "cancel", "decline", "nope", "0", "stop"):
            self.assertFalse(self._coerce(r), f"{r!r} should NOT be consent")

    def test_negated_affirmatives_are_false(self) -> None:
        # The regression: "not ok" used to coerce to TRUE (consent on refusal).
        for r in ("not ok", "not okay", "no, that's not ok", "please don't",
                  "never", "do not confirm", "nicht ok", "reject this"):
            self.assertFalse(self._coerce(r), f"{r!r} must be fail-closed (not consent)")

    def test_unrecognized_is_fail_closed(self) -> None:
        self.assertFalse(self._coerce("hmm maybe later"))
        self.assertFalse(self._coerce(""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
