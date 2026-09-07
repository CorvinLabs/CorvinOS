"""LIVE end-to-end proof of the console chat path with a REAL ``claude -p`` turn.

Opt-in (``CLAUDE_LIVE_E2E=1`` + the ``claude`` CLI on PATH; costs API credits):

1. sandboxed console under a temp ``CORVIN_HOME`` — the real router, a real
   session record (auth deps exercised, nothing overridden), the ACP registry
   booted — via the ``_sandbox`` of ``test_learning_loop_routes_e2e.py``;
2. the tenant is pinned to the ``claude_code`` engine with the ``haiku`` model
   through the real tenant spec (``spec.default_engine`` +
   ``spec.engine_models.claude_code.os_model``) so the turn is cheap;
3. a chat session is created over HTTP and ONE real turn is sent over the real
   WebSocket (``/chat/sessions/{sid}/stream``) — the console spawns
   ``claude -p --model haiku`` for the answer; nothing about the engine is mocked;
4. the answer is asserted (``ready`` → ``done`` without ``error``, non-empty text
   containing the requested token, and present in the session's HTTP turn
   history), and the audit trail under the temp home is asserted: the console
   chain carries ``chat.session.create`` + ``chat.ws.connected``, the core chain
   carries the finished task's ``learning.outcome`` — with no prompt text in either.

Run: CLAUDE_LIVE_E2E=1 .venv/bin/python -m pytest -q -o addopts="" \
     -p no:cacheprovider core/console/tests/test_chat_live_llm_e2e.py -s
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

from test_learning_loop_routes_e2e import _sandbox  # noqa: E402

pytestmark = pytest.mark.live

live = pytest.mark.skipif(
    os.environ.get("CLAUDE_LIVE_E2E", "") != "1" or shutil.which("claude") is None,
    reason="live chat E2E needs CLAUDE_LIVE_E2E=1 and the claude CLI",
)


def _audit_records(home: Path) -> list[dict]:
    out: list[dict] = []
    for f in home.rglob("audit.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def _mentions(rec: dict, needle: str) -> bool:
    return needle in json.dumps(rec)


@live
class ChatLiveLLME2E(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_real_haiku_turn_answers_and_is_audited(self):
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, emitter, chain):
            # ── engine pin through the real tenant spec (claude_code + haiku) ──
            spec_path = home / "tenants" / tenant_id / "global" / "tenant.corvin.yaml"
            spec_path.parent.mkdir(parents=True, exist_ok=True)
            spec_path.write_text(
                "spec:\n"
                "  default_engine: claude_code\n"
                "  engine_models:\n"
                "    claude_code:\n"
                "      os_model: haiku\n",
                encoding="utf-8",
            )

            # ── session over HTTP (CSRF-gated mutation) ────────────────────────
            r = client.post("/v1/console/chat/sessions", json={"title": "live chat e2e"})
            self.assertIn(r.status_code, (200, 201), r.text)
            body = r.json()
            sid = (body.get("session") or {}).get("sid") or body.get("sid") or body.get("session_id")
            self.assertTrue(sid, r.text)

            # ── ONE real turn over the real WebSocket ──────────────────────────
            types: list[str] = []
            texts: list[str] = []
            t0 = time.monotonic()
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
            answer = "".join(texts).strip()
            print(f"\n[live] ws types={types} answer={answer!r} in {time.monotonic() - t0:.1f}s")
            self.assertIn("done", types)
            self.assertNotIn("error", types, f"turn errored: {types}")
            self.assertTrue(answer, "empty answer from the real engine")
            self.assertIn("PONG", answer.upper())

            # ── the turn is in the session's HTTP history too ──────────────────
            r = client.get(f"/v1/console/chat/sessions/{sid}/turns")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIn("PONG", r.text.upper())

            # ── audit trail under the temp home ────────────────────────────────
            # console chain: the session + the WebSocket turn transport;
            # core chain (VOICE_AUDIT_PATH): the finished task's learning.outcome
            # (ADR-0613 sink) — proof the turn ran to completion and was recorded.
            recs = _audit_records(home)
            kinds = sorted({str(x.get("event_type") or x.get("action") or "?") for x in recs})
            print(f"[live] audit records={len(recs)} event_types={kinds}")
            self.assertTrue(any(_mentions(x, "chat.session.create") for x in recs),
                            "no chat.session.create audit record under CORVIN_HOME")
            self.assertTrue(any(_mentions(x, "chat.ws.connected") for x in recs),
                            "no chat.ws.connected audit record under CORVIN_HOME")
            core = [json.loads(l) for l in chain.read_text(encoding="utf-8").splitlines() if l.strip()]
            self.assertTrue(any(c.get("event_type") == "learning.outcome" for c in core),
                            f"no learning.outcome in the core chain: {sorted({c.get('event_type') for c in core})}")
            # audit is METADATA — the user prompt text never lands in any chain
            self.assertFalse(any(_mentions(x, "single word PONG") for x in recs + core),
                             "prompt text leaked into an audit record")


if __name__ == "__main__":
    unittest.main()
