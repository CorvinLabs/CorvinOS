"""LIVE end-to-end proof of the ADR-0613 learning loop with a REAL LLM turn.

Opt-in (``CLAUDE_LIVE_E2E=1`` + the ``claude`` CLI on PATH; costs API credits):

1. sandboxed console (real router, real auth session, BOOTED ACP registry with
   the audit-first learning store) — the ``_sandbox`` of
   ``test_learning_loop_routes_e2e.py``;
2. a REAL chat turn over the real WebSocket (``/chat/sessions/{sid}/stream``):
   the console spawns ``claude -p`` for the answer — nothing about the engine
   is mocked;
3. then the loop's evidence is read back from DISK:
   * ``skill_executed`` learning event(s) for ``os.delegation_router`` in
     shadow mode, carrying the bundled engine (source side, F1) — every one
     joined to the core hash chain via ``audit_ref``;
   * an ``outcome`` learning event for the finished task, with the task's own
     tenant (sink side);
   * ``learning.skill_executed`` / ``learning.outcome`` records in the core chain;
4. and the operator closes the loop through the real HTTP API: feedback on
   that task → hypotheses → the optimizer epoch advanced and persisted.

Run: CLAUDE_LIVE_E2E=1 core/console/.venv/bin/python -m pytest \
     core/console/tests/test_learning_loop_live_e2e.py -q -s
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from test_learning_loop_routes_e2e import _chain, _events_on_disk, _sandbox  # noqa: E402

live = pytest.mark.skipif(
    os.environ.get("CLAUDE_LIVE_E2E", "") != "1" or shutil.which("claude") is None,
    reason="live learning-loop E2E needs CLAUDE_LIVE_E2E=1 and the claude CLI",
)


def _wait(pred, timeout=10.0):
    for _ in range(int(timeout / 0.1)):
        if pred():
            return True
        time.sleep(0.1)
    return pred()


@live
class LearningLoopLiveE2E(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def test_real_turn_produces_shadow_decision_outcome_and_feedback_closes_loop(self):
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, emitter, chain):
            # ── 1. a real chat session + one REAL turn over the WebSocket ──────
            r = client.post("/v1/console/chat/sessions", json={"title": "live learning loop"})
            self.assertIn(r.status_code, (200, 201), r.text)
            body = r.json()
            sid = (body.get("session") or {}).get("sid") or body.get("sid") or body.get("session_id")
            self.assertTrue(sid, r.text)

            types: list[str] = []
            texts: list[str] = []
            with client.websocket_connect(f"/v1/console/chat/sessions/{sid}/stream") as ws:
                first = ws.receive_json()
                self.assertEqual(first["type"], "ready", first)
                ws.send_json({"type": "user", "text": "Reply with exactly the single word PONG and nothing else."})
                deadline = time.monotonic() + 240
                while time.monotonic() < deadline:
                    msg = ws.receive_json()
                    types.append(msg.get("type"))
                    if msg.get("type") in ("result", "delta", "text"):
                        texts.append(str(msg.get("text") or msg.get("delta") or ""))
                    if msg.get("type") == "done":
                        break
            print("\n[live] ws event types:", types)
            self.assertIn("done", types)
            self.assertNotIn("error", types, f"turn errored: {types}")
            self.assertIn("PONG", "".join(texts).upper())

            # ── 2. loop evidence on disk ───────────────────────────────────────
            self.assertTrue(_wait(lambda: any(
                e["event_type"] == "outcome" for e in _events_on_disk(home, tenant_id)
            ), timeout=15.0), "no outcome event recorded for the finished task")
            emitter.stop(timeout=5.0)
            events = _events_on_disk(home, tenant_id)

            shadow = [
                e for e in events
                if e["event_type"] == "skill_executed"
                and e["skill_id"] == "os.delegation_router"
                and (e.get("signal") or {}).get("output", {}).get("shadow") is True
            ]
            # exactly ONE shadow record per turn (route-selection OR engine site, never both)
            self.assertEqual(len(shadow), 1, [e["event_type"] for e in events])
            self.assertIn(shadow[0]["signal"]["output"]["bundled_engine"], ("native", "acs", "tde"))
            self.assertTrue(all(e["audit_ref"] for e in shadow))

            outcomes = [e for e in events if e["event_type"] == "outcome"]
            self.assertGreaterEqual(len(outcomes), 1)
            self.assertEqual(outcomes[0]["tenant_id"], tenant_id)
            self.assertEqual(outcomes[0]["signal"]["status"], "completed")
            self.assertEqual(outcomes[0]["signal"]["engine"], "claude")  # from the task.started stamp
            task_id = outcomes[0]["signal"]["task_id"]
            self.assertTrue(task_id)
            self.assertNotIn("PONG", json.dumps(outcomes))  # content-free

            core = _chain(chain)
            self.assertGreaterEqual(sum(1 for c in core if c.get("event_type") == "learning.skill_executed"), 1)
            self.assertGreaterEqual(sum(1 for c in core if c.get("event_type") == "learning.outcome"), 1)
            refs = {(c.get("details") or {}).get("audit_ref") for c in core}
            self.assertIn(shadow[0]["audit_ref"], refs)  # the disk record joins the chain

            # ── 3. the operator closes the loop on THAT task ───────────────────
            r = client.post(
                "/v1/console/learning/feedback",
                json={"task_id": task_id, "outcome_quality": "excellent", "would_repeat": True},
            )
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual(body["recent_outcomes"]["total"], len(outcomes))
            self.assertTrue(any(h["param"] == "confidence_threshold" for h in body["hypotheses"]))
            cfg = home / "tenants" / tenant_id / "skills" / "os_delegation_router_config.json"
            self.assertTrue(cfg.is_file())
            self.assertGreaterEqual(json.loads(cfg.read_text())["optimizer"]["epoch"], 2)
            print("[live] loop closed: shadow=%d outcome=%d hypotheses=%d" % (
                len(shadow), len(outcomes), len(body["hypotheses"])))


if __name__ == "__main__":
    unittest.main()
