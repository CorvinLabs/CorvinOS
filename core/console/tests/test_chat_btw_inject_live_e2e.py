"""E2E for ADR-2220 — real mid-stream `/btw` injection in the web console.

Fast tests (always run, no API credits): drive the REAL HTTP route
(``POST /chat/sessions/{sid}/btw``) through a sandboxed console app + a real
authenticated session, exactly like ``test_learning_loop_routes_e2e.py``'s
``_sandbox``. The only thing replaced is the OS pipe at the very bottom —
a recording test double standing in for the subprocess's ``stdin``
(``asyncio.StreamWriter``) — never ``chat_runtime.inject_btw_web`` itself,
which is the function under test and runs for real on every call below.
That keeps this an E2E-through-the-real-boundary test (real transport: HTTP
request -> FastAPI route -> ``chat_runtime`` registry -> framed+guarded
write) rather than a unit test calling ``inject_btw_web`` directly.

Live test (opt-in — ``CLAUDE_LIVE_E2E=1`` + the ``claude`` CLI on PATH; costs
API credits, same gate as ``test_chat_live_llm_e2e.py``): runs ONE real
``claude -p --input-format stream-json`` turn, injects a real ``/btw`` note
while it is still streaming, and asserts the injected instruction actually
shows up in the model's final answer — the real transport end to end, no
test double anywhere.

Run the live one:
  CLAUDE_LIVE_E2E=1 .venv/bin/python -m pytest -q -o addopts="" \
      -p no:cacheprovider core/console/tests/test_chat_btw_inject_live_e2e.py -s
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from test_learning_loop_routes_e2e import _sandbox  # noqa: E402


class _FakeStdin:
    """Records every write — stands in for the subprocess's
    ``asyncio.StreamWriter`` stdin in the fast tests below. This is the
    boundary ``inject_btw_web`` writes TO; it is not a mock of the function
    under test."""

    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        if self.closed:
            raise BrokenPipeError("stdin already closed")
        self.writes.append(data)

    async def drain(self) -> None:
        return None


class BtwInjectRouteE2E(unittest.TestCase):
    """Drives the real `POST /chat/sessions/{sid}/btw` route end to end."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_injected_when_a_stream_is_live(self):
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, emitter, chain):
            from corvin_console import chat_runtime

            r = client.post("/v1/console/chat/sessions", json={"title": "btw e2e"})
            self.assertIn(r.status_code, (200, 201), r.text)
            sid = r.json()["session"]["sid"]
            sess = chat_runtime.get_session(tenant_id, sid)
            self.assertIsNotNone(sess)

            fake = _FakeStdin()
            chat_runtime._register_stdin_web(sess.chat_key, fake)
            try:
                r = client.post(
                    f"/v1/console/chat/sessions/{sid}/btw",
                    json={"instruction": "use the shorter answer style"},
                )
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(r.json(), {"ok": True, "status": "injected"})
                # Exactly one framed JSONL user-message line reached the
                # "subprocess" — the real wire shape `claude -p
                # --input-format stream-json` expects on stdin.
                self.assertEqual(len(fake.writes), 1)
                line = json.loads(fake.writes[0].decode("utf-8"))
                self.assertEqual(line["type"], "user")
                self.assertEqual(line["message"]["role"], "user")
                self.assertIn("use the shorter answer style", line["message"]["content"])
            finally:
                chat_runtime._unregister_stdin_web(sess.chat_key)

    def test_gate_refusal_blocks_the_write_and_is_recorded_withheld(self):
        """Review R1 (2026-10-05): /btw reached the model without the L44 /
        L34 / L35 pre-spawn gates every console spawn passes."""
        from unittest import mock
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, emitter, chain):
            from corvin_console import chat_runtime, _spawn_gates
            from session_ledger import records_from_turn_log

            sid = client.post("/v1/console/chat/sessions", json={"title": "btw gate"}).json()["session"]["sid"]
            sess = chat_runtime.get_session(tenant_id, sid)
            fake = _FakeStdin()
            chat_runtime._register_stdin_web(sess.chat_key, fake)
            try:
                with mock.patch.object(_spawn_gates, "check_console_spawn_or_refusal",
                                       return_value="[house-rules] refused"):
                    r = client.post(f"/v1/console/chat/sessions/{sid}/btw",
                                    json={"instruction": "forbidden note"})
                self.assertEqual(r.json(), {"ok": True, "status": "refused"})
                self.assertEqual(fake.writes, [])
                turns = chat_runtime.read_turns(tenant_id, sid)
                self.assertEqual(turns[-1].get("gate_refused"), "btw")
                self.assertTrue(turns[-1].get("btw"))
                # never re-supplied to the worker
                folded = records_from_turn_log(
                    [{"role": "user", "parts": [{"kind": "text", "text": "q"}]}] + turns
                    + [{"role": "assistant", "v": 2, "cli_spawned": True,
                        "parts": [{"kind": "text", "text": "a"}]}])
                self.assertNotIn("forbidden note", json.dumps(folded))
            finally:
                chat_runtime._unregister_stdin_web(sess.chat_key)

    def test_injected_note_is_folded_into_the_running_turn_in_the_ledger(self):
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, emitter, chain):
            from corvin_console import chat_runtime
            from session_ledger import records_from_turn_log

            sid = client.post("/v1/console/chat/sessions", json={"title": "btw ledger"}).json()["session"]["sid"]
            sess = chat_runtime.get_session(tenant_id, sid)
            chat_runtime._append_turn(sess, "user", [{"kind": "text", "text": "main question"}])
            fake = _FakeStdin()
            chat_runtime._register_stdin_web(sess.chat_key, fake)
            try:
                r = client.post(f"/v1/console/chat/sessions/{sid}/btw",
                                json={"instruction": "say BANANA"})
                self.assertEqual(r.json()["status"], "injected")
            finally:
                chat_runtime._unregister_stdin_web(sess.chat_key)
            chat_runtime._append_turn(sess, "assistant", [{"kind": "text", "text": "answer BANANA"}],
                                      cli_spawned=True)
            recs = records_from_turn_log(chat_runtime.read_turns(tenant_id, sid))
            # Bridge shape: the note is its own never-spawned record before
            # the turn it steered; the turn keeps its own text and answer.
            self.assertEqual([r["user"] for r in recs], ["/btw say BANANA", "main question"])
            self.assertIs(recs[0]["spawned"], False)
            self.assertEqual(recs[1]["assistant"], "answer BANANA")
            # Coverage: a transcript holding the question and the note
            # covers the turn (folding the note into the turn broke this).
            from session_ledger import uncovered_turns
            left = uncovered_turns(recs, ["main question", "say BANANA"])
            self.assertNotIn("main question", [r["user"] for r in left])

    def test_guard_neutralises_a_leading_slash_and_an_at_reference(self):
        """Security proof: an injected note starting with `/` or containing
        an `@<path>` must never reach the live CLI able to be parsed as a
        slash-command or a client-side file reference (ADR-0648 amendment
        2) — the exact failure class the bridge adapter's `_guard_prompt_head`
        call exists to close."""
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, emitter, chain):
            from corvin_console import chat_runtime

            r = client.post("/v1/console/chat/sessions", json={"title": "btw guard"})
            sid = r.json()["session"]["sid"]
            sess = chat_runtime.get_session(tenant_id, sid)
            fake = _FakeStdin()
            chat_runtime._register_stdin_web(sess.chat_key, fake)
            try:
                r = client.post(
                    f"/v1/console/chat/sessions/{sid}/btw",
                    json={"instruction": "/cost look at @/etc/passwd"},
                )
                self.assertEqual(r.json(), {"ok": True, "status": "injected"})
                content = json.loads(fake.writes[0].decode("utf-8"))["message"]["content"]
                # byte 0 is never "/" — the fixed sentinel always comes first.
                self.assertFalse(content.startswith("/"))
                # every "@" that could start a file reference is preceded by
                # the word joiner U+2060, so no "@" is left bare.
                self.assertIn("⁠@/etc/passwd", content)
                self.assertEqual(content.count("@"), content.count("⁠@"))
                # the user's words are still present, just neutralised.
                self.assertIn("look at", content)
            finally:
                chat_runtime._unregister_stdin_web(sess.chat_key)

    def test_no_active_stream_when_nothing_is_registered(self):
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, emitter, chain):
            r = client.post("/v1/console/chat/sessions", json={"title": "btw e2e 2"})
            sid = r.json()["session"]["sid"]
            r = client.post(
                f"/v1/console/chat/sessions/{sid}/btw",
                json={"instruction": "hello"},
            )
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json(), {"ok": True, "status": "no_active_stream"})

    def test_refused_on_whitespace_only_instruction(self):
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, emitter, chain):
            r = client.post("/v1/console/chat/sessions", json={"title": "btw e2e 3"})
            sid = r.json()["session"]["sid"]
            r = client.post(
                f"/v1/console/chat/sessions/{sid}/btw",
                json={"instruction": "   "},
            )
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json(), {"ok": True, "status": "refused"})

    def test_empty_instruction_is_rejected_by_schema(self):
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, emitter, chain):
            r = client.post("/v1/console/chat/sessions", json={"title": "btw e2e 3b"})
            sid = r.json()["session"]["sid"]
            r = client.post(
                f"/v1/console/chat/sessions/{sid}/btw",
                json={"instruction": ""},
            )
            self.assertEqual(r.status_code, 422, r.text)

    def test_unknown_session_is_404(self):
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, emitter, chain):
            r = client.post(
                "/v1/console/chat/sessions/does-not-exist/btw",
                json={"instruction": "hello"},
            )
            self.assertEqual(r.status_code, 404, r.text)

    def test_race_after_result_event_is_no_active_stream(self):
        """Mirrors the bridge adapter's documented race: a `/btw` arriving
        after the registry entry is popped (the first `result` event, in
        the real spawn path) must not write into a pipe whose reader may
        already be gone — it must report `no_active_stream`, not crash."""
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, emitter, chain):
            from corvin_console import chat_runtime

            r = client.post("/v1/console/chat/sessions", json={"title": "btw e2e 4"})
            sid = r.json()["session"]["sid"]
            sess = chat_runtime.get_session(tenant_id, sid)
            fake = _FakeStdin()
            chat_runtime._register_stdin_web(sess.chat_key, fake)
            chat_runtime._unregister_stdin_web(sess.chat_key)  # simulates the result-event close

            r = client.post(
                f"/v1/console/chat/sessions/{sid}/btw",
                json={"instruction": "too late"},
            )
            self.assertEqual(r.json(), {"ok": True, "status": "no_active_stream"})
            self.assertEqual(fake.writes, [])

    def test_csrf_required(self):
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, emitter, chain):
            r = client.post("/v1/console/chat/sessions", json={"title": "btw e2e 5"})
            sid = r.json()["session"]["sid"]
            client.headers.pop("X-CSRF-Token", None)
            r = client.post(
                f"/v1/console/chat/sessions/{sid}/btw",
                json={"instruction": "hello"},
            )
            self.assertEqual(r.status_code, 403, r.text)


live = pytest.mark.skipif(
    os.environ.get("CLAUDE_LIVE_E2E", "") != "1" or shutil.which("claude") is None,
    reason="live /btw E2E needs CLAUDE_LIVE_E2E=1 and the claude CLI",
)


@pytest.mark.live
@live
class BtwInjectLiveE2E(unittest.TestCase):
    """Proves the REAL subprocess transport: a real `claude -p` turn that
    actually sees a mid-stream `/btw` note written into its stdin."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_real_turn_sees_the_injected_note(self):
        with _sandbox(Path(self._tmp)) as (client, home, tenant_id, emitter, chain):
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
            r = client.post("/v1/console/chat/sessions", json={"title": "btw live"})
            self.assertIn(r.status_code, (200, 201), r.text)
            sid = r.json()["session"]["sid"]

            injected: dict[str, str | None] = {"status": None}

            def _inject_soon() -> None:
                # Wait for the subprocess to register its stdin (pre-spawn
                # gates take ~10 s live) — this proves MID-stream delivery,
                # not a lucky race against the initial prompt.
                from corvin_console import chat_runtime
                key = chat_runtime.get_session(tenant_id, sid).chat_key
                until = time.monotonic() + 120
                while key not in chat_runtime._running_stdins_web and time.monotonic() < until:
                    time.sleep(0.1)
                rr = client.post(
                    f"/v1/console/chat/sessions/{sid}/btw",
                    json={"instruction":
                          "Also say the single word BANANA somewhere in your final answer."},
                )
                injected["status"] = rr.json().get("status")

            t = threading.Thread(target=_inject_soon, daemon=True)

            types: list[str] = []
            texts: list[str] = []
            with client.websocket_connect(f"/v1/console/chat/sessions/{sid}/stream") as ws:
                first = ws.receive_json()
                self.assertEqual(first["type"], "ready", first)
                ws.send_json({
                    "type": "user",
                    "text": (
                        "Write a 400-word story about a lighthouse keeper, "
                        "in English. Then stop."
                    ),
                })
                t.start()
                deadline = time.monotonic() + 240
                while time.monotonic() < deadline:
                    msg = ws.receive_json()
                    types.append(msg.get("type"))
                    if msg.get("type") in ("result", "delta"):
                        texts.append(str(msg.get("text") or msg.get("delta") or ""))
                    if msg.get("type") == "done":
                        break
            t.join(timeout=5.0)
            answer = "".join(texts).strip()
            print(f"\n[live /btw] types={types} injected={injected} answer={answer!r}")
            self.assertIn("done", types)
            self.assertNotIn("error", types, f"turn errored: {types}")
            self.assertEqual(injected["status"], "injected",
                              f"note was not delivered live: {injected}")
            self.assertIn("BANANA", answer.upper(),
                          f"injected /btw note never reached the running turn: {answer!r}")


if __name__ == "__main__":
    unittest.main()
