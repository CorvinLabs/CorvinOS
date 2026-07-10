"""Tests for ADR-0188 M5 (checkpoint/resume) and M6 (ask_human/answer).

Uses a temp CORVIN_HOME so checkpoint files never touch the real repo's
.corvin/ directory. The bridge outbox write (shared across all chat-facing
node types) is real filesystem I/O by design (it's the same directory the
messenger daemons poll) — tests clean up what they write.

Run directly:  python3 core/workflows/tests/test_pause_resume.py
"""
from __future__ import annotations

import glob
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PKG_ROOT = _HERE.parent
sys.path.insert(0, str(_PKG_ROOT))

from corvin_workflows import DAGRunner, StubEngine, WorkflowDoc, validate  # noqa: E402
from corvin_workflows.runner import resume_workflow  # noqa: E402

_OUTBOX_DIR = _PKG_ROOT.parent.parent / "operator" / "bridges" / "shared" / "outbox"


def _doc(graph: list[dict], **kw) -> WorkflowDoc:
    d = WorkflowDoc(
        awp_version=kw.get("awp_version", "1.1.0"),
        name=kw.get("name", "test_pause_resume"),
        description=kw.get("description", "test"),
        inputs=kw.get("inputs", {}),
        orchestration={"engine": kw.get("engine", "dag"), "graph": graph},
        raw={},
    )
    return d


class PauseResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_home = tempfile.mkdtemp(prefix="corvin_home_test_")
        self._prev_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp_home
        self._outbox_before = set(glob.glob(str(_OUTBOX_DIR / "wf_msg_*.json")))

    def tearDown(self) -> None:
        if self._prev_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._prev_home
        shutil.rmtree(self._tmp_home, ignore_errors=True)
        # Clean up any outbox files this test wrote.
        after = set(glob.glob(str(_OUTBOX_DIR / "wf_msg_*.json")))
        for f in after - self._outbox_before:
            Path(f).unlink(missing_ok=True)

    def _write_workflow_file(self, graph: list[dict]) -> str:
        """A real file is required — resume() reloads the workflow by path."""
        import yaml

        tmpdir = tempfile.mkdtemp(prefix="awp_wf_")
        path = Path(tmpdir) / "wf.awp.yaml"
        path.write_text(yaml.dump({
            "awp": "1.1.0",
            "workflow": {"name": "confirm_flow", "description": "test"},
            "inputs": {"chat_id": {"type": "string"}},
            "orchestration": {"engine": "dag", "graph": graph},
        }))
        self.addCleanup(lambda: shutil.rmtree(tmpdir, ignore_errors=True))
        return str(path)

    def test_ask_human_pauses_and_checkpoints(self) -> None:
        graph = [
            {"id": "extract", "type": "agent", "agent": "extractor", "depends_on": []},
            {
                "id": "confirm", "type": "ask_human", "depends_on": ["extract"],
                "channel": "discord", "chat_id": "12345",
                "prompt": "Confirm the action? (ja/nein)",
                "expect": {"field": "confirmed", "type": "boolean"},
            },
        ]
        path = self._write_workflow_file(graph)
        from corvin_workflows.storage import load_workflow
        doc = load_workflow(path)
        validate(doc)
        engine = StubEngine(responses={"extractor": {"title": "reset password"}})
        runner = DAGRunner(doc, engine=engine)
        result = runner.run(inputs={"chat_id": "12345"})

        self.assertEqual(result.state, "paused")
        self.assertEqual(result.paused_at_node, "confirm")
        self.assertIn("Confirm", result.paused_prompt)
        self.assertIsNotNone(result.run_id)
        # extract already ran and is reflected in state
        self.assertIn("extract", result.final_state)

        from corvin_workflows import checkpoint
        ckpt = checkpoint.load(result.run_id)
        self.assertIsNotNone(ckpt)
        self.assertEqual(ckpt["paused_at_node"], "confirm")
        self.assertEqual(ckpt["completed_ids"], ["extract"])

    def test_ask_human_writes_outbox_prompt(self) -> None:
        graph = [{
            "id": "confirm", "type": "ask_human", "depends_on": [],
            "channel": "discord", "chat_id": "999",
            "prompt": "Proceed?", "expect": {"field": "confirmed", "type": "boolean"},
        }]
        path = self._write_workflow_file(graph)
        from corvin_workflows.storage import load_workflow
        doc = load_workflow(path)
        validate(doc)
        runner = DAGRunner(doc, engine=StubEngine())
        result = runner.run()
        self.assertEqual(result.state, "paused")

        written = set(glob.glob(str(_OUTBOX_DIR / "wf_msg_*.json"))) - self._outbox_before
        self.assertEqual(len(written), 1, "ask_human must write exactly one outbox message")
        import json
        envelope = json.loads(Path(next(iter(written))).read_text())
        self.assertEqual(envelope["chat_id"], "999")
        self.assertEqual(envelope["text"], "Proceed?")
        self.assertTrue(envelope["_workflow_ask_human"])

    def test_resume_with_confirming_reply_continues_run(self) -> None:
        graph = [
            {"id": "extract", "type": "agent", "agent": "extractor", "depends_on": []},
            {
                "id": "confirm", "type": "ask_human", "depends_on": ["extract"],
                "channel": "discord", "chat_id": "12345",
                "prompt": "Confirm?", "expect": {"field": "confirmed", "type": "boolean"},
            },
            {
                "id": "act", "type": "agent", "agent": "actor", "depends_on": ["confirm"],
            },
        ]
        path = self._write_workflow_file(graph)
        from corvin_workflows.storage import load_workflow
        doc = load_workflow(path)
        validate(doc)
        engine = StubEngine(responses={
            "extractor": {"title": "reset password"},
            "actor": {"done": True},
        })
        runner = DAGRunner(doc, engine=engine)
        paused = runner.run(inputs={"chat_id": "12345"})
        self.assertEqual(paused.state, "paused")

        resumed = resume_workflow(paused.run_id, "ja", engine=engine)
        self.assertEqual(resumed.state, "complete", resumed.error)
        self.assertEqual(resumed.nodes["confirm"].output["confirmed"], True)
        self.assertEqual(resumed.nodes["act"].output["done"], True)
        # extract must NOT have been called again on resume.
        extractor_calls = [c for c in engine.history if c.agent == "extractor"]
        self.assertEqual(len(extractor_calls), 1)

    def test_resume_with_declining_reply(self) -> None:
        graph = [{
            "id": "confirm", "type": "ask_human", "depends_on": [],
            "channel": "discord", "chat_id": "1",
            "prompt": "Confirm?", "expect": {"field": "confirmed", "type": "boolean"},
        }]
        path = self._write_workflow_file(graph)
        from corvin_workflows.storage import load_workflow
        doc = load_workflow(path)
        validate(doc)
        runner = DAGRunner(doc, engine=StubEngine())
        paused = runner.run(inputs={"chat_id": "1"})
        resumed = resume_workflow(paused.run_id, "nein", engine=StubEngine())
        self.assertEqual(resumed.state, "complete")
        self.assertFalse(resumed.nodes["confirm"].output["confirmed"])

    def test_checkpoint_deleted_after_successful_resume(self) -> None:
        graph = [{
            "id": "confirm", "type": "ask_human", "depends_on": [],
            "channel": "discord", "chat_id": "1",
            "prompt": "Confirm?", "expect": {"field": "confirmed", "type": "boolean"},
        }]
        path = self._write_workflow_file(graph)
        from corvin_workflows.storage import load_workflow
        doc = load_workflow(path)
        validate(doc)
        runner = DAGRunner(doc, engine=StubEngine())
        paused = runner.run(inputs={"chat_id": "1"})

        from corvin_workflows import checkpoint
        self.assertIsNotNone(checkpoint.load(paused.run_id))
        resume_workflow(paused.run_id, "ja", engine=StubEngine())
        self.assertIsNone(checkpoint.load(paused.run_id), "checkpoint must be deleted after completion")

    def test_resume_unknown_run_id_raises(self) -> None:
        with self.assertRaises(KeyError):
            resume_workflow("does-not-exist", "ja", engine=StubEngine())

    def test_route_skip_state_survives_pause_and_resume(self) -> None:
        """A route decision made BEFORE the pause must still be honored after
        resume — the skipped branch must not execute post-resume either."""
        graph = [
            {
                "id": "gate", "type": "route", "mode": "condition", "depends_on": [],
                "cases": [
                    {"id": "needs_confirm", "when": {"selector": "risky", "op": "==", "value": True}},
                    {"id": "skip_path", "when": "default"},
                ],
            },
            {
                "id": "confirm", "type": "ask_human", "depends_on": ["gate"], "branch": "needs_confirm",
                "channel": "discord", "chat_id": "1", "prompt": "Confirm?",
                "expect": {"field": "confirmed", "type": "boolean"},
            },
            {"id": "never_taken", "type": "agent", "agent": "never_called",
             "depends_on": ["gate"], "branch": "skip_path"},
        ]
        path = self._write_workflow_file(graph)
        from corvin_workflows.storage import load_workflow
        doc = load_workflow(path)
        validate(doc)
        engine = StubEngine()
        runner = DAGRunner(doc, engine=engine)
        paused = runner.run(inputs={"chat_id": "1", "risky": True})
        self.assertEqual(paused.state, "paused")

        resumed = resume_workflow(paused.run_id, "ja", engine=engine)
        self.assertEqual(resumed.state, "complete", resumed.error)
        self.assertEqual(resumed.nodes["never_taken"].status, "skipped")
        called_agents = {c.agent for c in engine.history}
        self.assertNotIn("never_called", called_agents)

    def test_skipped_sibling_materialized_before_pause_survives_resume(self) -> None:
        """Regression: when the skipped sibling sorts BEFORE the pausing node
        within the same level, the runner actually iterates to it and gives
        it a NodeResult (status='skipped') before the pause happens — so it
        lands in BOTH `run.nodes` (-> completed_ids) and the `skipped` set
        (-> skipped_ids) at checkpoint time. An earlier implementation let
        those two id sets overlap; on resume, the `if nid in done: continue`
        check fired before the `skipped` check ever ran, silently dropping
        the node from the resumed run's `nodes` entirely (neither skipped
        nor success — just gone)."""
        graph = [
            {
                "id": "gate", "type": "route", "mode": "condition", "depends_on": [],
                "cases": [
                    {"id": "needs_confirm", "when": {"selector": "risky", "op": "==", "value": True}},
                    {"id": "skip_path", "when": "default"},
                ],
            },
            # Sorts BEFORE the ask_human node within the same level, so the
            # runner actually reaches and materializes it as skipped before
            # the pause.
            {"id": "aaa_never_taken", "type": "agent", "agent": "never_called",
             "depends_on": ["gate"], "branch": "skip_path"},
            {
                "id": "zzz_confirm", "type": "ask_human", "depends_on": ["gate"], "branch": "needs_confirm",
                "channel": "discord", "chat_id": "1", "prompt": "Confirm?",
                "expect": {"field": "confirmed", "type": "boolean"},
            },
        ]
        path = self._write_workflow_file(graph)
        from corvin_workflows.storage import load_workflow
        doc = load_workflow(path)
        validate(doc)
        engine = StubEngine()
        runner = DAGRunner(doc, engine=engine)
        paused = runner.run(inputs={"chat_id": "1", "risky": True})
        self.assertEqual(paused.state, "paused")
        self.assertEqual(paused.nodes["aaa_never_taken"].status, "skipped")

        from corvin_workflows import checkpoint
        ckpt = checkpoint.load(paused.run_id)
        self.assertIsNotNone(ckpt)
        self.assertEqual(
            set(ckpt["completed_ids"]) & set(ckpt["skipped_ids"]), set(),
            "completed_ids and skipped_ids must never overlap",
        )

        resumed = resume_workflow(paused.run_id, "ja", engine=engine)
        self.assertEqual(resumed.state, "complete", resumed.error)
        self.assertIn("aaa_never_taken", resumed.nodes,
                       "a node skipped before the pause must not disappear from the resumed run")
        self.assertEqual(resumed.nodes["aaa_never_taken"].status, "skipped")


class AnswerNodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._outbox_before = set(glob.glob(str(_OUTBOX_DIR / "wf_msg_*.json")))

    def tearDown(self) -> None:
        after = set(glob.glob(str(_OUTBOX_DIR / "wf_msg_*.json")))
        for f in after - self._outbox_before:
            Path(f).unlink(missing_ok=True)

    def test_answer_sends_and_completes_without_pausing(self) -> None:
        doc = _doc([
            {"id": "draft", "type": "agent", "agent": "drafter", "depends_on": []},
            {
                "id": "reply", "type": "answer", "depends_on": ["draft"],
                "channel": "discord", "chat_id": "42", "text_from": "draft.text",
            },
        ])
        validate(doc)
        engine = StubEngine(responses={"drafter": {"text": "Here is your answer."}})
        result = DAGRunner(doc, engine=engine).run()
        self.assertEqual(result.state, "complete", result.error)
        self.assertTrue(result.nodes["reply"].output["sent"])

        written = set(glob.glob(str(_OUTBOX_DIR / "wf_msg_*.json"))) - self._outbox_before
        self.assertEqual(len(written), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
