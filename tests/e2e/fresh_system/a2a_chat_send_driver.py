"""Inside an installed system: one REAL chat turn that asks the worker to stage an A2A message.

``python a2a_chat_send_driver.py <peer_kid> <text>`` — local-login, a chat session, a turn over the
chat WebSocket whose answer is printed verbatim, then ``GET /a2a/feed/send/pending`` so the caller
can see whether the worker's ``a2a_send`` MCP call staged a record (ADR-2099 Phase 2 two-step).
Prints ``ANSWER: …`` and ``PENDING: <json>``.
"""
import json
import sys

sys.path.insert(0, "/home/tester/CorvinOS/tests/e2e/fresh_system")
from fresh_console import answer_text, login, new_session, ws_turn  # noqa: E402

kid, text = sys.argv[1], sys.argv[2]
c, cookie = login()
sid = new_session(c, "a2a staged send e2e")
events = ws_turn(cookie, sid, text, timeout=300)
print("ANSWER:", answer_text(events).replace("\n", " ")[:600])
tools = [e for e in events if e.get("type") in ("tool", "tool_use", "tool_result")]
print("TOOL_EVENTS:", json.dumps(tools)[:800])
r = c.get("/a2a/feed/send/pending")
print("PENDING:", r.status_code, r.text[:800])
