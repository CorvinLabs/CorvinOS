"""End-to-end tests for the it_support_ticket_agent example workflow
(ADR-0188's worked example) — all three paths: auto-resolved, confirmed
ticket creation, and declined. Uses StubEngine for deterministic, fast CI
coverage; the real-LLM run is exercised separately via the CLI
(`corvin_workflows run it_support_ticket_agent ... --engine claude`), not
in this file, to keep the test suite free of network/API dependence.

Run directly:  python3 core/workflows/tests/test_it_support_ticket_agent.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PKG_ROOT = _HERE.parent
sys.path.insert(0, str(_PKG_ROOT))

from corvin_workflows import DAGRunner, StubEngine, load_workflow, validate  # noqa: E402
from corvin_workflows.engines import EngineCall  # noqa: E402
from corvin_workflows.runner import resume_workflow  # noqa: E402

WORKFLOW_PATH = _PKG_ROOT / "corvin_workflows" / "examples" / "it_support_ticket_agent.awp.yaml"


def _classifier(category: str):
    def _fn(call: EngineCall) -> dict:
        return {"class": category}
    return _fn


def _draft(needs_confirmation: bool, confirm_text: str = ""):
    def _fn(call: EngineCall) -> dict:
        kb = (call.state.get("lookup_kb") or {})
        return {
            "text": kb.get("kb_fix") or "I'd like to take an action on your behalf.",
            "needs_confirmation": needs_confirmation,
            "confirm_action_text": confirm_text,
        }
    return _fn


class TicketAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_home = tempfile.mkdtemp(prefix="corvin_home_ticket_test_")
        self._prev_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self._tmp_home
        self.doc = load_workflow(WORKFLOW_PATH)
        validate(self.doc)
        self._outbox_dir = _PKG_ROOT.parent.parent / "operator" / "bridges" / "shared" / "outbox"
        self._outbox_before = set(self._outbox_dir.glob("wf_msg_*.json"))

    def tearDown(self) -> None:
        if self._prev_home is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = self._prev_home
        shutil.rmtree(self._tmp_home, ignore_errors=True)
        for f in set(self._outbox_dir.glob("wf_msg_*.json")) - self._outbox_before:
            f.unlink(missing_ok=True)

    def test_workflow_loads_and_validates(self) -> None:
        self.assertEqual(self.doc.name, "it_support_ticket_agent")
        self.assertEqual(self.doc.engine, "chat")
        self.assertEqual(len(self.doc.graph), 10)

    def test_auto_resolved_path_never_pauses(self) -> None:
        engine = StubEngine(default=lambda call: (
            {"class": "password_account"} if call.agent == "triage_classifier"
            else _draft(False)(call) if call.agent == "assistant"
            else {}
        ))
        result = DAGRunner(self.doc, engine=engine).run(
            inputs={"chat_id": "1001", "query": "I forgot my password"}
        )
        self.assertEqual(result.state, "complete", result.error)
        self.assertEqual(result.nodes["lookup_kb"].output["kb_hit"], True)
        self.assertEqual(result.nodes["answer_resolved"].status, "success")
        self.assertEqual(result.nodes["confirm_action"].status, "skipped")
        self.assertEqual(result.nodes["build_ticket_payload"].status, "skipped")
        self.assertEqual(result.nodes["answer_declined"].status, "skipped")

    def test_needs_confirmation_path_pauses_then_creates_ticket_on_yes(self) -> None:
        engine = StubEngine(default=lambda call: (
            {"class": "hardware"} if call.agent == "triage_classifier"
            else _draft(True, "Reassign asset tag to the requester")(call) if call.agent == "assistant"
            else {}
        ))
        runner = DAGRunner(self.doc, engine=engine)
        paused = runner.run(inputs={"chat_id": "1002", "query": "my laptop screen is broken"})

        self.assertEqual(paused.state, "paused")
        self.assertEqual(paused.paused_at_node, "confirm_action")
        # hardware DOES have a KB entry (kb_hit=True) — it still needs
        # confirmation per the draft_answer instructions' special case
        # (asset reassignment is a real-world action), demonstrating that
        # "needs_confirmation" is an independent judgment, not just !kb_hit.
        self.assertEqual(paused.nodes["lookup_kb"].output["kb_hit"], True)

        resumed = resume_workflow(paused.run_id, "ja, bitte", engine=engine)
        self.assertEqual(resumed.state, "complete", resumed.error)
        self.assertEqual(resumed.nodes["confirmation_gate"].output["case"], "confirmed")
        self.assertEqual(resumed.nodes["build_ticket_payload"].status, "success")
        payload = resumed.nodes["build_ticket_payload"].output["payload"]
        self.assertEqual(payload["category"], "hardware")
        self.assertEqual(payload["ticket_id"], "TCK-HAR-0001")
        self.assertEqual(resumed.nodes["answer_ticket_created"].status, "success")
        self.assertEqual(resumed.nodes["answer_declined"].status, "skipped")

    def test_needs_confirmation_path_declined(self) -> None:
        engine = StubEngine(default=lambda call: (
            {"class": "other"} if call.agent == "triage_classifier"
            else _draft(True, "Escalate to a human technician")(call) if call.agent == "assistant"
            else {}
        ))
        runner = DAGRunner(self.doc, engine=engine)
        paused = runner.run(inputs={"chat_id": "1003", "query": "my desk phone is possessed"})
        self.assertEqual(paused.state, "paused")

        resumed = resume_workflow(paused.run_id, "nein danke", engine=engine)
        self.assertEqual(resumed.state, "complete", resumed.error)
        self.assertEqual(resumed.nodes["confirmation_gate"].output["case"], "declined")
        self.assertEqual(resumed.nodes["answer_declined"].status, "success")
        self.assertEqual(resumed.nodes["build_ticket_payload"].status, "skipped")
        self.assertEqual(resumed.nodes["answer_ticket_created"].status, "skipped")

    def test_outbox_message_sent_on_pause(self) -> None:
        engine = StubEngine(default=lambda call: (
            {"class": "network"} if call.agent == "triage_classifier"
            else _draft(True, "restart the router")(call) if call.agent == "assistant"
            else {}
        ))
        DAGRunner(self.doc, engine=engine).run(inputs={"chat_id": "1004", "query": "vpn is down"})
        written = set(self._outbox_dir.glob("wf_msg_*.json")) - self._outbox_before
        self.assertEqual(len(written), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
